from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

SCHEMA_VERSION = 8
CACHE_PATH = Path(__file__).with_name("fcfc_cache.json")
PERTH_TZ = timezone(timedelta(hours=8))

ROSTER: list[dict[str, str]] = [{'player': 'Jarrod Berry', 'club': 'BRL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-brisbane-lions--jarrod-berry', 'source': 'Squad'}, {'player': 'Sam Draper', 'club': 'BRL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-brisbane-lions--sam-draper', 'source': 'Squad'}, {'player': 'Josh Dunkley', 'club': 'BRL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-brisbane-lions--josh-dunkley', 'source': 'Squad'}, {'player': 'Kai Lohmann', 'club': 'BRL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-brisbane-lions--kai-lohmann', 'source': 'Squad'}, {'player': 'Ben Murphy', 'club': 'BRL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-brisbane-lions--ben-murphy', 'source': 'Squad'}, {'player': 'Blake Acres', 'club': 'CAR', 'profile_url': 'https://www.footywire.com/afl/footy/pp-carlton--blake-acres', 'source': 'Squad'}, {'player': "Harry O'Farrell", 'club': 'CAR', 'profile_url': 'https://www.footywire.com/afl/footy/pp-carlton--harry-ofarrell', 'source': 'Squad'}, {'player': 'Charlie West', 'club': 'COL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-collingwood--charlie-west', 'source': 'Squad'}, {'player': 'Lachlan Blakiston', 'club': 'ESS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-essendon--lachlan-blakiston', 'source': 'Squad'}, {'player': 'Darcy Parish', 'club': 'ESS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-essendon--darcy-parish', 'source': 'Squad'}, {'player': 'Jaxon Prior', 'club': 'ESS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-essendon--jaxon-prior', 'source': 'Squad'}, {'player': 'Jordan Ridley', 'club': 'ESS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-essendon--jordan-ridley', 'source': 'Squad'}, {'player': 'Josh Treacy', 'club': 'FRE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-fremantle--josh-treacy', 'source': 'Squad'}, {'player': 'Cooper Bell', 'club': 'GCS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-gold-coast-suns--cooper-bell', 'source': 'Squad'}, {'player': 'Jack Bowes', 'club': 'GEE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-geelong-cats--jack-bowes', 'source': 'Squad'}, {'player': 'Nick Driscoll', 'club': 'GEE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-geelong-cats--nick-driscoll', 'source': 'Squad'}, {'player': 'Bailey Smith', 'club': 'GEE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-geelong-cats--bailey-smith', 'source': 'Squad'}, {'player': 'Toby Bedford', 'club': 'GWS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-greater-western-sydney-giants--toby-bedford', 'source': 'Squad'}, {'player': 'Darcy Jones', 'club': 'GWS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-greater-western-sydney-giants--darcy-jones', 'source': 'Squad'}, {'player': 'Nick Madden', 'club': 'GWS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-greater-western-sydney-giants--nick-madden', 'source': 'Squad'}, {'player': 'Lachie Whitfield', 'club': 'GWS', 'profile_url': 'https://www.footywire.com/afl/footy/pp-greater-western-sydney-giants--lachie-whitfield', 'source': 'Squad'}, {'player': 'Cameron Nairn', 'club': 'HAW', 'profile_url': 'https://www.footywire.com/afl/footy/pp-hawthorn--cameron-nairn', 'source': 'Squad'}, {'player': 'Xavier Taylor', 'club': 'MEL', 'profile_url': 'https://www.footywire.com/afl/footy/pp-melbourne-demons--xavier-taylor', 'source': 'Squad'}, {'player': 'Charlie Comben', 'club': 'NM', 'profile_url': 'https://www.footywire.com/afl/footy/pp-north-melbourne--charlie-comben', 'source': 'Squad'}, {'player': 'Luke Urquhart', 'club': 'NM', 'profile_url': 'https://www.footywire.com/afl/footy/pp-north-melbourne--luke-urquhart', 'source': 'Squad'}, {'player': 'Josh Sinn', 'club': 'POR', 'profile_url': 'https://www.footywire.com/afl/footy/pp-port-adelaide--josh-sinn', 'source': 'Squad'}, {'player': 'Noah Roberts-Thomson', 'club': 'RIC', 'profile_url': 'https://www.footywire.com/afl/footy/pp-richmond-tigers--noah-roberts-thomson', 'source': 'Squad'}, {'player': 'Max King', 'club': 'STK', 'profile_url': 'https://www.footywire.com/afl/footy/pp-st-kilda-saints--max-king-1', 'source': 'Squad'}, {'player': 'Taylor Adams', 'club': 'SYD', 'profile_url': 'https://www.footywire.com/afl/footy/pp-sydney-swans--taylor-adams', 'source': 'Squad'}, {'player': 'Joel Amartey', 'club': 'SYD', 'profile_url': 'https://www.footywire.com/afl/footy/pp-sydney-swans--joel-amartey', 'source': 'Squad'}, {'player': 'James Jordon', 'club': 'SYD', 'profile_url': 'https://www.footywire.com/afl/footy/pp-sydney-swans--james-jordon', 'source': 'Squad'}, {'player': 'Oskar Baker', 'club': 'WBD', 'profile_url': 'https://www.footywire.com/afl/footy/pp-western-bulldogs--oskar-baker', 'source': 'Squad'}, {'player': 'Adam Treloar', 'club': 'WBD', 'profile_url': 'https://www.footywire.com/afl/footy/pp-western-bulldogs--adam-treloar', 'source': 'Squad'}, {'player': 'Harry Edwards', 'club': 'WCE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-west-coast-eagles--harry-edwards', 'source': 'Squad'}, {'player': 'Finlay Macrae', 'club': 'WCE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-west-coast-eagles--finlay-macrae', 'source': 'Squad'}, {'player': 'Archer Reid', 'club': 'WCE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-west-coast-eagles--archer-reid', 'source': 'Squad'}, {'player': 'Deven Robertson', 'club': 'WCE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-west-coast-eagles--deven-robertson', 'source': 'Squad'}, {'player': 'Brandon Starcevich', 'club': 'WCE', 'profile_url': 'https://www.footywire.com/afl/footy/pp-west-coast-eagles--brandon-starcevich', 'source': 'Squad'}, {'player': "Balyn O'Brien", 'club': 'POR', 'profile_url': 'https://www.footywire.com/afl/footy/pp-port-adelaide--balyn-obrien', 'source': 'Squad'}, {'player': 'Max Mapley', 'club': 'MEL', 'profile_url': '', 'source': 'Player stats only'}, {'player': 'Ben Miller', 'club': 'RIC', 'profile_url': 'https://www.footywire.com/afl/footy/pp-richmond-tigers--ben-miller', 'source': 'Master/Player stats only'}]

