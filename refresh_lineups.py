from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

PERTH_TZ = timezone(timedelta(hours=8))
AFL_V2 = "https://aflapi.afl.com.au/afl/v2"
AFL_TEAM_LINEUPS_PAGE = "https://www.afl.com.au/matches/team-lineups"
LINEUP_DATA_PATH = Path(__file__).resolve().parent / "data" / "lineups_latest.json"

STATUS_LABELS = {
    "confirmed": "Playing — confirmed",
    "provisional": "Provisional — extended squad",
    "emergency": "Emergency",
    "not_selected": "Not selected",
    "bye": "Bye",
    "not_announced": "Not yet published",
    "late_out": "Late out",
}

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

CLUB_DISPLAY_NAMES = {
    "ADE": "Adelaide Crows", "BRL": "Brisbane Lions", "CAR": "Carlton",
    "COL": "Collingwood", "ESS": "Essendon", "FRE": "Fremantle",
    "GCS": "Gold Coast Suns", "GEE": "Geelong Cats", "GWS": "GWS Giants",
    "HAW": "Hawthorn", "MEL": "Melbourne", "NM": "North Melbourne",
    "POR": "Port Adelaide", "RIC": "Richmond", "STK": "St Kilda",
    "SYD": "Sydney Swans", "WBD": "Western Bulldogs", "WCE": "West Coast Eagles",
}

SELECTED_GROUPS = {
    "back", "backs", "fullback", "fullbacks", "halfback", "halfbacks",
    "centre", "centres", "center", "centers", "wing", "wings",
    "halfforward", "halfforwards", "forward", "forwards", "fullforward", "fullforwards",
    "follower", "followers", "ruck", "rucks", "interchange", "interchanges", "bench",
    "selected", "selectedplayers", "lineup", "lineups", "players", "squad", "teamselection",
}
EMERGENCY_GROUPS = {"emergency", "emergencies", "emg"}


