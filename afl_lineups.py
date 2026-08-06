from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

PERTH_TZ = timezone(timedelta(hours=8))
AFL_V2 = "https://aflapi.afl.com.au/afl/v2"
AFL_TOKEN_URL = "https://api.afl.com.au/cfs/afl/WMCTok"
AFL_ROSTER_URL = "https://api.afl.com.au/cfs/afl/matchRoster/full/{match_id}"
AFL_PLAYER_STATS_URL = "https://api.afl.com.au/cfs/afl/playerStats/match/{match_id}"

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



def _contains_stat_marker(node: Any) -> bool:
    """Return True when a dictionary looks like one player-stat record.

    The official endpoint has used several response shapes.  Team selections
    are still present as player-stat rows before a match, with the numeric
    fields set to zero, so checking for the field names is more useful than
    checking for non-zero values.
    """
    markers = {
        "kicks", "handballs", "disposals", "marks", "goals",
        "behinds", "tackles", "hitouts", "freesfor",
        "playerstats", "stats",
    }
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        key_norm = _normalise_key(key)
        if key_norm in markers:
            return True
        if isinstance(value, dict) and any(_normalise_key(k) in markers for k in value):
            return True
    return False


def parse_player_stats_roster(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract selected players from the official player-stats response.

    This is intentionally independent of the match-roster endpoint.  The
    player-stats endpoint is the same official feed already used successfully
    by the app's statistics refresh and, once teams are published, normally
    exposes one row for every selected player even before the match starts.
    """
    selected: dict[str, dict[str, str]] = {"home": {}, "away": {}}

    def visit(node: Any, side: str | None = None, in_player_stats: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, side=side, in_player_stats=in_player_stats)
            return
        if not isinstance(node, dict):
            return

        if side and in_player_stats:
            name = _player_name_from_dict(node)
            if name and (_contains_stat_marker(node) or any(k in node for k in ("player", "playerName", "person"))):
                selected[side][canonical_name(name)] = name
                return

        for key, value in node.items():
            key_norm = _normalise_key(key)
            next_side = side
            next_in_stats = in_player_stats

            if key_norm in {"hometeamplayerstats", "homeplayerstats", "homeplayers"}:
                next_side = "home"
                next_in_stats = True
            elif key_norm in {"awayteamplayerstats", "awayplayerstats", "awayplayers"}:
                next_side = "away"
                next_in_stats = True
            elif key_norm in {"home", "hometeam"} and isinstance(value, (dict, list)):
                next_side = "home"
            elif key_norm in {"away", "awayteam"} and isinstance(value, (dict, list)):
                next_side = "away"
            if next_side and ("playerstats" in key_norm or key_norm in {"players", "playerstatistics"}):
                next_in_stats = True

            visit(value, side=next_side, in_player_stats=next_in_stats)

    visit(payload)

    result: dict[str, dict[str, Any]] = {}
    for side in ("home", "away"):
        names = sorted(selected[side].values())
        # A final AFL side is usually 22 or 23 players.  Accept a slightly wider
        # range for extended Sunday squads, but reject a full club list.
        if 18 <= len(names) <= 30:
            result[side] = {
                "selected": names,
                "emergencies": [],
                "provisional": len(names) > 23,
                "raw_selected_count": len(names),
            }
        else:
            result[side] = {
                "selected": [],
                "emergencies": [],
                "provisional": False,
                "raw_selected_count": len(names),
            }
    return result

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
    root = payload.get("matchRoster") if isinstance(payload.get("matchRoster"), dict) else payload
    home = _nested_get(root, "homeTeam", "home") if isinstance(root, dict) else None
    away = _nested_get(root, "awayTeam", "away") if isinstance(root, dict) else None
    return {
        "home": parse_roster_side(home if isinstance(home, dict) else {}),
        "away": parse_roster_side(away if isinstance(away, dict) else {}),
    }


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
            previous = dict(previous_statuses.get(name) or {})
            same_round = str(previous.get("round") or "") == str(fixture.get("round_name") or "")
            if same_round and previous.get("status"):
                previous.update({
                    "opponent": opponent,
                    "match_id": fixture["match_id"],
                    "round": fixture.get("round_name", ""),
                    "start_utc": fixture.get("start_utc", ""),
                })
                statuses[name] = previous
            else:
                base["status"] = "not_announced"
                base["status_label"] = STATUS_LABELS["not_announced"]
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
        payload = _request_json(self.session, "POST", AFL_TOKEN_URL, timeout=self.timeout)
        token = payload.get("token")
        if not token:
            raise RuntimeError("The AFL team-list token endpoint did not return a token")
        return str(token)

    def match_roster(self, match_id: str, token: str) -> dict[str, dict[str, Any]]:
        payload = _request_json(
            self.session,
            "GET",
            AFL_ROSTER_URL.format(match_id=match_id),
            headers={"x-media-mis-token": token},
            timeout=self.timeout,
        )
        return parse_match_roster(payload)

    def player_stats_roster(self, match_id: str, token: str) -> dict[str, dict[str, Any]]:
        """Read the published team from the official player-stats feed.

        This endpoint is already used by the working statistics refresh.  Using
        it for selections avoids ESPN and reuses the one AFL data route that has
        been proven to work from Streamlit Cloud.
        """
        payload = _request_json(
            self.session,
            "GET",
            AFL_PLAYER_STATS_URL.format(match_id=match_id),
            headers={
                "x-media-mis-token": token,
                "Origin": "https://www.afl.com.au",
                "Referer": f"https://www.afl.com.au/afl/matches/{match_id}",
            },
            timeout=self.timeout,
            attempts=3,
        )
        return parse_player_stats_roster(payload)

    def _espn_scoreboard(self, fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
        starts = [_parse_utc(item.get("start_utc")) for item in fixtures]
        starts = [value for value in starts if value]
        if not starts:
            return []
        start_date = (min(starts) - timedelta(days=1)).strftime("%Y%m%d")
        end_date = (max(starts) + timedelta(days=1)).strftime("%Y%m%d")
        url = "https://site.api.espn.com/apis/site/v2/sports/australian-football/afl/scoreboard"
        payload = _request_json(
            self.session,
            "GET",
            url,
            params={"dates": f"{start_date}-{end_date}", "limit": 100},
            timeout=self.timeout,
            attempts=3,
        )
        return [item for item in _as_list(payload.get("events")) if isinstance(item, dict)]

    @staticmethod
    def _espn_event_teams(event: dict[str, Any]) -> tuple[str, str]:
        competitions = _as_list(event.get("competitions"))
        if not competitions or not isinstance(competitions[0], dict):
            return "", ""
        home = ""
        away = ""
        for competitor in _as_list(competitions[0].get("competitors")):
            if not isinstance(competitor, dict):
                continue
            team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
            code = team_code(
                team.get("abbreviation")
                or team.get("displayName")
                or team.get("shortDisplayName")
                or team.get("name")
            )
            side = str(competitor.get("homeAway") or "").lower()
            if side == "home":
                home = code
            elif side == "away":
                away = code
        return home, away

    @staticmethod
    def _match_espn_event(fixture: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        fixture_teams = {fixture.get("home"), fixture.get("away")}
        fixture_start = _parse_utc(fixture.get("start_utc"))
        candidates: list[tuple[float, dict[str, Any]]] = []
        for event in events:
            home, away = AFLLineupClient._espn_event_teams(event)
            if {home, away} != fixture_teams:
                continue
            event_start = _parse_utc(event.get("date"))
            gap = abs((event_start - fixture_start).total_seconds()) if event_start and fixture_start else 0.0
            candidates.append((gap, event))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0])
        return candidates[0][1]

    def _espn_summary(self, event_id: str) -> dict[str, Any]:
        url = "https://site.api.espn.com/apis/site/v2/sports/australian-football/afl/summary"
        return _request_json(
            self.session,
            "GET",
            url,
            params={"event": event_id, "enable": "roster"},
            timeout=self.timeout,
            attempts=3,
        )

    @staticmethod
    def _espn_player_name(item: dict[str, Any]) -> str:
        athlete = item.get("athlete") if isinstance(item.get("athlete"), dict) else item
        for key in ("displayName", "fullName", "name", "shortName"):
            value = athlete.get(key) if isinstance(athlete, dict) else None
            if isinstance(value, str) and " " in value.strip():
                return value.strip()
        first = athlete.get("firstName") if isinstance(athlete, dict) else ""
        last = athlete.get("lastName") if isinstance(athlete, dict) else ""
        return f"{first or ''} {last or ''}".strip()

    @staticmethod
    def _espn_side_from_entries(entries: list[Any]) -> dict[str, Any]:
        selected: dict[str, str] = {}
        emergencies: dict[str, str] = {}
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            name = AFLLineupClient._espn_player_name(raw)
            if not name:
                continue
            status_bits = " ".join(
                str(raw.get(key) or "")
                for key in ("status", "type", "role", "position", "designation")
            ).lower()
            athlete = raw.get("athlete") if isinstance(raw.get("athlete"), dict) else {}
            status_bits += " " + " ".join(
                str(athlete.get(key) or "") for key in ("status", "position", "designation")
            ).lower()
            is_emergency = any(token in status_bits for token in ("emergency", "reserve", "emg"))
            is_inactive = any(token in status_bits for token in ("inactive", "out", "omitted", "injured"))
            active_flag = raw.get("active")
            if active_flag is False and not is_emergency:
                is_inactive = True
            if is_inactive:
                continue
            target = emergencies if is_emergency else selected
            target[canonical_name(name)] = name
        names = sorted(selected.values())
        emergency_names = sorted(emergencies.values())
        # AFL final teams normally contain 22 or 23 players. A larger list is
        # treated as an extended squad, while a full-season roster is rejected.
        if len(names) > 30:
            return {"selected": [], "emergencies": [], "provisional": False, "raw_selected_count": 0}
        return {
            "selected": names,
            "emergencies": emergency_names,
            "provisional": len(names) > 23,
            "raw_selected_count": len(names),
        }

    @staticmethod
    def _espn_team_code(record: dict[str, Any]) -> str:
        team = record.get("team") if isinstance(record.get("team"), dict) else {}
        competitor = record.get("competitor") if isinstance(record.get("competitor"), dict) else {}
        for source in (team, competitor, record):
            code = team_code(
                source.get("abbreviation")
                or source.get("displayName")
                or source.get("shortDisplayName")
                or source.get("name")
            )
            if code:
                return code
        return ""

    @staticmethod
    def _extract_espn_rosters(summary: dict[str, Any], fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
        by_club: dict[str, dict[str, Any]] = {}

        def add_record(record: dict[str, Any]) -> None:
            code = AFLLineupClient._espn_team_code(record)
            if not code:
                return
            entries: list[Any] = []
            for key in ("roster", "athletes", "players", "lineup", "entries", "items"):
                value = record.get(key)
                if isinstance(value, list):
                    entries.extend(value)
                elif isinstance(value, dict):
                    for subvalue in value.values():
                        if isinstance(subvalue, list):
                            entries.extend(subvalue)
            side = AFLLineupClient._espn_side_from_entries(entries)
            if side["selected"] or side["emergencies"]:
                existing = by_club.get(code)
                if not existing or side["raw_selected_count"] > existing["raw_selected_count"]:
                    by_club[code] = side

        # ESPN summary responses usually expose match rosters here.
        for key in ("rosters", "lineups"):
            for record in _as_list(summary.get(key)):
                if isinstance(record, dict):
                    add_record(record)

        # Some sports place the same data under boxscore.players.
        boxscore = summary.get("boxscore") if isinstance(summary.get("boxscore"), dict) else {}
        for record in _as_list(boxscore.get("players")):
            if isinstance(record, dict):
                add_record(record)

        # A deliberately broad final pass handles minor ESPN schema changes,
        # but only records containing a recognisable AFL club are accepted.
        def visit(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if not isinstance(node, dict):
                return
            if any(key in node for key in ("roster", "athletes", "players", "lineup", "entries")):
                add_record(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)

        if len(by_club) < 2:
            visit(summary)

        result: dict[str, dict[str, Any]] = {}
        if fixture.get("home") in by_club:
            result["home"] = by_club[fixture["home"]]
        if fixture.get("away") in by_club:
            result["away"] = by_club[fixture["away"]]
        return result

    def espn_match_roster(
        self,
        fixture: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        event = self._match_espn_event(fixture, events)
        if not event:
            raise RuntimeError("ESPN did not list the matching fixture")
        event_id = str(event.get("id") or "")
        if not event_id:
            raise RuntimeError("The matching ESPN fixture had no event ID")
        summary = self._espn_summary(event_id)
        parsed = self._extract_espn_rosters(summary, fixture)
        home_count = int((parsed.get("home") or {}).get("raw_selected_count", 0))
        away_count = int((parsed.get("away") or {}).get("raw_selected_count", 0))
        if home_count < 18 or away_count < 18:
            raise RuntimeError(
                f"ESPN returned incomplete line-ups ({fixture['home']} {home_count}, "
                f"{fixture['away']} {away_count})"
            )
        return parsed

    def refresh(
        self,
        season: int,
        roster: list[dict[str, str]],
        previous_statuses: dict[str, dict[str, Any]] | None = None,
        games: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        checked_at = datetime.now(PERTH_TZ).isoformat(timespec="seconds")
        fixtures = self.upcoming_matches(season)
        if not fixtures:
            return {
                "afl_matches": [],
                "afl_next_opponents": {},
                "team_status": previous_statuses or {},
                "lineup_errors": [{"level": "error", "match_id": "fixture", "message": "No upcoming AFL round was found."}],
                "lineup_failure_message": "No upcoming AFL round was found. Existing team statuses were retained.",
            }

        errors: list[dict[str, str]] = []
        match_rosters: dict[str, dict[str, dict[str, Any]]] = {}
        failed: set[str] = set()

        # Primary source: the official AFL player-stats feed.  This is the
        # same endpoint already used successfully by the statistics refresh,
        # and it normally contains the selected players with zeroed statistics
        # as soon as teams are published.
        try:
            token = self.token()
        except Exception as exc:
            token = ""
            errors.append({"level": "warning", "match_id": "afl-token", "message": str(exc)})

        if token:
            for fixture in fixtures:
                match_id = fixture["match_id"]
                try:
                    parsed = self.player_stats_roster(match_id, token)
                    home_count = int((parsed.get("home") or {}).get("raw_selected_count", 0))
                    away_count = int((parsed.get("away") or {}).get("raw_selected_count", 0))
                    if home_count < 18 or away_count < 18:
                        raise RuntimeError(
                            f"player-stats feed did not contain a complete team "
                            f"({fixture['home']} {home_count}, {fixture['away']} {away_count})"
                        )
                    match_rosters[match_id] = parsed
                except Exception as exc:
                    failed.add(match_id)
                    errors.append({"level": "warning", "match_id": match_id, "message": f"AFL player stats: {exc}"})
        else:
            failed = {fixture["match_id"] for fixture in fixtures}

        # Secondary source: the dedicated match-roster feed.  It has been empty
        # for some rounds, so it is used only when the working player-stats route
        # has not supplied a complete side.
        if token and failed:
            still_failed: set[str] = set()
            for fixture in fixtures:
                match_id = fixture["match_id"]
                if match_id not in failed:
                    continue
                try:
                    parsed = self.match_roster(match_id, token)
                    home_count = int((parsed.get("home") or {}).get("raw_selected_count", 0))
                    away_count = int((parsed.get("away") or {}).get("raw_selected_count", 0))
                    if home_count < 18 or away_count < 18:
                        raise RuntimeError(
                            f"incomplete roster ({fixture['home']} {home_count}, {fixture['away']} {away_count})"
                        )
                    match_rosters[match_id] = parsed
                except Exception as exc:
                    still_failed.add(match_id)
                    errors.append({"level": "warning", "match_id": match_id, "message": f"AFL roster fallback: {exc}"})
            failed = still_failed

        usable_matches = len(fixtures) - len(failed)
        # Do not turn a source outage into 41 false player failures. If no match
        # was usable, keep the last good statuses and do not update the timestamp.
        if usable_matches == 0:
            return {
                "afl_matches": fixtures,
                "afl_next_opponents": opponents_from_fixtures(fixtures),
                "team_status": previous_statuses or {},
                "lineup_errors": errors,
                "lineup_failure_message": (
                    "No complete current-round line-ups could be retrieved. Existing team statuses were retained."
                ),
            }

        statuses = build_player_statuses(
            roster,
            fixtures,
            match_rosters,
            checked_at,
            previous_statuses,
            failed_match_ids=failed,
        )
        return {
            "lineups_refreshed_at": checked_at,
            "afl_matches": fixtures,
            "afl_next_opponents": opponents_from_fixtures(fixtures),
            "team_status": statuses,
            "lineup_errors": errors,
            "lineup_source": "Official AFL player-stats feed with AFL roster fallback",
            "lineup_matches_loaded": usable_matches,
        }