ROLE_SLOTS: list[tuple[str, str]] = [
    ("SUPERSTUD", "STUD"),
    ("FWD1", "FWD"),
    ("FWD2", "FWD"),
    ("MID1", "MID"),
    ("MID2", "MID"),
    ("MID3", "MID"),
    ("RUCK", "RUCK"),
    ("MARKER", "MARKER"),
    ("TACKLER", "TACKLER"),
    ("FREE-KICKER", "FREE"),
]
ROLE_TYPES = ["MID", "FWD", "RUCK", "MARKER", "TACKLER", "FREE", "STUD"]

TEAM_NAMES = {
    "ADE": "Adelaide", "BRL": "Brisbane", "CAR": "Carlton", "COL": "Collingwood",
    "ESS": "Essendon", "FRE": "Fremantle", "GCS": "Gold Coast", "GEE": "Geelong",
    "GWS": "GWS", "HAW": "Hawthorn", "MEL": "Melbourne", "NM": "North Melbourne",
    "POR": "Port Adelaide", "RIC": "Richmond", "STK": "St Kilda", "SYD": "Sydney",
    "WBD": "Western Bulldogs", "WCE": "West Coast",
}

TEAM_ALIASES = {
    "adelaide": "ADE", "crows": "ADE", "adelaide crows": "ADE",
    "brisbane": "BRL", "lions": "BRL", "brisbane lions": "BRL",
    "carlton": "CAR", "blues": "CAR", "carlton blues": "CAR",
    "collingwood": "COL", "magpies": "COL", "collingwood magpies": "COL",
    "essendon": "ESS", "bombers": "ESS", "essendon bombers": "ESS",
    "fremantle": "FRE", "dockers": "FRE", "fremantle dockers": "FRE",
    "gold coast": "GCS", "suns": "GCS", "gold coast suns": "GCS",
    "geelong": "GEE", "cats": "GEE", "geelong cats": "GEE",
    "gws": "GWS", "giants": "GWS", "gws giants": "GWS", "greater western sydney": "GWS",
    "hawthorn": "HAW", "hawks": "HAW", "hawthorn hawks": "HAW",
    "melbourne": "MEL", "demons": "MEL", "melbourne demons": "MEL",
    "north melbourne": "NM", "kangaroos": "NM", "north melbourne kangaroos": "NM",
    "port adelaide": "POR", "power": "POR", "port adelaide power": "POR",
    "richmond": "RIC", "tigers": "RIC", "richmond tigers": "RIC",
    "st kilda": "STK", "saints": "STK", "st kilda saints": "STK",
    "sydney": "SYD", "swans": "SYD", "sydney swans": "SYD",
    "western bulldogs": "WBD", "bulldogs": "WBD",
    "west coast": "WCE", "eagles": "WCE", "west coast eagles": "WCE",
}

TEAM_SLUGS = {
    "ADE": ["adelaide-crows"], "BRL": ["brisbane-lions"], "CAR": ["carlton", "carlton-blues"],
    "COL": ["collingwood", "collingwood-magpies"], "ESS": ["essendon", "essendon-bombers"],
    "FRE": ["fremantle", "fremantle-dockers"], "GCS": ["gold-coast-suns"],
    "GEE": ["geelong-cats", "geelong"], "GWS": ["greater-western-sydney-giants", "greater-western-sydney"],
    "HAW": ["hawthorn", "hawthorn-hawks"], "MEL": ["melbourne-demons", "melbourne"],
    "NM": ["north-melbourne", "north-melbourne-kangaroos"], "POR": ["port-adelaide", "port-adelaide-power"],
    "RIC": ["richmond-tigers", "richmond"], "STK": ["st-kilda-saints", "st-kilda"],
    "SYD": ["sydney-swans", "sydney"], "WBD": ["western-bulldogs"], "WCE": ["west-coast-eagles"],
}

BASIC_REQUIRED = {"DESCRIPTION", "DATE", "OPPONENT", "K", "HB"}
ADVANCED_REQUIRED = {"DESCRIPTION", "DATE", "OPPONENT", "CP", "UP"}
PERCENT_STATS = {"DE%", "TOG%"}
META_COLUMNS = {"DESCRIPTION", "DATE", "OPPONENT", "RESULT"}

HEADER_ALIASES = {
    "H": "HB", "H/B": "HB", "H/O": "HO", "HITOUTS": "HO", "HIT OUTS": "HO",
    "FREES FOR": "FF", "FREES AGAINST": "FA", "GOAL ASSISTS": "GA",
    "INSIDE 50": "I50", "REBOUND 50": "R50", "DISPOSALS": "D", "KICKS": "K",
    "HANDBALLS": "HB", "MARKS": "M", "GOALS": "G", "BEHINDS": "B", "TACKLES": "T",
}


def empty_cache(season: int | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "season": season or datetime.now(PERTH_TZ).year,
        "refreshed_at": None,
        "games": [],
        "fixtures": [],
        "next_opponents": {},
        "opponent_overrides": {},
        "errors": [],
        "lineups_refreshed_at": None,
        "afl_matches": [],
        "team_status": {},
        "lineup_errors": [],
    }