def canonical_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _nested_get(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        if path in data:
            return data[path]
        current: Any = data
        valid = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                valid = False
                break
            current = current[part]
        if valid:
            return current
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


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


def _team_identity(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    values = (
        _nested_get(node, "team.abbreviation", "club.abbreviation", "abbreviation"),
        _nested_get(node, "team.name", "club.name", "name"),
        _nested_get(node, "team.nickname", "club.nickname", "nickname"),
        _nested_get(node, "team.providerId", "teamId", "providerId"),
    )
    for value in values:
        code = team_code(value)
        if code:
            return code
    return ""


def parse_match(match: dict[str, Any]) -> dict[str, Any] | None:
    provider_id = _nested_get(match, "providerId", "providerID", "id")
    start = _parse_utc(_nested_get(match, "utcStartTime", "startTime", "date"))
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
    round_name = _nested_get(match, "round.name", "roundName") or (
        f"Round {round_number}" if round_number not in (None, "") else ""
    )
    return {
        "match_id": str(provider_id),
        "start_utc": start.isoformat(),
        "round_number": round_number,
        "round_name": str(round_name or ""),
        "round_provider_id": str(_nested_get(match, "round.providerId", "roundProviderId") or ""),
        "status": str(_nested_get(match, "status", "match.status") or ""),
        "home": home,
        "away": away,
    }


def choose_upcoming_round(matches: Iterable[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    parsed = [record for item in matches if isinstance(item, dict) and (record := parse_match(item))]
    future: list[dict[str, Any]] = []
    for record in parsed:
        start = _parse_utc(record["start_utc"])
        status = str(record.get("status") or "").lower()
        if start and start >= now - timedelta(hours=6) and status not in {
            "completed", "complete", "concluded", "cancelled", "c"
        }:
            future.append(record)
    if not future:
        return []
    future.sort(key=lambda item: item["start_utc"])
    first = future[0]
    if first.get("round_number") not in (None, ""):
        selected = [item for item in future if item.get("round_number") == first.get("round_number")]
    else:
        selected = [item for item in future if item.get("round_name") == first.get("round_name")]
    return sorted(selected, key=lambda item: item["start_utc"])


def _player_id_from_dict(item: dict[str, Any]) -> str:
    for path in (
        "player.providerId", "person.providerId", "providerId", "playerId", "player.id", "person.id", "id"
    ):
        value = _nested_get(item, path)
        if value not in (None, ""):
            text = str(value)
            if text.startswith("CD_I") or path not in {"providerId", "id"}:
                return text
    return ""


def _player_name_from_dict(item: dict[str, Any]) -> str:
    for nested_key in ("player", "person", "playerName"):
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            name = _player_name_from_dict(nested)
            if name:
                return name
        elif isinstance(nested, str) and " " in nested.strip():
            return nested.strip()
    for key in ("fullName", "displayName", "playerName", "personName"):
        value = _nested_get(item, key)
        if isinstance(value, str) and " " in value.strip():
            return value.strip()
    first = _nested_get(
        item, "firstName", "givenName", "player.firstName", "person.firstName", "playerName.givenName"
    )
    last = _nested_get(
        item, "surname", "lastName", "familyName", "player.surname", "person.surname", "playerName.surname"
    )
    if first and last:
        return f"{str(first).strip()} {str(last).strip()}".strip()
    return ""


def _looks_like_player(item: dict[str, Any]) -> bool:
    if _player_id_from_dict(item):
        return True
    keys = {_normalise_key(key) for key in item}
    return bool(
        {"firstname", "surname"}.issubset(keys)
        or {"givenname", "familyname"}.issubset(keys)
        or "player" in keys
        or "playername" in keys
        or "jumpernumber" in keys
    )


def parse_roster_side(side: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    if isinstance(side, list):
        side = {"players": side}
    if not isinstance(side, dict):
        return {
            "selected": [], "emergencies": [], "selected_records": [], "emergency_records": [],
            "provisional": False, "raw_selected_count": 0,
        }

    selected: dict[str, dict[str, str]] = {}
    emergencies: dict[str, dict[str, str]] = {}
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

        if _looks_like_player(node):
            name = _player_name_from_dict(node)
            if name:
                player_id = _player_id_from_dict(node)
                record = {"name": name, "player_id": player_id}
                flags = " ".join(
                    str(_nested_get(node, path) or "")
                    for path in ("status", "selectionStatus", "position", "role", "type")
                ).lower()
                emergency_flag = bool(
                    _nested_get(node, "isEmergency", "emergency") is True
                    or category_key in EMERGENCY_GROUPS
                    or "emerg" in category_key
                    or "emerg" in flags
                )
                key = canonical_name(name)
                if emergency_flag:
                    emergencies[key] = record
                elif category_key in SELECTED_GROUPS or any(
                    token in category_key
                    for token in ("back", "forward", "centre", "center", "follow", "interchange", "bench", "selected", "lineup", "squad")
                ) or not category_key:
                    selected[key] = record

        for key, value in node.items():
            key_norm = _normalise_key(key)
            next_category = category
            if key_norm in EMERGENCY_GROUPS or "emerg" in key_norm:
                next_category = "emergencies"
            elif key_norm in SELECTED_GROUPS or any(
                token in key_norm
                for token in ("back", "forward", "centre", "center", "follow", "interchange", "bench", "selected", "lineup", "squad", "extended")
            ):
                next_category = key_norm
            visit(value, next_category)

    visit(side)
    selected_records = sorted(selected.values(), key=lambda row: row["name"])
    emergency_records = sorted(emergencies.values(), key=lambda row: row["name"])
    selected_names = [row["name"] for row in selected_records]
    emergency_names = [row["name"] for row in emergency_records]
    provisional = explicit_provisional or len(selected_names) > 23
    return {
        "selected": selected_names,
        "emergencies": emergency_names,
        "selected_records": selected_records,
        "emergency_records": emergency_records,
        "provisional": provisional,
        "raw_selected_count": len(selected_names),
    }


def _side_quality(side: dict[str, Any]) -> tuple[int, int]:
    return (int(side.get("raw_selected_count", 0)), len(side.get("emergencies") or []))


def parse_club_rosters_payload(payload: Any) -> dict[str, dict[str, Any]]:
    """Extract the best roster for each club from any AFL line-up JSON shape."""
    found: dict[str, dict[str, Any]] = {}

    def store(club: str, node: Any) -> None:
        if not club:
            return
        side = parse_roster_side(node)
        if _side_quality(side) > _side_quality(found.get(club, {})):
            found[club] = side

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return

        for key in ("homeTeam", "home", "awayTeam", "away", "team", "club"):
            value = node.get(key)
            if isinstance(value, (dict, list)):
                identity_node = value if isinstance(value, dict) else node
                club = _team_identity(identity_node) or _team_identity(node)
                store(club, value)

        club = _team_identity(node)
        if club:
            store(club, node)

        for value in node.values():
            visit(value)

    visit(payload)
    return found


def merge_club_rosters(*collections: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for collection in collections:
        for club, side in (collection or {}).items():
            if _side_quality(side) > _side_quality(merged.get(club, {})):
                merged[club] = side
    return merged


def _club_mentions(text: str) -> set[str]:
    lowered = re.sub(r"\s+", " ", text.lower())
    matches: set[str] = set()
    for name, club in TEAM_NAME_TO_FCFC.items():
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", lowered):
            matches.add(club)
    return matches


def parse_rendered_lineups_html(page_html: str) -> tuple[int | None, dict[str, dict[str, Any]]]:
    """Fallback parser for the fully rendered AFL page.

    It uses player profile links and the nearest DOM container that identifies one
    club. Network JSON is preferred, but this makes the browser collector resilient
    to an internal endpoint rename.
    """
    soup = BeautifulSoup(page_html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    round_match = re.search(r"\bRound\s*(\d+)\b", text, re.I)
    round_number = int(round_match.group(1)) if round_match else None
    selected: dict[str, dict[str, dict[str, str]]] = {}
    emergencies: dict[str, dict[str, dict[str, str]]] = {}

    anchors = soup.find_all("a", href=True)
    for anchor in anchors:
        href = str(anchor.get("href") or "")
        if not re.search(r"/(?:players?|player-profile)/", href, re.I):
            continue
        name = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if len(name.split()) < 2 or len(name) > 60:
            continue

        club = ""
        emergency = False
        node = anchor
        for _ in range(9):
            if not getattr(node, "parent", None):
                break
            node = node.parent
            candidate = " ".join(
                [
                    str(node.get("data-team") or ""), str(node.get("aria-label") or ""),
                    " ".join(node.get("class") or []), str(node.get("id") or ""),
                    node.get_text(" ", strip=True)[:1200],
                ]
            )
            mentions = _club_mentions(candidate)
            if "emerg" in candidate.lower():
                emergency = True
            if len(mentions) == 1:
                club = next(iter(mentions))
                break
        if not club:
            continue
        record = {"name": name, "player_id": ""}
        target = emergencies if emergency else selected
        target.setdefault(club, {})[canonical_name(name)] = record

    clubs: dict[str, dict[str, Any]] = {}
    for club in set(selected) | set(emergencies):
        selected_records = sorted(selected.get(club, {}).values(), key=lambda row: row["name"])
        emergency_records = sorted(emergencies.get(club, {}).values(), key=lambda row: row["name"])
        clubs[club] = {
            "selected": [row["name"] for row in selected_records],
            "emergencies": [row["name"] for row in emergency_records],
            "selected_records": selected_records,
            "emergency_records": emergency_records,
            "provisional": len(selected_records) > 23,
            "raw_selected_count": len(selected_records),
        }
    return round_number, clubs


def validate_lineup_document(
    document: dict[str, Any], expected_fixtures: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    clubs = document.get("clubs") if isinstance(document, dict) else None
    if not isinstance(clubs, dict):
        return {"valid": False, "errors": ["The lineup document has no clubs object."], "warnings": []}

    expected_clubs: set[str] = set()
    fixtures = expected_fixtures if expected_fixtures is not None else document.get("fixtures") or []
    for fixture in fixtures:
        if isinstance(fixture, dict):
            expected_clubs.update({str(fixture.get("home") or ""), str(fixture.get("away") or "")})
    expected_clubs.discard("")

    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(expected_clubs - set(clubs))
    if missing:
        errors.append("Missing clubs: " + ", ".join(missing))

    invalid_counts: list[str] = []
    duplicate_players: list[str] = []
    for club in sorted(expected_clubs or set(clubs)):
        side = clubs.get(club)
        if not isinstance(side, dict):
            continue
        selected = [str(name) for name in side.get("selected") or [] if str(name).strip()]
        count = len({canonical_name(name) for name in selected})
        if count < 22 or count > 27:
            invalid_counts.append(f"{club}={count}")
        if count != len(selected):
            duplicate_players.append(club)
    if invalid_counts:
        errors.append("Implausible selected-player counts: " + ", ".join(invalid_counts))
    if duplicate_players:
        errors.append("Duplicate selected players within: " + ", ".join(duplicate_players))

    if not expected_clubs and len(clubs) < 2:
        errors.append("The lineup document does not identify a complete round.")
    if not document.get("round_number"):
        warnings.append("No numeric round was detected.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "expected_clubs": sorted(expected_clubs),
        "club_count": len(clubs),
    }


def _match_player(name_map: dict[str, str], player_name: str) -> str | None:
    target = canonical_name(player_name)
    if target in name_map:
        return name_map[target]

    # Nick/Nicholas and similar common short-name differences are handled by a
    # conservative first-initial + surname match, only where unique within a club.
    parts = re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKD", player_name))
    if len(parts) < 2:
        return None
    initial = parts[0][0].lower()
    surname = canonical_name(parts[-1])
    candidates: list[str] = []
    for original in name_map.values():
        other = re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKD", original))
        if len(other) >= 2 and other[0][0].lower() == initial and canonical_name(other[-1]) == surname:
            candidates.append(original)
    return candidates[0] if len(candidates) == 1 else None


def opponents_from_fixtures(fixtures: Iterable[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for fixture in fixtures:
        home = str(fixture.get("home") or "")
        away = str(fixture.get("away") or "")
        if home and away:
            result[home] = away
            result[away] = home
    return result


def build_player_statuses_from_clubs(
    roster: list[dict[str, str]],
    fixtures: list[dict[str, Any]],
    club_rosters: dict[str, dict[str, Any]],
    checked_at: str,
    previous_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    previous_statuses = previous_statuses or {}
    fixture_by_club: dict[str, dict[str, Any]] = {}
    for fixture in fixtures:
        fixture_by_club[str(fixture.get("home") or "")] = fixture
        fixture_by_club[str(fixture.get("away") or "")] = fixture

    statuses: dict[str, dict[str, Any]] = {}
    for player in roster:
        name = str(player["player"])
        club = str(player["club"])
        fixture = fixture_by_club.get(club)
        opponent = ""
        if fixture:
            opponent = str(fixture.get("away") if fixture.get("home") == club else fixture.get("home") or "")
        default_status = "bye" if not fixture else "not_announced"
        base = {
            "player": name,
            "club": club,
            "status": default_status,
            "status_label": STATUS_LABELS[default_status],
            "opponent": opponent,
            "match_id": str((fixture or {}).get("match_id") or ""),
            "round": str((fixture or {}).get("round_name") or ""),
            "start_utc": str((fixture or {}).get("start_utc") or ""),
            "checked_at": checked_at,
            "matched_name": "",
            "selected_count": 0,
        }
        side = club_rosters.get(club)
        if not isinstance(side, dict):
            statuses[name] = base
            continue

        selected_names = [str(value) for value in side.get("selected") or []]
        emergency_names = [str(value) for value in side.get("emergencies") or []]
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
            same_match = str(previous.get("match_id") or "") == str(base["match_id"])
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
    except (TypeError, ValueError):
        return True


def load_lineup_document(path: Path | str = LINEUP_DATA_PATH) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(
            "No browser-collected AFL line-up file exists yet. The GitHub workflow must run successfully first."
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("The saved AFL line-up file is not a JSON object.")
    validation = payload.get("validation")
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        rerun = validate_lineup_document(payload)
        if not rerun.get("valid"):
            raise RuntimeError("The saved AFL line-up file failed validation: " + "; ".join(rerun.get("errors") or []))
        payload["validation"] = rerun
    return payload


def lineup_result_from_document(
    document: dict[str, Any],
    season: int,
    roster: list[dict[str, str]],
    previous_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    file_season = int(document.get("season") or 0)
    if file_season != int(season):
        raise RuntimeError(f"The saved line-up file is for {file_season}, not {season}.")
    fixtures = [row for row in document.get("fixtures") or [] if isinstance(row, dict)]
    clubs = document.get("clubs") or {}
    checked_at = str(document.get("checked_at_perth") or document.get("checked_at") or "")
    if not checked_at:
        checked_at = datetime.now(PERTH_TZ).isoformat(timespec="seconds")
    statuses = build_player_statuses_from_clubs(
        roster=roster,
        fixtures=fixtures,
        club_rosters=clubs,
        checked_at=checked_at,
        previous_statuses=previous_statuses,
    )
    diagnostics = document.get("diagnostics") or {}
    warnings = list(document.get("warnings") or []) + list((document.get("validation") or {}).get("warnings") or [])
    lineup_errors = [
        {"level": "warning", "match_id": "browser-cache", "message": str(message)}
        for message in warnings if str(message).strip()
    ]
    return {
        "lineups_refreshed_at": checked_at,
        "afl_matches": fixtures,
        "afl_next_opponents": opponents_from_fixtures(fixtures),
        "team_status": statuses,
        "lineup_errors": lineup_errors,
        "lineup_failure_message": "",
        "lineup_source": str(document.get("source") or "AFL Team Line-ups browser collector"),
        "lineup_source_url": str(document.get("source_url") or AFL_TEAM_LINEUPS_PAGE),
        "lineup_source_title": str(document.get("source_title") or f"AFL Team Line-ups — Round {document.get('round_number', '')}"),
        "lineup_round_number": document.get("round_number"),
        "lineup_collection_method": str(document.get("collection_method") or "playwright"),
        "lineup_validation": document.get("validation") or {},
        "lineup_diagnostics": diagnostics,
    }


def load_committed_lineup_result(
    season: int,
    roster: list[dict[str, str]],
    previous_statuses: dict[str, dict[str, Any]] | None = None,
    path: Path | str = LINEUP_DATA_PATH,
) -> dict[str, Any]:
    return lineup_result_from_document(
        load_lineup_document(path), season=season, roster=roster, previous_statuses=previous_statuses
    )


def _club_heading_patterns() -> list[tuple[re.Pattern[str], str]]:
    rows: list[tuple[re.Pattern[str], str]] = []
    names_by_club: dict[str, set[str]] = {}
    for name, club in TEAM_NAME_TO_FCFC.items():
        names_by_club.setdefault(club, set()).add(name)
    for club, names in names_by_club.items():
        alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        rows.append((re.compile(rf"^\s*(?:{alternatives})\s*$", re.I), club))
    return rows


def result_from_pasted_text(
    text: str,
    season: int,
    roster: list[dict[str, str]],
    fixtures: list[dict[str, Any]],
    previous_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Manual fallback for text copied from the rendered AFL Team Line-ups page.

    Only FCFC squad players need to be recognised. A club is treated as complete
    only when its heading is found in the pasted text; absent players under that
    heading are therefore safely classified as not selected.
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Paste the AFL Team Line-ups page text first.")
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines() if line.strip()]
    patterns = _club_heading_patterns()
    sections: dict[str, list[str]] = {}
    current_club = ""
    for line in lines:
        matched = ""
        for pattern, club in patterns:
            if pattern.match(line):
                matched = club
                break
        if matched:
            current_club = matched
            sections.setdefault(current_club, [])
            continue
        if current_club:
            sections[current_club].append(line)

    if not sections:
        raise ValueError(
            "No club headings were recognised in the pasted text. Copy the full rendered Team Line-ups page, including club names."
        )

    clubs: dict[str, dict[str, Any]] = {}
    for club, section_lines in sections.items():
        section = "\n".join(section_lines)
        lower = section.lower()
        emergency_index = lower.find("emergenc")
        selected: list[str] = []
        emergencies: list[str] = []
        for row in roster:
            if row.get("club") != club:
                continue
            player = str(row.get("player") or "")
            key = canonical_name(player)
            compact = canonical_name(section)
            if not key or key not in compact:
                continue
            position = lower.find(player.lower())
            if emergency_index >= 0 and position >= emergency_index:
                emergencies.append(player)
            else:
                selected.append(player)
        provisional = any(term in lower for term in ("extended squad", "extended team", "26-player", "26 player"))
        clubs[club] = {
            "selected": selected,
            "emergencies": emergencies,
            "selected_records": [{"name": name, "player_id": ""} for name in selected],
            "emergency_records": [{"name": name, "player_id": ""} for name in emergencies],
            "provisional": provisional,
            "raw_selected_count": len(selected),
            "manual_partial": True,
        }

    checked_at = datetime.now(PERTH_TZ).isoformat(timespec="seconds")
    statuses = build_player_statuses_from_clubs(
        roster, fixtures, clubs, checked_at, previous_statuses=previous_statuses
    )
    round_number = next((fixture.get("round_number") for fixture in fixtures if fixture.get("round_number") is not None), None)
    return {
        "lineups_refreshed_at": checked_at,
        "afl_matches": fixtures,
        "afl_next_opponents": opponents_from_fixtures(fixtures),
        "team_status": statuses,
        "lineup_errors": [],
        "lineup_failure_message": "",
        "lineup_source": "Manual paste from AFL Team Line-ups",
        "lineup_source_url": AFL_TEAM_LINEUPS_PAGE,
        "lineup_source_title": f"Pasted AFL Team Line-ups — Round {round_number or ''}",
        "lineup_round_number": round_number,
        "lineup_collection_method": "manual-paste",
    }


class AFLLineupClient:
    """Public fixture reader plus local browser-cache loader.

    Team selections are deliberately not scraped from Streamlit. The scheduled
    Playwright workflow writes data/lineups_latest.json; this class only reads that
    validated file inside the app.
    """

    def __init__(self, timeout: int = 15, lineup_path: Path | str = LINEUP_DATA_PATH):
        self.timeout = timeout
        self.lineup_path = Path(lineup_path)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; FCFC-Squad-Tracker/3.0)",
            "Accept": "application/json,text/plain,*/*",
            "Origin": "https://www.afl.com.au",
            "Referer": "https://www.afl.com.au/",
        })

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"AFL API returned {type(payload).__name__}, not an object.")
        return payload

    def competition_id(self) -> str:
        payload = self._get(f"{AFL_V2}/competitions", {"pageSize": 50})
        for competition in _as_list(payload.get("competitions")):
            if not isinstance(competition, dict):
                continue
            if str(competition.get("code") or "").upper() == "AFL" and "legacy" not in str(competition.get("name") or "").lower():
                return str(competition.get("id"))
        raise RuntimeError("Could not identify the current AFL competition in the official public API.")

    def season_id(self, season: int, competition_id: str) -> str:
        payload = self._get(f"{AFL_V2}/competitions/{competition_id}/compseasons", {"pageSize": 100})
        for item in _as_list(payload.get("compSeasons")):
            if not isinstance(item, dict) or "legacy" in str(item.get("name") or "").lower():
                continue
            if re.search(rf"\b{season}\b", str(item.get("name") or "")):
                return str(item.get("id"))
        raise RuntimeError(f"Could not identify the {season} AFL season in the official public API.")

    def upcoming_matches(self, season: int, now: datetime | None = None) -> list[dict[str, Any]]:
        competition_id = self.competition_id()
        season_id = self.season_id(season, competition_id)
        payload = self._get(f"{AFL_V2}/matches", {
            "competitionId": competition_id,
            "compSeasonId": season_id,
            "roundNumber": "",
            "pageSize": 300,
        })
        return choose_upcoming_round(_as_list(payload.get("matches")), now=now)

    def refresh(
        self,
        season: int,
        roster: list[dict[str, str]],
        previous_statuses: dict[str, dict[str, Any]] | None = None,
        games: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del games  # Kept in the signature for compatibility with earlier builds.
        return load_committed_lineup_result(
            season=season,
            roster=roster,
            previous_statuses=previous_statuses,
            path=self.lineup_path,
        )
