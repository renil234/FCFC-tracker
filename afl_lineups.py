from __future__ import annotations

import html as html_lib
import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

PERTH_TZ = timezone(timedelta(hours=8))
AFL_V2 = "https://aflapi.afl.com.au/afl/v2"
AFL_TEAM_LINEUPS_PAGE = "https://www.afl.com.au/matches/team-lineups"
FOOTYWIRE_TEAM_SELECTIONS = "https://www.footywire.com/afl/footy/afl_team_selections"
FOOTYWIRE_READER_URL = "https://r.jina.ai/http://www.footywire.com/afl/footy/afl_team_selections"
AFL_TOKEN_URLS = [
    "https://api.afl.com.au/cfs/afl/WMCTok",
    "https://www.afl.com.au/api/cfs/afl/WMCTok",
]
AFL_ROSTER_URLS = [
    "https://api.afl.com.au/cfs/afl/matchRoster/full/{match_id}",
    "https://www.afl.com.au/api/cfs/afl/matchRoster/full/{match_id}",
]

STATUS_LABELS = {
    "confirmed": "Playing — confirmed",
    "provisional": "Provisional — extended squad",
    "emergency": "Emergency",
    "not_selected": "Not selected",
    "bye": "Bye",
    "not_announced": "Team not announced",
    "late_out": "Late out",
    "check_failed": "Line-up check failed",
}

# Official AFL API abbreviations are not always the same as the workbook codes.
AFL_CODE_TO_FCFC = {
    "ADEL": "ADE", "ADE": "ADE",
    "BL": "BRL", "BRIS": "BRL", "BRL": "BRL",
    "CARL": "CAR", "CAR": "CAR",
    "COLL": "COL", "COL": "COL",
    "ESS": "ESS",
    "FRE": "FRE",
    "GCFC": "GCS", "GCS": "GCS", "GC": "GCS",
    "GEEL": "GEE", "GEE": "GEE",
    "GWS": "GWS",
    "HAW": "HAW",
    "MELB": "MEL", "MEL": "MEL",
    "NMFC": "NM", "NM": "NM",
    "PORT": "POR", "PA": "POR", "POR": "POR",
    "RICH": "RIC", "RIC": "RIC",
    "STK": "STK",
    "SYD": "SYD",
    "WB": "WBD", "WBD": "WBD",
    "WCE": "WCE",
}

TEAM_NAME_TO_FCFC = {
    "adelaide": "ADE", "adelaide crows": "ADE", "crows": "ADE",
    "brisbane": "BRL", "brisbane lions": "BRL", "lions": "BRL",
    "carlton": "CAR", "blues": "CAR",
    "collingwood": "COL", "magpies": "COL",
    "essendon": "ESS", "bombers": "ESS",
    "fremantle": "FRE", "dockers": "FRE",
    "gold coast": "GCS", "gold coast suns": "GCS", "suns": "GCS",
    "geelong": "GEE", "geelong cats": "GEE", "cats": "GEE",
    "greater western sydney": "GWS", "gws": "GWS", "gws giants": "GWS", "giants": "GWS",
    "hawthorn": "HAW", "hawks": "HAW",
    "melbourne": "MEL", "demons": "MEL",
    "north melbourne": "NM", "kangaroos": "NM",
    "port adelaide": "POR", "power": "POR",
    "richmond": "RIC", "tigers": "RIC",
    "st kilda": "STK", "saints": "STK",
    "sydney": "SYD", "sydney swans": "SYD", "swans": "SYD",
    "western bulldogs": "WBD", "bulldogs": "WBD",
    "west coast": "WCE", "west coast eagles": "WCE", "eagles": "WCE",
}