def load_cache(path: Path = CACHE_PATH) -> tuple[dict[str, Any], str | None]:
    if not path.exists() or path.stat().st_size == 0:
        return empty_cache(), None
    raw = path.read_bytes()
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            payload = json.loads(raw.decode(encoding))
            if not isinstance(payload, dict):
                raise ValueError("Cache root is not an object")
            incoming_version = int(payload.get("schema_version") or 0)
            base = empty_cache(int(payload.get("season") or datetime.now(PERTH_TZ).year))
            base.update(payload)
            if incoming_version < SCHEMA_VERSION:
                # Parser v6 fixes AFL Tables season-table identification. Do not
                # continue displaying statistics parsed by an older version.
                base["schema_version"] = SCHEMA_VERSION
                base["games"] = []
                base["refreshed_at"] = None
                base["refresh_summary"] = {}
                return base, "Stored statistics were cleared because the AFL Tables parser was upgraded. Run Refresh AFL Tables statistics once."
            return base, None
        except Exception as exc:  # deliberately recover from corrupt uploads
            last_error = exc
    return empty_cache(), f"Cache was unreadable and was safely ignored: {last_error}"


def save_cache(cache: dict[str, Any], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(cache, ensure_ascii=True, indent=2, sort_keys=True)
    temp.write_text(text + "\n", encoding="ascii", newline="\n")
    temp.replace(path)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def normalise_header(value: str) -> str:
    header = clean_text(value).upper().replace(".", "")
    header = re.sub(r"\s+", " ", header)
    return HEADER_ALIASES.get(header, header)


def team_code(value: str) -> str:
    key = clean_text(value).lower().replace(".", "")
    return TEAM_ALIASES.get(key, clean_text(value).upper())


def player_slug(name: str) -> str:
    value = name.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _number(value: str) -> int | float | None:
    text = clean_text(value).replace(",", "").replace("$", "")
    if text in {"", "-", "–", "—", "NA", "N/A"}:
        return None
    text = text.rstrip("%")
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _round_order(description: str) -> int:
    match = re.search(r"ROUND\s+(\d+)", description.upper())
    if match:
        return int(match.group(1))
    finals = {"ELIMINATION FINAL": 25, "QUALIFYING FINAL": 25, "SEMI FINAL": 26,
              "PRELIMINARY FINAL": 27, "GRAND FINAL": 28}
    return finals.get(description.upper(), 99)




def game_sort_key(game: dict[str, Any]) -> tuple[int, int, str]:
    """Return a stable chronological key, recalculating round order from the label.

    Older caches may have missing dates or stale round_order values, so never rely
    solely on those stored fields when selecting recent games.
    """
    season = int(game.get("season", 0) or 0)
    description = str(game.get("description") or game.get("round") or "")
    derived_round = _round_order(description)
    if derived_round == 99:
        try:
            derived_round = int(game.get("round_order", 99) or 99)
        except (TypeError, ValueError):
            derived_round = 99
    return (season, derived_round, str(game.get("date") or ""))


def unique_completed_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one record per actual match, sorted by match date.

    AFL API responses can expose the same player row through more than one nested
    object.  Season averages are often unaffected by duplicate rows, but a last-four
    slice can then contain repeated copies of the newest match and omit the fourth
    distinct match.  Match ID is preferred; date/opponent/round is the fallback.
    """
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for game in games:
        match_id = str(game.get("match_id") or "").strip()
        if match_id:
            key = (str(game.get("player") or ""), match_id)
        else:
            key = (
                str(game.get("player") or ""),
                str(game.get("date") or ""),
                str(game.get("opponent") or ""),
                str(game.get("description") or ""),
            )
        existing = unique.get(key)
        # Keep the row with the greatest number of populated scoring fields.
        richness = sum(1 for k in ("G", "B", "D", "M", "T", "HO", "FF", "K", "HB")
                       if (game.get("stats") or {}).get(k) is not None)
        old_richness = -1 if existing is None else sum(
            1 for k in ("G", "B", "D", "M", "T", "HO", "FF", "K", "HB")
            if (existing.get("stats") or {}).get(k) is not None
        )
        if existing is None or richness > old_richness:
            unique[key] = game

    def chronological(game: dict[str, Any]) -> tuple[str, int, str]:
        date_value = str(game.get("date") or "")
        # ISO dates sort chronologically. Missing dates fall back to round order.
        return (date_value, _round_order(str(game.get("description") or "")), str(game.get("match_id") or ""))

    return sorted(unique.values(), key=chronological)

def _parse_game_date(value: str, season: int) -> str | None:
    text = clean_text(value)
    match = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})", text)
    if not match:
        return None
    try:
        parsed = datetime.strptime(f"{match.group(1)} {match.group(2)} {season}", "%b %d %Y").date()
        return parsed.isoformat()
    except ValueError:
        return None


def _table_rows(table: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"], recursive=False)]
        if not cells:
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def parse_games_html(html: str, player: str, club: str, season: int, advanced: bool = False) -> tuple[list[dict[str, Any]], bool]:
    soup = BeautifulSoup(html, "html.parser")
    required = ADVANCED_REQUIRED if advanced else BASIC_REQUIRED
    best: list[dict[str, Any]] = []
    found_log = bool(re.search(rf"{season}\s+Games Log", soup.get_text(" ", strip=True), re.I))

    for table in soup.find_all("table"):
        rows = _table_rows(table)
        for header_index, cells in enumerate(rows):
            headers = [normalise_header(cell) for cell in cells]
            if not required.issubset(set(headers)):
                continue
            parsed_rows: list[dict[str, Any]] = []
            for values in rows[header_index + 1:]:
                if len(values) < 4:
                    continue
                if len(values) < len(headers):
                    values = values + [""] * (len(headers) - len(values))
                if len(values) > len(headers):
                    values = values[:len(headers) - 1] + [" ".join(values[len(headers) - 1:])]
                row = dict(zip(headers, values))
                description = clean_text(row.get("DESCRIPTION", ""))
                if not (description.upper().startswith("ROUND") or "FINAL" in description.upper()):
                    continue
                record: dict[str, Any] = {
                    "player": player,
                    "club": club,
                    "season": season,
                    "description": description,
                    "round_order": _round_order(description),
                    "date": _parse_game_date(row.get("DATE", ""), season),
                    "opponent": team_code(row.get("OPPONENT", "")),
                    "result": clean_text(row.get("RESULT", "")),
                    "stats": {},
                }
                for header, raw_value in row.items():
                    if header in META_COLUMNS:
                        continue
                    number = _number(raw_value)
                    if number is not None:
                        record["stats"][header] = number
                parsed_rows.append(record)
            if len(parsed_rows) > len(best):
                best = parsed_rows
    return best, found_log


def parse_games_markdown(text: str, player: str, club: str, season: int, advanced: bool = False) -> tuple[list[dict[str, Any]], bool]:
    required = ADVANCED_REQUIRED if advanced else BASIC_REQUIRED
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found_log = any(re.search(rf"{season}\s+Games Log", line, re.I) for line in lines)
    best: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if "|" not in line:
            continue
        headers = [normalise_header(x) for x in line.strip("|").split("|")]
        if not required.issubset(set(headers)):
            continue
        parsed: list[dict[str, Any]] = []
        for data_line in lines[idx + 1:]:
            if "|" not in data_line:
                if parsed:
                    break
                continue
            if re.fullmatch(r"[|:\-\s]+", data_line):
                continue
            values = [clean_text(x) for x in data_line.strip("|").split("|")]
            if len(values) < len(headers):
                continue
            row = dict(zip(headers, values[:len(headers)]))
            description = clean_text(row.get("DESCRIPTION", ""))
            if not (description.upper().startswith("ROUND") or "FINAL" in description.upper()):
                continue
            record = {
                "player": player, "club": club, "season": season,
                "description": description, "round_order": _round_order(description),
                "date": _parse_game_date(row.get("DATE", ""), season),
                "opponent": team_code(row.get("OPPONENT", "")),
                "result": clean_text(row.get("RESULT", "")), "stats": {},
            }
            for header, raw_value in row.items():
                if header in META_COLUMNS:
                    continue
                number = _number(raw_value)
                if number is not None:
                    record["stats"][header] = number
            parsed.append(record)
        if len(parsed) > len(best):
            best = parsed
    return best, found_log


def _query_url(url: str, **params: Any) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query.update({key: str(value) for key, value in params.items() if value is not None})
    return urlunparse(parts._replace(query=urlencode(query)))


def _normalise_player_name(value: str) -> str:
    value = clean_text(value).lower().replace("’", "'")
    value = value.replace("'", "")
    return re.sub(r"[^a-z0-9]+", "", value)


def _afl_tables_fallback_url(player_name: str) -> str:
    safe = player_name.replace("’", "'").replace("'", "")
    parts = [part for part in re.split(r"\s+", safe.strip()) if part]
    filename = "_".join(parts) + ".html"
    initial = parts[0][0].upper() if parts else "A"
    return f"https://afltables.com/afl/stats/players/{initial}/{filename}"


@dataclass
class FetchResult:
    text: str
    kind: str
    source_url: str


class StatsClient:
    """Fail-fast HTTP client used for AFL Tables and fixture retrieval."""

    def __init__(self, timeout: tuple[int, int] = (3, 10)):
        self.timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; FCFC-Squad-Tracker/4.0; personal weekly statistics refresh)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
            "Connection": "close",
        }
        response = requests.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("The page returned no content")
        response.encoding = response.apparent_encoding or "utf-8"
        return FetchResult(response.text, "html", url)


def resolve_afl_tables_urls(client: StatsClient, season: int) -> dict[str, str]:
    """Resolve current player pages from the AFL Tables season index in one request."""
    fetched = client.fetch(f"https://afltables.com/afl/stats/{season}.html")
    soup = BeautifulSoup(fetched.text, "html.parser")
    resolved: dict[str, str] = {}
    for link in soup.find_all("a", href=True):
        href = clean_text(link.get("href", ""))
        name = clean_text(link.get_text(" ", strip=True))
        if "/stats/players/" not in href and not re.search(r"stats/players/[A-Z]/", href):
            continue
        if not name:
            continue
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = "https://afltables.com" + href
        else:
            url = "https://afltables.com/afl/stats/" + href.lstrip("./")
        resolved[_normalise_player_name(name)] = url
    return resolved


AFL_TABLES_HEADER_MAP = {
    "KI": "K", "MK": "M", "HB": "HB", "DI": "D", "GL": "G", "BH": "B",
    "HO": "HO", "TK": "T", "FF": "FF", "FA": "FA", "GA": "GA", "CP": "CP",
    "UP": "UP", "CM": "CM", "MI": "MI", "IF": "I50", "CL": "CL", "CG": "CG",
    "RB": "RB", "BR": "BR", "1%": "1%", "BO": "BO", "%P": "TOG%",
}


def _afl_round_description(value: str) -> str:
    text = clean_text(value).upper()
    finals = {"EF": "Elimination Final", "QF": "Qualifying Final", "SF": "Semi Final",
              "PF": "Preliminary Final", "GF": "Grand Final"}
    if text in finals:
        return finals[text]
    match = re.search(r"\d+", text)
    return f"Round {int(match.group())}" if match else text.title()


def _afl_tables_table_year(table: Any) -> int | None:
    """Identify the season belonging to an AFL Tables game table.

    Match links inside each season table contain the season in their URL and are
    the most reliable signal. The nearest preceding year label is used only as a
    fallback. This avoids selecting a longer historical season table merely
    because the requested year appears elsewhere on the page.
    """
    linked_years: list[int] = []
    for link in table.find_all("a", href=True):
        href = clean_text(link.get("href", ""))
        for match in re.finditer(r"(?:^|/)((?:19|20)\d{2})(?:/|$)", href):
            linked_years.append(int(match.group(1)))
    if linked_years:
        counts = {year: linked_years.count(year) for year in set(linked_years)}
        return max(counts, key=counts.get)

    cursor = table
    for _ in range(40):
        cursor = cursor.find_previous(["a", "b", "strong", "h1", "h2", "h3", "h4", "caption", "font"])
        if cursor is None:
            break
        text = clean_text(cursor.get_text(" ", strip=True))
        match = re.fullmatch(r"((?:19|20)\d{2})(?:\s+Games)?", text, re.I)
        if match:
            return int(match.group(1))
    return None


def parse_afl_tables_player_html(html: str, player: str, club: str, season: int) -> tuple[list[dict[str, Any]], bool]:
    """Parse exactly the requested season's game-by-game AFL Tables table."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[list[dict[str, Any]]] = []
    found = False
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if not rows:
            continue
        header_idx = None
        headers: list[str] = []
        for idx, row in enumerate(rows[:4]):
            upper = [clean_text(x).upper() for x in row]
            if {"GM", "OPPONENT", "RD", "KI", "MK", "HB", "DI"}.issubset(set(upper)):
                header_idx = idx
                headers = upper
                break
        if header_idx is None:
            continue

        table_year = _afl_tables_table_year(table)
        if table_year != season:
            continue
        found = True
        parsed: list[dict[str, Any]] = []
        for values in rows[header_idx + 1:]:
            if len(values) < len(headers):
                values = values + [""] * (len(headers) - len(values))
            row = dict(zip(headers, values[:len(headers)]))
            opponent = clean_text(row.get("OPPONENT", ""))
            if not opponent or opponent.lower() == "totals":
                continue
            rd = clean_text(row.get("RD", ""))
            if not rd:
                continue
            description = _afl_round_description(rd)
            record: dict[str, Any] = {
                "player": player,
                "club": club,
                "season": season,
                "description": description,
                "round_order": _round_order(description),
                "date": None,
                "opponent": team_code(opponent),
                "result": clean_text(row.get("R", "")),
                "stats": {},
                "source": "AFL Tables",
            }
            for source_header, target_header in AFL_TABLES_HEADER_MAP.items():
                number = _number(row.get(source_header, ""))
                if number is not None:
                    record["stats"][target_header] = number
            for source_header, raw_value in row.items():
                if source_header in {"GM", "OPPONENT", "RD", "R", "#"}:
                    continue
                number = _number(raw_value)
                if number is not None:
                    record["stats"].setdefault(AFL_TABLES_HEADER_MAP.get(source_header, source_header), number)
            parsed.append(record)
        candidates.append(parsed)

    if not candidates:
        return [], found
    # There should be one table for the season. If AFL Tables duplicates it,
    # prefer the candidate with the highest latest round, then the most rows.
    candidates.sort(key=lambda rows: (max((game_sort_key(g)[1] for g in rows), default=-1), len(rows)))
    return candidates[-1], found


def fetch_player_games(
    client: StatsClient,
    player: dict[str, str],
    season: int,
    include_advanced: bool = False,
    resolved_urls: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Fetch one AFL Tables player page. All published raw fields arrive in one request."""
    del include_advanced
    resolved_urls = resolved_urls or {}
    key = _normalise_player_name(player["player"])
    url = resolved_urls.get(key) or _afl_tables_fallback_url(player["player"])
    fetched = client.fetch(url)
    games, found = parse_afl_tables_player_html(
        fetched.text, player["player"], player["club"], season
    )
    if not found:
        raise RuntimeError("AFL Tables player page was found, but the current-season game table was not located")
    warnings: list[str] = []
    if not games:
        warnings.append("AFL Tables lists no senior games for this player in the selected season.")
    return games, url, warnings

def parse_fixture_html(html: str, season: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    fixtures: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        for cells in _table_rows(table):
            lowered = [cell.lower() for cell in cells]
            try:
                v_index = lowered.index("v")
            except ValueError:
                continue
            if v_index < 1 or v_index + 1 >= len(cells):
                continue
            home = team_code(cells[v_index - 1])
            away = team_code(cells[v_index + 1])
            if home not in TEAM_NAMES or away not in TEAM_NAMES:
                continue
            date_text = cells[0]
            match = re.search(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+([A-Z][a-z]{2})", date_text)
            if not match:
                continue
            try:
                match_date = datetime.strptime(f"{match.group(1)} {match.group(2)} {season}", "%d %b %Y").date()
            except ValueError:
                continue
            result = next((cell for cell in cells if re.fullmatch(r"\d{1,3}-\d{1,3}", cell)), "")
            fixtures.append({
                "date": match_date.isoformat(), "home": home, "away": away,
                "played": bool(result), "result": result,
            })
    unique = {(f["date"], f["home"], f["away"]): f for f in fixtures}
    return sorted(unique.values(), key=lambda f: (f["date"], f["home"], f["away"]))


def next_opponents(fixtures: Iterable[dict[str, Any]], as_of: date | None = None) -> dict[str, str]:
    as_of = as_of or datetime.now(PERTH_TZ).date()
    future = sorted(
        [f for f in fixtures if f.get("date") and date.fromisoformat(f["date"]) >= as_of and not f.get("played")],
        key=lambda f: f["date"],
    )
    result: dict[str, str] = {}
    for fixture in future:
        result.setdefault(fixture["home"], fixture["away"])
        result.setdefault(fixture["away"], fixture["home"])
    return result


def fetch_fixtures(client: StatsClient, season: int) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://www.footywire.com/afl/footy/ft_match_list?year={season}"
    try:
        fetched = client.fetch(url)
        if fetched.kind != "html":
            return [], "Fixture fallback returned text rather than HTML; opponents can be entered manually."
        fixtures = parse_fixture_html(fetched.text, season)
        if not fixtures:
            return [], "No fixture rows could be parsed; opponents can be entered manually."
        return fixtures, None
    except Exception as exc:
        return [], str(exc)


def _canon(value: Any) -> str:
    import unicodedata
    text = unicodedata.normalize('NFKD', str(value or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]', '', text)


def _walk_dict(data: Any, prefix: str = '') -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f'{prefix}.{k}' if prefix else str(k)
            if isinstance(v, dict): out.update(_walk_dict(v, key))
            else: out[key.lower()] = v
    return out


def _player_name(item: dict[str, Any]) -> str:
    flat = _walk_dict(item)
    for k in ('player.playername.fullname','player.fullname','player.displayname','player.name','playername.fullname','fullname','displayname','name'):
        v = flat.get(k)
        if isinstance(v, str) and ' ' in v.strip(): return v.strip()
    first = flat.get('player.playername.givenname') or flat.get('player.firstname') or flat.get('playername.givenname') or flat.get('givenname') or flat.get('firstname')
    last = flat.get('player.playername.surname') or flat.get('player.surname') or flat.get('playername.surname') or flat.get('surname') or flat.get('lastname')
    return f'{first} {last}'.strip() if first and last else ''


def _official_stats(item: dict[str, Any]) -> dict[str, float]:
    flat = _walk_dict(item)
    aliases = {'K':['playerstats.kicks','stats.kicks','kicks'], 'HB':['playerstats.handballs','stats.handballs','handballs'], 'D':['playerstats.disposals','stats.disposals','disposals'], 'M':['playerstats.marks','stats.marks','marks'], 'G':['playerstats.goals','stats.goals','goals'], 'B':['playerstats.behinds','stats.behinds','behinds'], 'T':['playerstats.tackles','stats.tackles','tackles'], 'HO':['playerstats.hitouts','stats.hitouts','hitouts'], 'FF':['playerstats.freesfor','stats.freesfor','freesfor']}
    result: dict[str,float] = {}
    for target, keys in aliases.items():
        for key in keys:
            if key in flat:
                n = _number(flat[key])
                if n is not None: result[target] = n; break
    if 'D' not in result and ('K' in result or 'HB' in result): result['D'] = result.get('K',0)+result.get('HB',0)
    return result


def _looks_like_stat_item(item: dict[str, Any]) -> bool:
    flat = _walk_dict(item)
    has_player = bool(_player_name(item))
    stat_markers = (
        "playerstats.disposals", "stats.disposals", "disposals",
        "playerstats.kicks", "stats.kicks", "kicks",
        "playerstats.tackles", "stats.tackles", "tackles",
    )
    return has_player and any(key in flat for key in stat_markers)


def _stat_items(payload: Any) -> list[dict[str, Any]]:
    """Recursively locate player-stat records in AFL API responses.

    The official response shape has changed over time. Some responses expose
    homeTeamPlayerStats/awayTeamPlayerStats as top-level arrays; others wrap
    those arrays in one or more objects. This walker handles both without
    relying on one fixed nesting level.
    """
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for value in node:
                visit(value)
            return
        if not isinstance(node, dict):
            return
        identity = id(node)
        if identity in seen:
            return
        seen.add(identity)
        if _looks_like_stat_item(node):
            found.append(node)
            return
        for value in node.values():
            if isinstance(value, (dict, list)):
                visit(value)

    visit(payload)
    return found


def _fetch_match_stats(session: requests.Session, match_id: str, token: str, timeout: int = 18) -> dict[str, Any]:
    url = f"https://api.afl.com.au/cfs/afl/playerStats/match/{match_id}"
    headers = {
        "x-media-mis-token": token,
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.afl.com.au",
        "Referer": f"https://www.afl.com.au/afl/matches/{match_id}",
    }
    response = session.get(url, headers=headers, timeout=timeout)
    if response.status_code in {401, 403}:
        raise RuntimeError(f"AFL stats request was rejected ({response.status_code})")
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"AFL stats endpoint returned {response.headers.get('content-type', 'unknown content')} rather than JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"AFL stats endpoint returned {type(payload).__name__}, not an object")
    return payload

def refresh_all(season: int | None = None, progress: Callable[[int,int,str,str],None] | None = None, roster: list[dict[str,str]] | None = None, previous_cache: dict[str,Any] | None = None, include_advanced: bool = False, max_workers: int = 8) -> dict[str, Any]:
    del include_advanced
    from afl_lineups import AFLLineupClient, parse_match

    season = 2026
    roster = roster or ROSTER
    previous_cache = previous_cache or empty_cache(season)
    client = AFLLineupClient(timeout=18)
    comp = client.competition_id()
    sid = client.season_id(season, comp)
    match_payload = client._get(
        "https://aflapi.afl.com.au/afl/v2/matches",
        {"competitionId": comp, "compSeasonId": sid, "roundNumber": "", "pageSize": 1000},
    )
    now = datetime.now(timezone.utc)
    matches: list[dict[str, Any]] = []
    for raw in match_payload.get("matches") or []:
        if not isinstance(raw, dict):
            continue
        match = parse_match(raw)
        if not match:
            continue
        start = datetime.fromisoformat(match["start_utc"].replace("Z", "+00:00"))
        status = str(match.get("status") or "").lower()
        completed = status in {"completed", "complete", "concluded", "full time", "fulltime", "final"}
        if completed or start <= now - timedelta(hours=3):
            match["date"] = start.date().isoformat()
            matches.append(match)

    token = client.token()
    lookup = {_canon(player["player"]): player for player in roster}
    games: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    session = client.session

    def fetch(match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return match, _fetch_match_stats(session, match["match_id"], token, timeout=18)

    workers = min(max_workers, max(1, len(matches)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch, match): match for match in matches}
        done = 0
        for future in as_completed(futures):
            done += 1
            match = futures[future]
            label = match.get("round_name") or f"Match {match.get('match_id', '')}"
            try:
                fetched_match, payload = future.result()
                items = _stat_items(payload)
                matched_count = 0
                for item in items:
                    official_name = _player_name(item)
                    roster_player = lookup.get(_canon(official_name))
                    stats = _official_stats(item)
                    if not roster_player or not stats:
                        continue
                    club = roster_player["club"]
                    home = fetched_match["home"]
                    away = fetched_match["away"]
                    opponent = away if club == home else home if club == away else ""
                    games.append({
                        "player": roster_player["player"],
                        "club": club,
                        "season": season,
                        "description": fetched_match.get("round_name", ""),
                        "round_order": _round_order(fetched_match.get("round_name", "")),
                        "date": fetched_match["date"],
                        "match_id": str(fetched_match.get("match_id") or ""),
                        "opponent": opponent,
                        "result": "",
                        "stats": stats,
                        "source": "AFL.com.au",
                    })
                    matched_count += 1
                if not items:
                    errors.append({
                        "scope": label,
                        "level": "warning",
                        "message": "The AFL endpoint returned JSON but no player-stat records were located.",
                    })
                elif matched_count == 0:
                    sample_names = [_player_name(item) for item in items[:5] if _player_name(item)]
                    errors.append({
                        "scope": label,
                        "level": "warning",
                        "message": f"Found {len(items)} official player rows but none matched the FCFC squad. Sample: {', '.join(sample_names) or 'names unavailable'}",
                    })
                if progress:
                    progress(done, len(matches), label, f"{len(items)} official rows; {matched_count} squad matches")
            except Exception as exc:
                errors.append({"scope": label, "level": "error", "message": str(exc)})
                if progress:
                    progress(done, len(matches), label, f"Error: {exc}")

    games = unique_completed_games(games)
    result = dict(previous_cache)
    successful_players = len({game["player"] for game in games})
    summary = {
        "completed_matches_checked": len(matches),
        "successful_players": successful_players,
        "failed_matches": sum(1 for error in errors if error.get("level") == "error"),
        "warnings": sum(1 for error in errors if error.get("level") == "warning"),
        "current_season_game_records": len(games),
    }
    result.update({
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "errors": errors,
        "data_source": "AFL.com.au",
        "refresh_summary": summary,
    })
    if games:
        result["games"] = games
        result["refreshed_at"] = datetime.now(PERTH_TZ).isoformat(timespec="seconds")
        result.pop("refresh_failed_at", None)
    else:
        result["refresh_failed_at"] = datetime.now(PERTH_TZ).isoformat(timespec="seconds")
        result["refresh_failure_message"] = (
            "The AFL refresh completed but produced zero usable squad records. Existing stored games were retained."
        )
    return result

def stat(stats: dict[str, Any], key: str) -> float:
    value = stats.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def role_score(stats: dict[str, Any], role: str, floor_stud: bool = True) -> float:
    role = role.upper()
    if role == "MID":
        return stat(stats, "K") + stat(stats, "HB")
    if role == "FWD":
        return 10 * stat(stats, "G") + stat(stats, "B")
    if role == "RUCK":
        return stat(stats, "HO") + stat(stats, "M")
    if role == "MARKER":
        return 3 * stat(stats, "M")
    if role == "TACKLER":
        return 5 * stat(stats, "T")
    if role == "FREE":
        return 6 * stat(stats, "FF")
    if role == "STUD":
        raw = (
            10 * stat(stats, "G") + stat(stats, "B") + stat(stats, "D")
            + 3 * stat(stats, "M") + 5 * stat(stats, "T") + 6 * stat(stats, "FF")
        ) / 2.5
        return float(math.floor(raw)) if floor_stud else raw
    raise ValueError(f"Unknown role: {role}")


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _recent_average(games: list[dict[str, Any]], role: str) -> float:
    """Recency-weighted average of the latest four distinct completed games.

    The oldest-to-newest weights are 10%, 20%, 30% and 40%. If fewer than
    four games are available, the matching most-recent weights are normalised.
    """
    latest = unique_completed_games(games)[-4:]
    if not latest:
        return 0.0
    base_weights = [0.1, 0.2, 0.3, 0.4][-len(latest):]
    weight_total = sum(base_weights)
    return sum(
        role_score(game.get("stats", {}), role) * weight
        for game, weight in zip(latest, base_weights)
    ) / weight_total


def _baseline_by_player(games: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        grouped[game["player"]].append(game)
    result: dict[tuple[str, str], float] = {}
    for player, player_games in grouped.items():
        for role in ROLE_TYPES:
            result[(player, role)] = _mean(role_score(g.get("stats", {}), role) for g in player_games)
    return result


def _cohort_factors(games: list[dict[str, Any]], baselines: dict[tuple[str, str], float]) -> dict[tuple[str, str], tuple[float, int]]:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for game in games:
        opponent = game.get("opponent", "")
        for role in ROLE_TYPES:
            baseline = baselines.get((game["player"], role), 0)
            if baseline <= 0:
                continue
            ratio = role_score(game.get("stats", {}), role) / baseline
            values[(opponent, role)].append(min(1.5, max(0.5, ratio)))
    result: dict[tuple[str, str], tuple[float, int]] = {}
    for key, ratios in values.items():
        raw = _mean(ratios)
        n = len(ratios)
        shrunk = 1 + (raw - 1) * min(0.35, n / 30)
        result[key] = (min(1.08, max(0.92, shrunk)), n)
    return result


def opponent_adjustment(
    player: str,
    role: str,
    opponent: str,
    games: list[dict[str, Any]],
    baseline: float,
    cohort: dict[tuple[str, str], tuple[float, int]],
) -> tuple[float, int, int]:
    cohort_factor, cohort_n = cohort.get((opponent, role), (1.0, 0))
    if not opponent or baseline <= 0:
        return cohort_factor, 0, cohort_n
    history = [g for g in games if g["player"] == player and g.get("opponent") == opponent]
    if not history:
        return cohort_factor, 0, cohort_n
    ratio = _mean(role_score(g.get("stats", {}), role) for g in history) / baseline
    n = len(history)
    individual = 1 + (ratio - 1) * n / (n + 3)
    individual = min(1.15, max(0.85, individual))
    combined = 0.75 * individual + 0.25 * cohort_factor
    return combined, n, cohort_n


def build_projections(
    games: list[dict[str, Any]],
    season: int,
    opponents: dict[str, str] | None = None,
    recent_weight: float = 0.65,
    unavailable: set[str] | None = None,
) -> list[dict[str, Any]]:
    opponents = opponents or {}
    unavailable = unavailable or set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        grouped[game["player"]].append(game)
    roster_lookup = {p["player"]: p for p in ROSTER}
    projections: list[dict[str, Any]] = []

    for player, all_games in grouped.items():
        if player in unavailable:
            continue
        # Recommendations are deliberately based only on the selected season.
        current = [g for g in all_games if int(g.get("season", 0)) == season]
        if not current:
            continue
        club = roster_lookup.get(player, {}).get("club", current[-1].get("club", ""))
        opponent = opponents.get(club, "")
        details: dict[str, Any] = {}
        role_scores: dict[str, float] = {}
        for role in ROLE_TYPES:
            season_avg = _mean(role_score(g.get("stats", {}), role) for g in current)
            recent_avg = _recent_average(current, role)
            expected = max(0.0, recent_weight * recent_avg + (1 - recent_weight) * season_avg)
            role_scores[role] = expected
            details[role] = {
                "season_avg": season_avg,
                "recent_avg": recent_avg,
            }
        projections.append({
            "player": player, "club": club, "opponent": opponent,
            "current_games": len(current), "prior_games": 0,
            "role_scores": role_scores, "details": details,
        })
    return sorted(projections, key=lambda p: p["player"])


def optimise_team(projections: list[dict[str, Any]]) -> dict[str, Any]:
    slot_count = len(ROLE_SLOTS)
    if len(projections) < slot_count:
        return {"starters": [], "interchange": [], "projected_total": 0.0,
                "reason": f"Only {len(projections)} players have usable data; {slot_count} are required."}

    # Exact dynamic programming assignment: each player may fill at most one slot.
    states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, tuple([-1] * slot_count))}
    for player_index, projection in enumerate(projections):
        updated = dict(states)
        for mask, (score, assignment) in states.items():
            for slot_index, (_, role) in enumerate(ROLE_SLOTS):
                bit = 1 << slot_index
                if mask & bit:
                    continue
                new_mask = mask | bit
                new_score = score + float(projection["role_scores"].get(role, 0.0))
                previous = updated.get(new_mask)
                if previous is None or new_score > previous[0] + 1e-9:
                    new_assignment = list(assignment)
                    new_assignment[slot_index] = player_index
                    updated[new_mask] = (new_score, tuple(new_assignment))
        states = updated

    full_mask = (1 << slot_count) - 1
    if full_mask not in states:
        return {"starters": [], "interchange": [], "projected_total": 0.0, "reason": "No valid assignment was found."}
    total, assignment = states[full_mask]
    starters: list[dict[str, Any]] = []
    selected: set[str] = set()
    role_benchmarks: dict[str, list[float]] = defaultdict(list)
    for slot_index, player_index in enumerate(assignment):
        slot, role = ROLE_SLOTS[slot_index]
        projection = projections[player_index]
        expected = projection["role_scores"][role]
        detail = projection["details"][role]
        starters.append({
            "position": slot, "role": role, "player": projection["player"], "club": projection["club"],
            "opponent": projection["opponent"], "expected_score": expected,
            "season_average": detail["season_avg"],
            "recent_average": detail["recent_avg"],
        })
        selected.add(projection["player"])
        role_benchmarks[role].append(expected)

    remaining = [p for p in projections if p["player"] not in selected]
    candidates: list[dict[str, Any]] = []
    for projection in remaining:
        best_role = "MID"
        best_ratio = -1.0
        best_score = 0.0
        broad_coverage = 0
        for role in ROLE_TYPES:
            score = projection["role_scores"].get(role, 0.0)
            benchmark = _mean(role_benchmarks.get(role, [score or 1.0])) or 1.0
            ratio = score / benchmark
            if ratio >= 0.75:
                broad_coverage += 1
            if (ratio, score) > (best_ratio, best_score):
                best_ratio, best_score, best_role = ratio, score, role
        candidates.append({
            "player": projection["player"], "club": projection["club"], "opponent": projection["opponent"],
            "preferred_role": best_role, "expected_if_used": best_score,
            "coverage_ratio": best_ratio, "broad_coverage": broad_coverage,
        })

    interchange: list[dict[str, Any]] = []
    covered_roles: set[str] = set()
    for bench_index in range(4):
        if not candidates:
            break
        def bench_key(candidate: dict[str, Any]) -> tuple[float, float, str]:
            diversity = 0.08 if candidate["preferred_role"] not in covered_roles else 0.0
            flexibility = 0.015 * candidate["broad_coverage"]
            return candidate["coverage_ratio"] + diversity + flexibility, candidate["expected_if_used"], candidate["player"]
        chosen = max(candidates, key=bench_key)
        candidates.remove(chosen)
        covered_roles.add(chosen["preferred_role"])
        interchange.append({"position": f"INT{bench_index + 1}", **chosen})

    return {"starters": starters, "interchange": interchange, "projected_total": total, "reason": None}


def flatten_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    all_stats = sorted({key for game in games for key in game.get("stats", {})})
    for game in games:
        row = {
            "Player": game.get("player"), "Club": game.get("club"), "Season": game.get("season"),
            "Round": game.get("description"), "Date": game.get("date"), "Opponent": game.get("opponent"),
            "Result": game.get("result"),
        }
        row.update({key: game.get("stats", {}).get(key) for key in all_stats})
        rows.append(row)
    return rows


def season_summary(games: list[dict[str, Any]], season: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current = [game for game in games if int(game.get("season", 0)) == season]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    all_stats = sorted({key for game in current for key in game.get("stats", {})})
    for game in current:
        grouped[(game["player"], game["club"])].append(game)
    totals: list[dict[str, Any]] = []
    averages: list[dict[str, Any]] = []
    for (player, club), player_games in sorted(grouped.items()):
        total_row: dict[str, Any] = {"Player": player, "Club": club, "Games": len(player_games)}
        average_row: dict[str, Any] = {"Player": player, "Club": club, "Games": len(player_games)}
        for key in all_stats:
            values = [float(g.get("stats", {}).get(key)) for g in player_games if isinstance(g.get("stats", {}).get(key), (int, float))]
            total_row[key] = round(_mean(values), 2) if key in PERCENT_STATS else (sum(values) if values else None)
            average_row[key] = round(_mean(values), 2) if values else None
        totals.append(total_row)
        averages.append(average_row)
    return totals, averages


def effective_opponents(cache: dict[str, Any]) -> dict[str, str]:
    auto = dict(cache.get("next_opponents") or {})
    overrides = dict(cache.get("opponent_overrides") or {})
    for club, opponent in overrides.items():
        if opponent:
            auto[club] = opponent
    return auto


def most_recent_monday(now: datetime | None = None) -> datetime:
    now = now or datetime.now(PERTH_TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday


def cache_is_stale(cache: dict[str, Any], now: datetime | None = None) -> bool:
    refreshed = cache.get("refreshed_at")
    if not refreshed:
        return True
    try:
        stamp = datetime.fromisoformat(refreshed)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=PERTH_TZ)
    except Exception:
        return True
    return stamp < most_recent_monday(now)