SELECTED_GROUPS = {
    "back", "backs", "fullback", "fullbacks", "halfback", "halfbacks",
    "centre", "centres", "center", "centers", "wing", "wings",
    "halfforward", "halfforwards", "forward", "forwards", "fullforward", "fullforwards",
    "follower", "followers", "ruck", "rucks", "interchange", "interchanges", "bench",
    "selected", "selectedplayers", "lineup", "lineups", "players", "squad",
}
EMERGENCY_GROUPS = {"emergency", "emergencies", "emg"}
IGNORE_GROUPS = {
    "ins", "outs", "milestones", "clubdebuts", "team", "coach", "coaches",
    "captain", "vicecaptain", "name", "id", "providerid", "abbreviation", "nickname",
}


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def canonical_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _nested_get(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        if path in data:
            return data[path]
        current: Any = data
        ok = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                ok = False
                break
            current = current[part]
        if ok:
            return current
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 12,
    attempts: int = 2,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.request(method, url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"AFL API returned {type(payload).__name__}, not an object")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"AFL API request failed for {url}: {last_error}") from last_error


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def team_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    upper = re.sub(r"[^A-Z0-9]", "", text.upper())
    if upper in AFL_CODE_TO_FCFC:
        return AFL_CODE_TO_FCFC[upper]
    key = re.sub(r"\s+", " ", text.lower()).strip()
    return TEAM_NAME_TO_FCFC.get(key, "")


def parse_match(match: dict[str, Any]) -> dict[str, Any] | None:
    provider_id = _nested_get(match, "providerId", "providerID", "id")
    start_raw = _nested_get(match, "utcStartTime", "startTime", "date")
    start = _parse_utc(start_raw)
    home = team_code(_nested_get(
        match,
        "home.team.abbreviation", "homeTeam.abbreviation", "home.team.name", "homeTeam.name",
        "home.abbreviation", "home.name", "homeTeam", "home",
    ))
    away = team_code(_nested_get(
        match,
        "away.team.abbreviation", "awayTeam.abbreviation", "away.team.name", "awayTeam.name",
        "away.abbreviation", "away.name", "awayTeam", "away",
    ))
    if not provider_id or not start or not home or not away:
        return None
    round_number = _nested_get(match, "round.roundNumber", "roundNumber")
    round_name = _nested_get(match, "round.name", "roundName") or (f"Round {round_number}" if round_number is not None else "")
    return {
        "match_id": str(provider_id),
        "start_utc": start.isoformat(),
        "round_number": round_number,
        "round_name": str(round_name or ""),
        "status": str(_nested_get(match, "status", "match.status") or ""),
        "home": home,
        "away": away,
    }


def choose_upcoming_round(matches: Iterable[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    parsed = [record for item in matches if (record := parse_match(item))]
    future = []
    for record in parsed:
        start = _parse_utc(record["start_utc"])
        status = record.get("status", "").lower()
        if start and start >= now - timedelta(hours=6) and status not in {"completed", "complete", "concluded", "cancelled"}:
            future.append(record)
    if not future:
        return []
    future.sort(key=lambda item: item["start_utc"])
    first = future[0]
    round_number = first.get("round_number")
    round_name = first.get("round_name")
    if round_number not in (None, ""):
        selected = [item for item in future if item.get("round_number") == round_number]
    else:
        selected = [item for item in future if item.get("round_name") == round_name]
    return sorted(selected, key=lambda item: item["start_utc"])


def _player_name_from_dict(item: dict[str, Any]) -> str:
    for nested_key in ("player", "person", "playerName"):
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            name = _player_name_from_dict(nested)
            if name:
                return name
        elif isinstance(nested, str) and nested.strip():
            return nested.strip()
    for key in ("fullName", "displayName", "name", "player.name", "person.name"):
        value = _nested_get(item, key)
        if isinstance(value, str) and " " in value.strip():
            return value.strip()
    first = _nested_get(item, "firstName", "givenName", "player.firstName", "person.firstName", "playerName.givenName")
    last = _nested_get(item, "surname", "lastName", "familyName", "player.surname", "person.surname", "playerName.surname")
    if first and last:
        return f"{str(first).strip()} {str(last).strip()}".strip()
    return ""


def parse_roster_side(side: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(side, dict):
        return {"selected": [], "emergencies": [], "provisional": False, "raw_selected_count": 0}

    selected: dict[str, str] = {}
    emergencies: dict[str, str] = {}
    explicit_provisional = False

    def visit(node: Any, category: str = "") -> None:
        nonlocal explicit_provisional
        if isinstance(node, list):
            for item in node:
                visit(item, category)
            return
        if not isinstance(node, dict):
            return

        category_key = _normalise_key(category)
        if "extended" in category_key or "provisional" in category_key:
            explicit_provisional = True

        name = _player_name_from_dict(node)
        if name and category_key:
            if category_key in EMERGENCY_GROUPS or "emerg" in category_key:
                emergencies[canonical_name(name)] = name
                return
            if category_key in SELECTED_GROUPS or any(token in category_key for token in ("back", "forward", "centre", "center", "follow", "interchange", "bench", "selected", "lineup", "squad")):
                selected[canonical_name(name)] = name
                return

        for key, value in node.items():
            key_norm = _normalise_key(key)
            if key_norm in IGNORE_GROUPS:
                continue
            next_category = category
            if key_norm in EMERGENCY_GROUPS or "emerg" in key_norm:
                next_category = "emergencies"
            elif key_norm in SELECTED_GROUPS or any(token in key_norm for token in ("back", "forward", "centre", "center", "follow", "interchange", "bench", "selected", "lineup", "squad", "extended")):
                next_category = key_norm
            visit(value, next_category)

    visit(side)
    selected_names = sorted(selected.values())
    emergency_names = sorted(emergencies.values())
    provisional = explicit_provisional or len(selected_names) > 23
    return {
        "selected": selected_names,
        "emergencies": emergency_names,
        "provisional": provisional,
        "raw_selected_count": len(selected_names),
    }


def parse_match_roster(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse the official roster response across the structures used by AFL.com.au.

    AFL has wrapped the roster under different keys over time. Search recursively for
    the first object containing recognisable home and away roster branches rather
    than assuming one fixed response shape.
    """
    candidates: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            candidates.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(payload)
    best: dict[str, dict[str, Any]] | None = None
    best_count = -1
    for root in candidates:
        home = _nested_get(root, "homeTeam", "home", "teams.home", "roster.homeTeam", "roster.home")
        away = _nested_get(root, "awayTeam", "away", "teams.away", "roster.awayTeam", "roster.away")
        if not isinstance(home, (dict, list)) or not isinstance(away, (dict, list)):
            continue
        home_side = parse_roster_side(home if isinstance(home, dict) else {"players": home})
        away_side = parse_roster_side(away if isinstance(away, dict) else {"players": away})
        count = home_side["raw_selected_count"] + away_side["raw_selected_count"] + len(home_side["emergencies"]) + len(away_side["emergencies"])
        if count > best_count:
            best_count = count
            best = {"home": home_side, "away": away_side}
    return best or {"home": parse_roster_side({}), "away": parse_roster_side({})}



def _json_candidates_from_html(page_html: str) -> list[Any]:
    """Extract embedded JSON payloads from the AFL team-lineups page.

    AFL.com.au has used both application/json script tags and JavaScript assignments.
    This deliberately supports both without depending on CSS class names.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[Any] = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=False)
        if not raw:
            continue
        text = html_lib.unescape(raw.strip())
        attempts: list[str] = []
        if text.startswith(("{", "[")):
            attempts.append(text)
        for pattern in (
            r"__NEXT_DATA__\s*=\s*({.*})\s*;?\s*$",
            r"window\.__INITIAL_STATE__\s*=\s*({.*})\s*;?\s*$",
            r"window\.__APOLLO_STATE__\s*=\s*({.*})\s*;?\s*$",
        ):
            match = re.search(pattern, text, flags=re.DOTALL)
            if match:
                attempts.append(match.group(1))
        for encoded in re.findall(r"JSON\.parse\(\s*(['\"])(.*?)\1\s*\)", text, flags=re.DOTALL):
            try:
                attempts.append(bytes(encoded[1], "utf-8").decode("unicode_escape"))
            except Exception:
                pass
        for candidate in attempts:
            try:
                candidates.append(json.loads(candidate))
            except Exception:
                continue
    return candidates


def _team_identity(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    direct_keys = ("abbreviation", "teamAbbreviation", "clubAbbreviation", "shortName", "teamName", "clubName", "name")
    for key in direct_keys:
        code = team_code(node.get(key))
        if code:
            return code
    for key in ("team", "club", "teamDetails", "clubDetails"):
        nested = node.get(key)
        if isinstance(nested, dict):
            code = _team_identity(nested)
            if code:
                return code
        elif isinstance(nested, str):
            code = team_code(nested)
            if code:
                return code
    return ""


def parse_team_lineups_page(page_html: str) -> dict[str, dict[str, Any]]:
    """Return the latest published team list keyed by FCFC club code."""
    found: dict[str, dict[str, Any]] = {}

    def store(club: str, node: Any) -> None:
        if not club:
            return
        side = parse_roster_side(node if isinstance(node, dict) else {"players": node})
        count = int(side.get("raw_selected_count", 0)) + len(side.get("emergencies", []))
        existing = found.get(club)
        existing_count = 0 if not existing else int(existing.get("raw_selected_count", 0)) + len(existing.get("emergencies", []))
        if count > existing_count:
            found[club] = side

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return

        # Common match wrappers: preserve the club identity from each side.
        for key in ("homeTeam", "home", "awayTeam", "away"):
            side_node = node.get(key)
            if isinstance(side_node, dict):
                club = _team_identity(side_node)
                store(club, side_node)

        club = _team_identity(node)
        if club:
            store(club, node)

        for value in node.values():
            visit(value)

    for payload in _json_candidates_from_html(page_html):
        visit(payload)
    return found



FOOTYWIRE_CLUB_SLUGS = {
    "adelaide-crows": "ADE", "brisbane-lions": "BRL", "carlton-blues": "CAR",
    "collingwood-magpies": "COL", "essendon-bombers": "ESS", "fremantle-dockers": "FRE",
    "geelong-cats": "GEE", "gold-coast-suns": "GCS",
    "greater-western-sydney-giants": "GWS", "gws-giants": "GWS",
    "hawthorn-hawks": "HAW", "melbourne-demons": "MEL",
    "north-melbourne-kangaroos": "NM", "port-adelaide-power": "POR",
    "richmond-tigers": "RIC", "st-kilda-saints": "STK", "sydney-swans": "SYD",
    "west-coast-eagles": "WCE", "western-bulldogs": "WBD",
}


def _footywire_player_from_href(href: str) -> tuple[str, str]:
    match = re.search(r"/pp-([^?]+?)--([^/?#]+)", str(href or ""), flags=re.I)
    if not match:
        return "", ""
    club_slug = match.group(1).lower().strip("-")
    name_slug = match.group(2).lower().strip("-")
    club = FOOTYWIRE_CLUB_SLUGS.get(club_slug, "")
    if not club:
        # Tolerate minor changes in FootyWire's club slug wording.
        for slug, code in FOOTYWIRE_CLUB_SLUGS.items():
            if club_slug == slug or club_slug.endswith(slug) or slug.endswith(club_slug):
                club = code
                break
    name = " ".join(part.capitalize() for part in name_slug.split("-") if part)
    return club, name


def parse_footywire_team_selections(page_html: str) -> tuple[int | None, dict[str, dict[str, Any]]]:
    """Parse the latest FootyWire team-selection page.

    Player profile links contain both the club and full player name, allowing the
    parser to avoid fragile initials-only matching. Field rows and interchange are
    selected; emergencies are retained separately; ins and outs are ignored.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    round_match = re.search(r"AFL\s+\d{4}\s+Round\s+(\d+)\s+Team Selections", page_text, re.I)
    round_number = int(round_match.group(1)) if round_match else None

    selected: dict[str, dict[str, str]] = {}
    emergencies: dict[str, dict[str, str]] = {}
    current_category = ""
    position_tokens = {"fb", "hb", "c", "hf", "ff", "fol", "followers", "foll"}

    # The page is table-based. Reading rows in document order preserves section
    # headings such as Interchange, Emergencies, Ins and Outs.
    rows = soup.find_all("tr")
    if not rows:
        rows = [soup]
    for row in rows:
        text = re.sub(r"\s+", " ", row.get_text(" ", strip=True)).strip()
        lowered = text.lower()
        anchors = [a for a in row.find_all("a", href=True) if "/pp-" in str(a.get("href", ""))]

        if re.search(r"\bemergenc(?:y|ies)\b", lowered):
            current_category = "emergencies"
        elif re.search(r"\bouts?\b", lowered):
            current_category = "outs"
        elif re.search(r"\bins?\b", lowered):
            current_category = "ins"
        elif "interchange" in lowered or re.search(r"\bbench\b", lowered):
            current_category = "interchange"

        first_token_match = re.match(r"^\s*([A-Za-z]+)", text)
        first_token = first_token_match.group(1).lower() if first_token_match else ""
        row_is_field = first_token in position_tokens

        for anchor in anchors:
            club, full_name = _footywire_player_from_href(str(anchor.get("href", "")))
            if not club or not full_name:
                continue
            key = canonical_name(full_name)
            if row_is_field or current_category == "interchange":
                selected.setdefault(club, {})[key] = full_name
            elif current_category == "emergencies":
                emergencies.setdefault(club, {})[key] = full_name
            # Ins and outs deliberately do not affect selected status.

        # Position rows should not permanently reset section state, but matchup
        # headings and unrelated rows must not leak an old Ins/Outs category.
        if not anchors and re.search(r"\bv\b.*\(", text) and current_category in {"ins", "outs", "emergencies"}:
            current_category = ""

    clubs: dict[str, dict[str, Any]] = {}
    for club in set(selected) | set(emergencies):
        selected_names = sorted(selected.get(club, {}).values())
        emergency_names = sorted(emergencies.get(club, {}).values())
        clubs[club] = {
            "selected": selected_names,
            "emergencies": emergency_names,
            "provisional": len(selected_names) > 23,
            "raw_selected_count": len(selected_names),
        }
    return round_number, clubs


def parse_footywire_reader_text(reader_text: str) -> tuple[int | None, dict[str, dict[str, Any]]]:
    """Parse Jina Reader markdown for the FootyWire selections page.

    Reader responses retain the FootyWire player-profile URLs but return markdown
    rather than the original table HTML. Convert each markdown line into a
    synthetic table row, preserving section labels and player links, then reuse
    the normal FootyWire parser.
    """
    text = str(reader_text or "")
    if not text.strip():
        return None, {}

    def link_to_anchor(match: re.Match[str]) -> str:
        label = html_lib.escape(match.group(1).strip())
        href = html_lib.escape(match.group(2).strip(), quote=True)
        return f'<a href="{href}">{label}</a>'

    rows: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", link_to_anchor, line)
        rows.append(f"<tr><td>{line}</td></tr>")
    synthetic_html = "<table>" + "".join(rows) + "</table>"
    return parse_footywire_team_selections(synthetic_html)

def build_player_statuses_from_clubs(
    roster: list[dict[str, str]],
    fixtures: list[dict[str, Any]],
    club_rosters: dict[str, dict[str, Any]],
    checked_at: str,
    previous_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Apply page-published team lists to the FCFC squad."""
    previous_statuses = previous_statuses or {}
    fixture_by_club: dict[str, dict[str, Any]] = {}
    for fixture in fixtures:
        fixture_by_club[str(fixture.get("home") or "")] = fixture
        fixture_by_club[str(fixture.get("away") or "")] = fixture

    statuses: dict[str, dict[str, Any]] = {}
    for player in roster:
        name, club = player["player"], player["club"]
        fixture = fixture_by_club.get(club, {})
        opponent = ""
        if fixture:
            opponent = fixture.get("away") if fixture.get("home") == club else fixture.get("home")
        base = {
            "player": name, "club": club, "status": "bye" if not fixture else "not_announced",
            "status_label": STATUS_LABELS["bye" if not fixture else "not_announced"],
            "opponent": opponent or "", "match_id": str(fixture.get("match_id") or ""),
            "round": str(fixture.get("round_name") or ""), "start_utc": str(fixture.get("start_utc") or ""),
            "checked_at": checked_at, "matched_name": "", "selected_count": 0,
        }
        side = club_rosters.get(club)
        if not side:
            statuses[name] = base
            continue
        selected_names = list(side.get("selected") or [])
        emergency_names = list(side.get("emergencies") or [])
        base["selected_count"] = int(side.get("raw_selected_count", len(selected_names)))
        selected_map = {canonical_name(value): value for value in selected_names}
        emergency_map = {canonical_name(value): value for value in emergency_names}
        matched_selected = _match_player(selected_map, name)
        matched_emergency = _match_player(emergency_map, name)
        if matched_selected:
            status = "provisional" if side.get("provisional") else "confirmed"
            base.update({"status": status, "status_label": STATUS_LABELS[status], "matched_name": matched_selected})
        elif matched_emergency:
            base.update({"status": "emergency", "status_label": STATUS_LABELS["emergency"], "matched_name": matched_emergency})
        else:
            previous = previous_statuses.get(name, {})
            same_match = str(previous.get("match_id", "")) == str(base["match_id"])
            previously_selected = previous.get("status") in {"confirmed", "provisional"}
            status = "late_out" if same_match and previously_selected else "not_selected"
            base.update({"status": status, "status_label": STATUS_LABELS[status]})
        statuses[name] = base
    return statuses


def _match_player(roster_name_map: dict[str, str], player_name: str) -> str | None:
    target = canonical_name(player_name)
    if target in roster_name_map:
        return roster_name_map[target]
    # Conservative fallback: same surname and first initial, unique within the club side.
    parts = re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKD", player_name))
    if len(parts) < 2:
        return None
    first_initial = parts[0][0].lower()
    surname = canonical_name(parts[-1])
    matches: list[str] = []
    for canonical, original in roster_name_map.items():
        original_parts = re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKD", original))
        if len(original_parts) >= 2 and original_parts[0][0].lower() == first_initial and canonical_name(original_parts[-1]) == surname:
            matches.append(original)
    return matches[0] if len(matches) == 1 else None


def build_player_statuses(
    roster: list[dict[str, str]],
    fixtures: list[dict[str, Any]],
    match_rosters: dict[str, dict[str, dict[str, Any]]],
    checked_at: str,
    previous_statuses: dict[str, dict[str, Any]] | None = None,
    failed_match_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    previous_statuses = previous_statuses or {}
    failed_match_ids = failed_match_ids or set()
    by_club: dict[str, tuple[dict[str, Any], str]] = {}
    for fixture in fixtures:
        by_club[fixture["home"]] = (fixture, "home")
        by_club[fixture["away"]] = (fixture, "away")

    statuses: dict[str, dict[str, Any]] = {}
    for player in roster:
        name = player["player"]
        club = player["club"]
        base = {
            "player": name,
            "club": club,
            "status": "bye",
            "status_label": STATUS_LABELS["bye"],
            "opponent": "",
            "match_id": "",
            "round": "",
            "start_utc": "",
            "checked_at": checked_at,
            "matched_name": "",
            "selected_count": 0,
        }
        if club not in by_club:
            statuses[name] = base
            continue

        fixture, side_key = by_club[club]
        opponent = fixture["away"] if side_key == "home" else fixture["home"]
        base.update({
            "opponent": opponent,
            "match_id": fixture["match_id"],
            "round": fixture.get("round_name", ""),
            "start_utc": fixture.get("start_utc", ""),
        })
        if fixture["match_id"] in failed_match_ids:
            base["status"] = "check_failed"
            base["status_label"] = STATUS_LABELS["check_failed"]
            statuses[name] = base
            continue

        match_payload = match_rosters.get(fixture["match_id"], {})
        side = match_payload.get(side_key, {}) if isinstance(match_payload, dict) else {}
        selected_names = side.get("selected", []) if isinstance(side, dict) else []
        emergency_names = side.get("emergencies", []) if isinstance(side, dict) else []
        base["selected_count"] = int(side.get("raw_selected_count", len(selected_names))) if isinstance(side, dict) else 0
        if not selected_names and not emergency_names:
            base["status"] = "not_announced"
            base["status_label"] = STATUS_LABELS["not_announced"]
            statuses[name] = base
            continue

        selected_map = {canonical_name(value): value for value in selected_names}
        emergency_map = {canonical_name(value): value for value in emergency_names}
        matched_selected = _match_player(selected_map, name)
        matched_emergency = _match_player(emergency_map, name)
        if matched_selected:
            status = "provisional" if side.get("provisional") else "confirmed"
            base.update({"status": status, "status_label": STATUS_LABELS[status], "matched_name": matched_selected})
        elif matched_emergency:
            base.update({"status": "emergency", "status_label": STATUS_LABELS["emergency"], "matched_name": matched_emergency})
        else:
            previous = previous_statuses.get(name, {})
            same_match = str(previous.get("match_id", "")) == str(fixture["match_id"])
            previously_selected = previous.get("status") in {"confirmed", "provisional"}
            status = "late_out" if same_match and previously_selected else "not_selected"
            base.update({"status": status, "status_label": STATUS_LABELS[status]})
        statuses[name] = base
    return statuses


def eligible_players(statuses: dict[str, dict[str, Any]], include_provisional: bool = False) -> set[str]:
    allowed = {"confirmed"}
    if include_provisional:
        allowed.add("provisional")
    return {name for name, record in statuses.items() if record.get("status") in allowed}


def opponents_from_fixtures(fixtures: Iterable[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for fixture in fixtures:
        home = str(fixture.get("home") or "")
        away = str(fixture.get("away") or "")
        if home and away:
            result[home] = away
            result[away] = home
    return result


def lineup_check_due(last_checked: str | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(PERTH_TZ)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    thursday_release = week_start + timedelta(days=3, hours=16, minutes=25)
    if now < thursday_release:
        return False
    if not last_checked:
        return True
    try:
        checked = datetime.fromisoformat(last_checked)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=PERTH_TZ)
        return checked.astimezone(PERTH_TZ) < thursday_release
    except ValueError:
        return True


class AFLLineupClient:
    def __init__(self, timeout: int = 12):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; FCFC-Squad-Tracker/2.0; personal weekly team check)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-AU,en;q=0.9",
        })

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return _request_json(self.session, "GET", url, params=params, timeout=self.timeout)

    def team_lineups_page(self) -> dict[str, dict[str, Any]]:
        response = self.session.get(
            AFL_TEAM_LINEUPS_PAGE,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.afl.com.au/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return parse_team_lineups_page(response.text)

    def footywire_team_selections(self) -> tuple[int | None, dict[str, dict[str, Any]]]:
        direct_error: Exception | None = None
        try:
            response = self.session.get(
                FOOTYWIRE_TEAM_SELECTIONS,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://www.footywire.com/",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            round_number, clubs = parse_footywire_team_selections(response.text)
            if clubs:
                return round_number, clubs
            direct_error = RuntimeError("direct FootyWire response contained no player lists")
        except Exception as exc:
            direct_error = exc

        try:
            reader = self.session.get(
                FOOTYWIRE_READER_URL,
                headers={
                    "Accept": "text/plain,text/markdown,*/*",
                    "Referer": "https://r.jina.ai/",
                },
                timeout=max(self.timeout, 20),
            )
            reader.raise_for_status()
            round_number, clubs = parse_footywire_reader_text(reader.text)
            if clubs:
                return round_number, clubs
            raise RuntimeError("reader response contained no player lists")
        except Exception as reader_error:
            raise RuntimeError(
                f"FootyWire direct request failed: {direct_error} | "
                f"reader fallback failed: {reader_error}"
            ) from reader_error

    def competition_id(self) -> str:
        payload = self._get(f"{AFL_V2}/competitions", {"pageSize": 50})
        for competition in _as_list(payload.get("competitions")):
            if not isinstance(competition, dict):
                continue
            code = str(competition.get("code") or "").upper()
            name = str(competition.get("name") or "")
            if code == "AFL" and "legacy" not in name.lower():
                return str(competition.get("id"))
        raise RuntimeError("Could not identify the current AFL competition in the official API")

    def season_id(self, season: int, competition_id: str) -> str:
        payload = self._get(f"{AFL_V2}/competitions/{competition_id}/compseasons", {"pageSize": 100})
        candidates: list[dict[str, Any]] = []
        for item in _as_list(payload.get("compSeasons")):
            if isinstance(item, dict) and "legacy" not in str(item.get("name") or "").lower():
                candidates.append(item)
        for item in candidates:
            if re.search(rf"\b{season}\b", str(item.get("name") or "")):
                return str(item.get("id"))
        raise RuntimeError(f"Could not identify the {season} AFL season in the official API")

    def upcoming_matches(self, season: int, now: datetime | None = None) -> list[dict[str, Any]]:
        competition_id = self.competition_id()
        season_id = self.season_id(season, competition_id)
        payload = self._get(f"{AFL_V2}/matches", {
            "competitionId": competition_id,
            "compSeasonId": season_id,
            "roundNumber": "",
            "pageSize": 1000,
        })
        return choose_upcoming_round(_as_list(payload.get("matches")), now=now)

    def token(self) -> str:
        """Get the official AFL media token using the same plain POST used by fitzRoy."""
        errors: list[str] = []
        # The official endpoint is sensitive to unnecessary browser headers. Try the
        # exact request used by the maintained fitzRoy AFL client first.
        for url in AFL_TOKEN_URLS:
            try:
                response = self.session.post(url, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                token = payload.get("token") if isinstance(payload, dict) else None
                if token:
                    return str(token)
                errors.append(f"POST {url}: no token in response")
            except Exception as exc:
                errors.append(f"POST {url}: {exc}")
        raise RuntimeError("The AFL team-list token could not be retrieved. " + " | ".join(errors[-2:]))

    def match_roster(self, match_id: str, token: str) -> dict[str, dict[str, Any]]:
        errors: list[str] = []
        headers = {
            "x-media-mis-token": token,
            "Origin": "https://www.afl.com.au",
            "Referer": "https://www.afl.com.au/matches/team-lineups",
        }
        for template in AFL_ROSTER_URLS:
            url = template.format(match_id=match_id)
            try:
                payload = _request_json(
                    self.session,
                    "GET",
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    attempts=1,
                )
                parsed = parse_match_roster(payload)
                home_count = int(parsed.get("home", {}).get("raw_selected_count", 0))
                away_count = int(parsed.get("away", {}).get("raw_selected_count", 0))
                emergency_count = len(parsed.get("home", {}).get("emergencies", [])) + len(parsed.get("away", {}).get("emergencies", []))
                if home_count or away_count or emergency_count:
                    return parsed
                errors.append(f"{url}: roster response contained no players")
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise RuntimeError("No usable AFL roster was returned for match " + str(match_id) + ". " + " | ".join(errors[-2:]))

    def refresh(self, season: int, roster: list[dict[str, str]], previous_statuses: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        checked_at = datetime.now(PERTH_TZ).isoformat(timespec="seconds")
        previous_statuses = previous_statuses or {}
        fixtures = self.upcoming_matches(season)
        if not fixtures:
            raise RuntimeError("The AFL fixture API did not return an upcoming round.")

        errors: list[dict[str, str]] = []
        try:
            published_round, club_rosters = self.footywire_team_selections()
            fixture_rounds = {int(f["round_number"]) for f in fixtures if str(f.get("round_number", "")).isdigit()}
            if published_round is not None and fixture_rounds and published_round not in fixture_rounds:
                raise RuntimeError(
                    f"FootyWire is showing Round {published_round}, but the upcoming fixture is "
                    f"Round {sorted(fixture_rounds)[0]}."
                )
            usable = sum(
                int(side.get("raw_selected_count", 0)) + len(side.get("emergencies", []))
                for side in club_rosters.values()
            )
            if usable <= 0:
                raise RuntimeError("FootyWire returned no usable selected-player lists.")
            statuses = build_player_statuses_from_clubs(
                roster, fixtures, club_rosters, checked_at, previous_statuses
            )
            missing_clubs = sorted({f[side] for f in fixtures for side in ("home", "away")} - set(club_rosters))
            if missing_clubs:
                errors.append({
                    "match_id": "footywire", "level": "warning",
                    "message": "No FootyWire team was parsed for: " + ", ".join(missing_clubs),
                })
            return {
                "lineups_refreshed_at": checked_at,
                "afl_matches": fixtures,
                "afl_next_opponents": opponents_from_fixtures(fixtures),
                "team_status": statuses,
                "lineup_errors": errors,
                "lineup_failure_message": "",
                "lineup_source": FOOTYWIRE_TEAM_SELECTIONS,
            }
        except Exception as exc:
            errors.append({"match_id": "footywire", "level": "error", "message": str(exc)})

        # Preserve an explicit failure state rather than silently using the AFL
        # roster endpoint, which currently returns empty payloads and HTTP 403.
        failure_statuses: dict[str, dict[str, Any]] = {}
        fixture_by_club: dict[str, dict[str, Any]] = {}
        for fixture in fixtures:
            fixture_by_club[str(fixture.get("home") or "")] = fixture
            fixture_by_club[str(fixture.get("away") or "")] = fixture
        for player in roster:
            name, club = player["player"], player["club"]
            fixture = fixture_by_club.get(club, {})
            opponent = ""
            if fixture:
                opponent = fixture.get("away") if fixture.get("home") == club else fixture.get("home")
            failure_statuses[name] = {
                "player": name, "club": club, "status": "check_failed",
                "status_label": STATUS_LABELS["check_failed"],
                "opponent": opponent or "", "match_id": str(fixture.get("match_id") or ""),
                "round": str(fixture.get("round_name") or ""),
                "start_utc": str(fixture.get("start_utc") or ""),
                "checked_at": checked_at, "matched_name": "", "selected_count": 0,
            }
        return {
            "lineups_refreshed_at": checked_at,
            "afl_matches": fixtures,
            "afl_next_opponents": opponents_from_fixtures(fixtures),
            "team_status": failure_statuses,
            "lineup_errors": errors,
            "lineup_failure_message": "FootyWire team selections could not be retrieved. See the error below.",
            "lineup_source": FOOTYWIRE_TEAM_SELECTIONS,
        }
