from __future__ import annotations

from datetime import datetime, timedelta, timezone


import difflib
import re
import unicodedata
from typing import Any


PASTE_ORDERED_SLOTS = [
    "SUPERSTUD", "FWD1", "FWD2", "MID1", "MID2", "MID3",
    "RUCK", "MARKER", "TACKLER", "FREE-KICKER",
    "INT1", "INT2", "INT3", "INT4",
]

PASTE_SLOT_ALIASES = {
    "superstud": "SUPERSTUD", "super stud": "SUPERSTUD", "stud": "SUPERSTUD", "ss": "SUPERSTUD",
    "forward 1": "FWD1", "fwd 1": "FWD1", "fwd1": "FWD1",
    "forward 2": "FWD2", "fwd 2": "FWD2", "fwd2": "FWD2",
    "midfielder 1": "MID1", "mid 1": "MID1", "mid1": "MID1",
    "midfielder 2": "MID2", "mid 2": "MID2", "mid2": "MID2",
    "midfielder 3": "MID3", "mid 3": "MID3", "mid3": "MID3",
    "ruck": "RUCK", "ruckman": "RUCK",
    "marker": "MARKER",
    "tackler": "TACKLER",
    "free kicker": "FREE-KICKER", "free-kicker": "FREE-KICKER",
    "soft arsed free kicker": "FREE-KICKER", "soft-arsed free-kicker": "FREE-KICKER",
    "int 1": "INT1", "int1": "INT1", "interchange 1": "INT1", "bench 1": "INT1", "reserve 1": "INT1",
    "int 2": "INT2", "int2": "INT2", "interchange 2": "INT2", "bench 2": "INT2", "reserve 2": "INT2",
    "int 3": "INT3", "int3": "INT3", "interchange 3": "INT3", "bench 3": "INT3", "reserve 3": "INT3",
    "int 4": "INT4", "int4": "INT4", "interchange 4": "INT4", "bench 4": "INT4", "reserve 4": "INT4",
}

PASTE_GROUP_ALIASES = {
    "forwards": ["FWD1", "FWD2"], "forward": ["FWD1", "FWD2"], "fwds": ["FWD1", "FWD2"],
    "midfielders": ["MID1", "MID2", "MID3"], "midfield": ["MID1", "MID2", "MID3"],
    "mids": ["MID1", "MID2", "MID3"],
    "interchange": ["INT1", "INT2", "INT3", "INT4"], "bench": ["INT1", "INT2", "INT3", "INT4"],
    "reserves": ["INT1", "INT2", "INT3", "INT4"],
}

PASTE_ROLE_MAP = {
    "superstud": "SUPERSTUD", "stud": "SUPERSTUD",
    "forward": "FORWARD", "fwd": "FORWARD",
    "midfielder": "MIDFIELDER", "midfield": "MIDFIELDER", "mid": "MIDFIELDER",
    "ruck": "RUCK", "ruckman": "RUCK",
    "marker": "MARKER", "tackler": "TACKLER",
    "free kicker": "FREE-KICKER", "free-kicker": "FREE-KICKER", "free": "FREE-KICKER",
}


def _paste_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _clean_paste_label(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("_", " ")
    text = re.sub(r"[^a-z0-9 -]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_names(value: str) -> list[str]:
    value = re.sub(r"\s+[|/]\s+", ",", value)
    value = re.sub(r"\s{2,}", ",", value)
    parts = [part.strip(" -–—:;\t") for part in re.split(r"[,;\n]+", value)]
    return [part for part in parts if part]


def _strip_club_and_role(value: str, known_clubs: set[str]) -> tuple[str, str, str]:
    text = value.strip(" -–—:;\t")
    preferred_role = ""
    club = ""
    bracket_bits = re.findall(r"[\[(]([^\])]+)[\])]", text)
    for bit in bracket_bits:
        cleaned = _clean_paste_label(bit)
        role = PASTE_ROLE_MAP.get(cleaned)
        if role:
            preferred_role = role
        club_key = re.sub(r"[^A-Z]", "", bit.upper())
        if club_key in known_clubs:
            club = club_key
    text = re.sub(r"[\[(][^\])]+[\])]", "", text).strip()
    columns = [part.strip() for part in re.split(r"\t|\s+\|\s+", text) if part.strip()]
    if len(columns) >= 2:
        last_key = re.sub(r"[^A-Z]", "", columns[-1].upper())
        if last_key in known_clubs:
            club = last_key
            text = " ".join(columns[:-1]).strip()
    return text, club, preferred_role


def _resolve_pasted_name(value: str, directory: list[dict[str, str]]) -> tuple[str, str, float]:
    target = _paste_key(value)
    if not target:
        return "", "", 0.0
    exact = [item for item in directory if _paste_key(item.get("player")) == target]
    if len(exact) == 1:
        return exact[0].get("player", ""), exact[0].get("club", ""), 1.0

    words = re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKD", value))
    if len(words) >= 2:
        first_initial = words[0][0].lower()
        surname = _paste_key(words[-1])
        initial_matches = []
        for item in directory:
            candidate_words = re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKD", item.get("player", "")))
            if len(candidate_words) >= 2 and candidate_words[0][0].lower() == first_initial and _paste_key(candidate_words[-1]) == surname:
                initial_matches.append(item)
        if len(initial_matches) == 1:
            item = initial_matches[0]
            return item.get("player", ""), item.get("club", ""), 0.94

    scored = []
    for item in directory:
        candidate = item.get("player", "")
        score = difflib.SequenceMatcher(None, target, _paste_key(candidate)).ratio()
        scored.append((score, candidate, item.get("club", "")))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.86 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
        score, candidate, club = scored[0]
        return candidate, club, score
    return "", "", 0.0


def parse_pasted_team(
    text: str,
    directory: list[dict[str, str]],
    *,
    opponent: bool,
    known_clubs: set[str],
) -> tuple[dict[str, object], list[str]]:
    """Parse labelled, tabular or one-name-per-line submitted teams."""
    result: dict[str, object] = {}
    unresolved: list[str] = []
    raw_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    lines = []
    for line in raw_lines:
        cleaned = _clean_paste_label(line)
        if cleaned in {
            "my team", "my submitted team", "opponent", "opponents team",
            "opponent submitted team", "submitted team", "team",
            "position player club", "position player",
        }:
            continue
        lines.append(line)

    leftovers: list[str] = []
    for line in lines:
        # Accept tabs, colons, pipes and dash-separated rows.
        match = re.match(r"^\s*([^:\t|–—]+?)\s*(?::|\t|\||\s+[–—-]\s+)\s*(.+?)\s*$", line)
        label = _clean_paste_label(match.group(1)) if match else ""
        value = match.group(2).strip() if match else line.strip()

        if label in PASTE_GROUP_ALIASES:
            names = _split_names(value)
            for slot, name_text in zip(PASTE_GROUP_ALIASES[label], names):
                cleaned_name, explicit_club, preferred_role = _strip_club_and_role(name_text, known_clubs)
                player, auto_club, _ = _resolve_pasted_name(cleaned_name, directory)
                if not player:
                    unresolved.append(cleaned_name)
                    continue
                if slot.startswith("INT"):
                    result[slot] = {"player": player, "preferred_role": preferred_role, **({"club": explicit_club or auto_club} if opponent else {})}
                elif opponent:
                    result[slot] = {"player": player, "club": explicit_club or auto_club}
                else:
                    result[slot] = player
            continue

        slot = PASTE_SLOT_ALIASES.get(label)
        if slot:
            cleaned_name, explicit_club, preferred_role = _strip_club_and_role(value, known_clubs)
            player, auto_club, _ = _resolve_pasted_name(cleaned_name, directory)
            if not player:
                unresolved.append(cleaned_name)
                continue
            if slot.startswith("INT"):
                result[slot] = {"player": player, "preferred_role": preferred_role, **({"club": explicit_club or auto_club} if opponent else {})}
            elif opponent:
                result[slot] = {"player": player, "club": explicit_club or auto_club}
            else:
                result[slot] = player
            continue

        leftovers.append(line)

    # For a simple copied list, assign names in the Match Centre's displayed order.
    if leftovers:
        free_slots = [slot for slot in PASTE_ORDERED_SLOTS if slot not in result]
        for slot, line in zip(free_slots, leftovers):
            cleaned_name, explicit_club, preferred_role = _strip_club_and_role(line, known_clubs)
            # Remove leading sequence numbers such as "1. Player".
            cleaned_name = re.sub(r"^\s*\d+[.)]\s*", "", cleaned_name).strip()
            player, auto_club, _ = _resolve_pasted_name(cleaned_name, directory)
            if not player:
                unresolved.append(cleaned_name)
                continue
            if slot.startswith("INT"):
                result[slot] = {"player": player, "preferred_role": preferred_role, **({"club": explicit_club or auto_club} if opponent else {})}
            elif opponent:
                result[slot] = {"player": player, "club": explicit_club or auto_club}
            else:
                result[slot] = player

    return result, unresolved


def main() -> None:
    import pandas as pd
    import streamlit as st

    from afl_lineups import AFLLineupClient, STATUS_LABELS, eligible_players, lineup_check_due, team_code
    from fcfc_engine import (
        CACHE_PATH,
        ROLE_SLOTS,
        ROLE_TYPES,
        ROSTER,
        TEAM_NAMES,
        build_projections,
        cache_is_stale,
        effective_opponents,
        flatten_games,
        load_cache,
        optimise_team,
        refresh_all,
        role_score,
        game_sort_key,
        unique_completed_games,
        save_cache,
        score_submitted_team,
    )

    perth_tz = timezone(timedelta(hours=8))
    st.set_page_config(page_title="FCFC Squad Optimiser", page_icon="🏉", layout="wide")
    cache, cache_warning = load_cache()
    if cache_warning:
        st.warning(cache_warning)
    season = 2026
    games = list(cache.get("games") or [])
    statuses = dict(cache.get("team_status") or {})
    manual_lineup_overrides = dict(cache.get("lineup_manual_overrides") or {})
    for player_name, override in manual_lineup_overrides.items():
        if isinstance(override, dict):
            base = dict(statuses.get(player_name) or {})
            base.update(override)
            base["manual_override"] = True
            statuses[player_name] = base

    with st.sidebar:
        st.header("Controls")
        st.caption("Season: 2026")
        recent_weight = float(
            st.slider(
                "Latest-four-games weighting",
                min_value=0.0,
                max_value=1.0,
                value=0.75,
                step=0.05,
                help="The balance is applied to the broader season average.",
            )
        )
        manually_unavailable = set(
            st.multiselect("Manually unavailable", [player["player"] for player in ROSTER])
        )
        stats_refresh_clicked = st.button(
            "Refresh AFL.com.au statistics", type="primary", use_container_width=True
        )
        lineup_refresh_clicked = st.button(
            "Check AFL team line-ups", use_container_width=True
        )
        if cache.get("refreshed_at"):
            st.caption(f"Statistics refreshed: {cache['refreshed_at']}")
        else:
            st.caption("Statistics have not yet been refreshed.")
        if cache.get("lineups_refreshed_at"):
            st.caption(f"Line-ups checked: {cache['lineups_refreshed_at']}")
        else:
            st.caption("AFL team line-ups have not yet been checked.")

    # On the first app visit after Thursday 4:25 pm Perth time, refresh the
    # published teams automatically. A second automatic check occurs after
    # Friday 4:25 pm for final Sunday teams. The manual button remains available.
    if (
        not lineup_refresh_clicked
        and lineup_check_due(cache.get("lineups_refreshed_at"))
        and not st.session_state.get("lineup_auto_attempted", False)
    ):
        st.session_state["lineup_auto_attempted"] = True
        try:
            auto_result = AFLLineupClient(timeout=12).refresh(
                season=season,
                roster=ROSTER,
                previous_statuses=dict(cache.get("team_status") or {}),
                games=list(cache.get("games") or []),
            )
            cache.update(auto_result)
            if auto_result.get("afl_next_opponents"):
                cache.setdefault("next_opponents", {}).update(auto_result["afl_next_opponents"])
            save_cache(cache, CACHE_PATH)
            if not auto_result.get("lineup_failure_message"):
                st.rerun()
        except Exception as exc:
            cache["lineup_errors"] = [{
                "level": "error",
                "match_id": "automatic-refresh",
                "message": str(exc),
            }]
            save_cache(cache, CACHE_PATH)

    def status_is_from_current_week(value: str | None) -> bool:
        if not value:
            return False
        try:
            checked = datetime.fromisoformat(value)
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=perth_tz)
            checked = checked.astimezone(perth_tz)
        except (TypeError, ValueError):
            return False
        now = datetime.now(perth_tz)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return checked >= week_start

    lineup_current = status_is_from_current_week(cache.get("lineups_refreshed_at"))
    all_players = {player["player"] for player in ROSTER}
    opponents = effective_opponents(cache)

    # A forecast is always available once statistics exist. It is deliberately
    # separate from the final team because it may include players not yet named.
    forecast_projections = build_projections(
        games=games,
        season=season,
        opponents=opponents,
        recent_weight=recent_weight,
        unavailable=manually_unavailable,
    )
    forecast_team = optimise_team(forecast_projections)

    if lineup_current:
        confirmed_players = eligible_players(statuses, include_provisional=False)
        confirmed_or_provisional = eligible_players(statuses, include_provisional=True)
    else:
        confirmed_players = set()
        confirmed_or_provisional = set()

    confirmed_projections = build_projections(
        games=games,
        season=season,
        opponents=opponents,
        recent_weight=recent_weight,
        unavailable=(all_players - confirmed_players) | manually_unavailable,
    )
    confirmed_team = optimise_team(confirmed_projections)

    provisional_projections = build_projections(
        games=games,
        season=season,
        opponents=opponents,
        recent_weight=recent_weight,
        unavailable=(all_players - confirmed_or_provisional) | manually_unavailable,
    )
    provisional_team = optimise_team(provisional_projections)

    current_game_counts = {
        player["player"]: sum(
            1
            for game in games
            if game.get("player") == player["player"]
            and int(game.get("season", 0) or 0) == season
        )
        for player in ROSTER
    }

    team_order = {club: index for index, club in enumerate(TEAM_NAMES)}
    display_roster = sorted(
        ROSTER,
        key=lambda player: team_order.get(player["club"], len(team_order)),
    )

    tabs = st.tabs(
        [
            "Squad",
            "Suggested team",
            "Match Centre",
            "Team announcements",
            "Season averages",
            "Last 4 averages",
            "Opponents",
            "Refresh report",
            "Scoring",
        ]
    )

    def team_table(team: dict, projections: list[dict], title: str, status_note: str) -> None:
        st.subheader(title)
        st.caption(status_note)
        if not team.get("starters"):
            st.warning(team.get("reason") or "A team cannot yet be calculated.")
            st.write(f"Players with usable projections: **{len(projections)}**; 10 are required.")
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("Projected starting score", f"{team['projected_total']:.1f}")
        c2.metric("Eligible projected players", len(projections))
        c3.metric("Latest-four weight", f"{recent_weight:.0%}")

        starter_rows = []
        for row in team["starters"]:
            starter_rows.append(
                {
                    "Position": row["position"],
                    "Player": row["player"],
                    "Club": row["club"],
                    "Opponent": row["opponent"],
                    "Expected": round(row["expected_score"], 1),
                    "Season avg": round(row["season_average"], 1),
                    "Last 4 avg": round(row["recent_average"], 1),
                    "Team status": (statuses.get(row["player"]) or {}).get(
                        "status_label", "Not checked"
                    ),
                }
            )
        starter_df = pd.DataFrame(starter_rows)
        st.dataframe(starter_df, hide_index=True, use_container_width=True)

        st.markdown("**Interchange order**")
        bench_rows = []
        for row in team.get("interchange") or []:
            bench_rows.append(
                {
                    "Position": row["position"],
                    "Player": row["player"],
                    "Club": row["club"],
                    "Opponent": row["opponent"],
                    "Preferred replacement role": row["preferred_role"],
                    "Expected in role": round(row["expected_if_used"], 1),
                    "Team status": (statuses.get(row["player"]) or {}).get(
                        "status_label", "Not checked"
                    ),
                }
            )
        bench_df = pd.DataFrame(bench_rows)
        if not bench_df.empty:
            st.dataframe(bench_df, hide_index=True, use_container_width=True)

        download_df = pd.concat([starter_df, bench_df], ignore_index=True)
        st.download_button(
            f"Download {title.lower()}",
            download_df.to_csv(index=False).encode("utf-8"),
            "fcfc_suggested_team.csv",
            "text/csv",
            key=f"download_{title}",
        )

    with tabs[0]:
        st.subheader("Full squad")
        roster_rows = []
        for player in display_roster:
            status_record = statuses.get(player["player"], {})
            roster_rows.append(
                {
                    "Player": player["player"],
                    "Club": player["club"],
                    "Current-season games": current_game_counts[player["player"]],
                    "Team status": status_record.get("status_label", "Not checked"),
                    "Opponent": status_record.get("opponent") or opponents.get(player["club"], ""),
                }
            )
        roster_df = pd.DataFrame(roster_rows)
        st.dataframe(roster_df, hide_index=True, use_container_width=True, height=680)
        st.download_button(
            "Download squad list",
            roster_df.to_csv(index=False).encode("utf-8"),
            "fcfc_squad.csv",
            "text/csv",
        )

    with tabs[1]:
        if lineup_current:
            team_table(
                confirmed_team,
                confirmed_projections,
                "Final confirmed team",
                "Only players confirmed in this week's official AFL team announcements are eligible.",
            )
            provisional_only = confirmed_or_provisional - confirmed_players
            if provisional_only:
                with st.expander("Provisional team including extended-squad players"):
                    team_table(
                        provisional_team,
                        provisional_projections,
                        "Provisional team",
                        "Includes players named in extended squads but not yet confirmed in the final side.",
                    )
        else:
            st.warning(
                "A current-week team-announcement check has not been completed, so the app will not present a final team as confirmed."
            )

        with st.expander("Pre-announcement forecast", expanded=not lineup_current):
            team_table(
                forecast_team,
                forecast_projections,
                "Pre-announcement forecast",
                "Form-based forecast only. It may include players who are not selected this week.",
            )

    with tabs[2]:
        st.subheader("Match Centre")

        @st.cache_data(ttl=900, show_spinner=False)
        def upcoming_round_name() -> str:
            try:
                fixtures = AFLLineupClient(timeout=12).upcoming_matches(season)
            except Exception:
                return ""
            if not fixtures:
                return ""
            return str(fixtures[0].get("round_name") or "")

        match_state = dict(cache.get("match_centre") or {})
        my_saved = dict(match_state.get("my_team") or {})
        opponent_saved = dict(match_state.get("opponent_team") or {})
        selected_round = upcoming_round_name()
        if not selected_round:
            selected_round = str(match_state.get("round") or "")
        if not selected_round:
            future_status_rounds = [
                str(value.get("round") or "")
                for value in statuses.values()
                if value.get("round")
            ]
            selected_round = max(future_status_rounds, default="")
        st.caption(f"Scoring {selected_round}." if selected_round else "Upcoming round is not yet available from AFL.com.au.")

        player_options = [""] + sorted({player["player"] for player in ROSTER})
        club_options = [""] + sorted(TEAM_NAMES)
        role_labels = {
            "SUPERSTUD": "Superstud", "FWD1": "Forward 1", "FWD2": "Forward 2",
            "MID1": "Midfielder 1", "MID2": "Midfielder 2", "MID3": "Midfielder 3",
            "RUCK": "Ruck", "MARKER": "Marker", "TACKLER": "Tackler", "FREE-KICKER": "Free-kicker",
        }
        ordered_slots = ["SUPERSTUD", "FWD1", "FWD2", "MID1", "MID2", "MID3", "RUCK", "MARKER", "TACKLER", "FREE-KICKER"]
        slot_roles = {slot: role for slot, role in ROLE_SLOTS}

        @st.cache_data(ttl=900, show_spinner=False)
        def fantasy_player_directory() -> list[dict[str, str]]:
            client = AFLLineupClient(timeout=12)
            fantasy_players = client.fantasy_players()
            fantasy_squads = client.fantasy_squads()
            squad_clubs = {
                str(item.get("id")): team_code(
                    item.get("abbreviation") or item.get("name") or item.get("fullName")
                )
                for item in fantasy_squads
            }
            directory: list[dict[str, str]] = []
            for item in fantasy_players:
                player_name = f"{str(item.get('firstName') or '').strip()} {str(item.get('lastName') or '').strip()}".strip()
                club = squad_clubs.get(str(item.get("squadId") or ""), "")
                if player_name and club:
                    directory.append({"player": player_name, "club": club})
            return directory

        flash = st.session_state.pop("mc_paste_flash", None)
        unresolved_saved = st.session_state.pop("mc_paste_unresolved", [])
        if flash:
            st.success(flash)

        with st.expander("Paste submitted teams", expanded=bool(unresolved_saved)):
            st.caption(
                "Paste role-labelled teams, a copied table or 14 player names in Match Centre order. "
                "Opponent clubs are filled automatically from AFL Fantasy."
            )
            paste_left, paste_right = st.columns(2)
            my_paste = paste_left.text_area(
                "Paste my team",
                height=220,
                placeholder="Superstud: Player name\nForward 1: Player name\n...",
                key="mc_paste_my",
            )
            opponent_paste = paste_right.text_area(
                "Paste opponent's team",
                height=220,
                placeholder="Superstud: Player name\nForward 1: Player name\n...",
                key="mc_paste_opp",
            )
            if st.button("Auto-populate pasted teams", use_container_width=True, disabled=not (my_paste.strip() or opponent_paste.strip())):
                try:
                    full_directory = fantasy_player_directory()
                except Exception as exc:
                    full_directory = []
                    st.error(f"Could not load the AFL player directory: {exc}")

                own_directory = [
                    {"player": item["player"], "club": item["club"]}
                    for item in ROSTER
                ]
                known_clubs = set(TEAM_NAMES)
                parsed_my: dict[str, object] = dict(my_saved)
                parsed_opponent: dict[str, object] = dict(opponent_saved)
                unresolved: list[str] = []
                sides_applied: list[str] = []

                if my_paste.strip():
                    new_my, missing_my = parse_pasted_team(
                        my_paste, own_directory, opponent=False, known_clubs=known_clubs
                    )
                    starter_count = sum(1 for slot in ordered_slots if new_my.get(slot))
                    if starter_count < 10:
                        unresolved.extend([f"My team: {name}" for name in missing_my])
                        st.error(
                            f"My team was not applied because only {starter_count} of 10 starting positions were recognised."
                        )
                    else:
                        parsed_my = new_my
                        unresolved.extend([f"My team: {name}" for name in missing_my])
                        sides_applied.append("my team")

                if opponent_paste.strip() and full_directory:
                    new_opponent, missing_opponent = parse_pasted_team(
                        opponent_paste, full_directory, opponent=True, known_clubs=known_clubs
                    )
                    starter_count = sum(1 for slot in ordered_slots if new_opponent.get(slot))
                    if starter_count < 10:
                        unresolved.extend([f"Opponent: {name}" for name in missing_opponent])
                        st.error(
                            f"The opponent's team was not applied because only {starter_count} of 10 starting positions were recognised."
                        )
                    else:
                        parsed_opponent = new_opponent
                        unresolved.extend([f"Opponent: {name}" for name in missing_opponent])
                        sides_applied.append("the opponent's team")

                if sides_applied:
                    cache["match_centre"] = {
                        "round": selected_round,
                        "my_team": parsed_my,
                        "opponent_team": parsed_opponent,
                    }
                    save_cache(cache, CACHE_PATH)
                    for key in list(st.session_state):
                        if (
                            key.startswith("mc_my_")
                            or key.startswith("mc_opp_")
                            or key in {"mc_paste_my", "mc_paste_opp"}
                        ):
                            del st.session_state[key]
                    message = "Auto-populated " + " and ".join(sides_applied) + "."
                    if unresolved:
                        message += " Check the unmatched entries shown below."
                    st.session_state["mc_paste_flash"] = message
                    st.session_state["mc_paste_unresolved"] = unresolved
                    st.rerun()

        if unresolved_saved:
            st.warning("Unmatched pasted entries: " + "; ".join(unresolved_saved))

        my_team_edit: dict[str, object] = {}
        opponent_team_edit: dict[str, object] = {}
        left, right = st.columns(2)
        with left:
            st.markdown("### My submitted team")
            for slot in ordered_slots:
                current = str(my_saved.get(slot) or "")
                my_team_edit[slot] = st.selectbox(
                    role_labels[slot], player_options,
                    index=player_options.index(current) if current in player_options else 0,
                    key=f"mc_my_{slot}",
                )
            st.markdown("**My interchange**")
            for idx in range(1, 5):
                raw = my_saved.get(f"INT{idx}") or {}
                if isinstance(raw, str): raw = {"player": raw, "preferred_role": ""}
                c1, c2 = st.columns([2, 1])
                player = c1.selectbox(
                    f"INT{idx}", player_options,
                    index=player_options.index(raw.get("player", "")) if raw.get("player", "") in player_options else 0,
                    key=f"mc_my_int_{idx}",
                )
                role = c2.selectbox(
                    "Preferred role", [""] + ROLE_TYPES,
                    index=([""] + ROLE_TYPES).index(raw.get("preferred_role", "")) if raw.get("preferred_role", "") in ([""] + ROLE_TYPES) else 0,
                    key=f"mc_my_int_role_{idx}",
                )
                my_team_edit[f"INT{idx}"] = {"player": player, "preferred_role": role}

        with right:
            st.markdown("### Opponent's submitted team")
            for slot in ordered_slots:
                raw = opponent_saved.get(slot) or {}
                if isinstance(raw, str): raw = {"player": raw, "club": ""}
                c1, c2 = st.columns([2, 1])
                name = c1.text_input(role_labels[slot], value=str(raw.get("player") or ""), key=f"mc_opp_{slot}")
                club = c2.selectbox(
                    "Club", club_options,
                    index=club_options.index(raw.get("club", "")) if raw.get("club", "") in club_options else 0,
                    key=f"mc_opp_club_{slot}",
                )
                opponent_team_edit[slot] = {"player": name.strip(), "club": club}
            st.markdown("**Opponent interchange**")
            for idx in range(1, 5):
                raw = opponent_saved.get(f"INT{idx}") or {}
                if isinstance(raw, str): raw = {"player": raw, "club": "", "preferred_role": ""}
                c1, c2, c3 = st.columns([2, 1, 1])
                name = c1.text_input(f"INT{idx}", value=str(raw.get("player") or ""), key=f"mc_opp_int_{idx}")
                club = c2.selectbox(
                    "Club", club_options,
                    index=club_options.index(raw.get("club", "")) if raw.get("club", "") in club_options else 0,
                    key=f"mc_opp_int_club_{idx}",
                )
                role = c3.selectbox(
                    "Preferred role", [""] + ROLE_TYPES,
                    index=([""] + ROLE_TYPES).index(raw.get("preferred_role", "")) if raw.get("preferred_role", "") in ([""] + ROLE_TYPES) else 0,
                    key=f"mc_opp_int_role_{idx}",
                )
                opponent_team_edit[f"INT{idx}"] = {"player": name.strip(), "club": club, "preferred_role": role}

        if st.button("Save submitted teams", type="primary"):
            cache["match_centre"] = {
                "round": selected_round,
                "my_team": my_team_edit,
                "opponent_team": opponent_team_edit,
            }
            save_cache(cache, CACHE_PATH)
            st.success("Both submitted teams were saved.")
            st.rerun()

        def score_selection(saved: dict[str, object], opponent: bool = False) -> dict:
            cleaned: dict[str, object] = {}
            for slot in ordered_slots:
                value = saved.get(slot) or ""
                if isinstance(value, dict):
                    cleaned[slot] = {
                        "player": value.get("player", ""),
                        "club": value.get("club", ""),
                    }
                else:
                    cleaned[slot] = value
            for idx in range(1, 5):
                value = saved.get(f"INT{idx}") or {}
                if isinstance(value, str):
                    value = {"player": value, "preferred_role": "", "club": ""}
                cleaned[f"INT{idx}"] = {
                    "player": value.get("player", ""),
                    "club": value.get("club", ""),
                    "preferred_role": value.get("preferred_role", ""),
                }
            return score_submitted_team(cleaned, games, selected_round, statuses)

        my_score = score_selection(my_saved)
        opponent_score = score_selection(opponent_saved, opponent=True)
        m1, m2, m3 = st.columns(3)
        m1.metric("My score", f"{my_score['total']:.0f}")
        m2.metric("Opponent score", f"{opponent_score['total']:.0f}")
        margin = my_score["total"] - opponent_score["total"]
        m3.metric("Margin", f"{margin:+.0f}")
        if selected_round:
            st.caption("Use **Refresh AFL.com.au statistics** during or after matches to update scores.")

        score_left, score_right = st.columns(2)
        def score_frame(result: dict) -> pd.DataFrame:
            return pd.DataFrame([{
                "Position": row["position"],
                "Submitted": row["submitted_player"],
                "Scoring player": row["scoring_player"],
                "Played": "Yes" if row["played"] else ("Pending" if row.get("pending") else "No"),
                "Score": row["score"],
            } for row in result["rows"]])
        with score_left:
            st.markdown("#### My scorecard")
            st.dataframe(score_frame(my_score), hide_index=True, use_container_width=True)
            if my_score["substitutions"]:
                st.caption("Interchange: " + "; ".join(
                    f"{sub['bench_slot']} {sub['in']} replaced {sub['out']} at {sub['position']}"
                    for sub in my_score["substitutions"]
                ))
        with score_right:
            st.markdown("#### Opponent scorecard")
            st.dataframe(score_frame(opponent_score), hide_index=True, use_container_width=True)
            if opponent_score["substitutions"]:
                st.caption("Interchange: " + "; ".join(
                    f"{sub['bench_slot']} {sub['in']} replaced {sub['out']} at {sub['position']}"
                    for sub in opponent_score["substitutions"]
                ))

    with tabs[3]:
        status_rows = []
        for player in ROSTER:
            record = statuses.get(player["player"], {})
            status_rows.append(
                {
                    "Player": player["player"],
                    "Club": player["club"],
                    "Status": record.get("status_label", "Not checked"),
                    "Opponent": record.get("opponent", ""),
                    "Round": record.get("round", ""),
                    "Matched official name": record.get("matched_name", ""),
                }
            )
        st.dataframe(pd.DataFrame(status_rows), hide_index=True, use_container_width=True, height=680)
        if not lineup_current:
            st.info("Use **Check AFL team line-ups** after the weekly teams are published.")
        lineup_errors = cache.get("lineup_errors") or []
        if lineup_errors:
            st.subheader("Line-up check errors")
            st.dataframe(pd.DataFrame(lineup_errors), hide_index=True, use_container_width=True)

        with st.expander("Manual status override"):
            st.caption("Use this where a club publishes its team in an image or an unusual page format.")
            override_player = st.selectbox(
                "Player", [player["player"] for player in ROSTER], key="lineup_override_player"
            )
            status_options = ["confirmed", "provisional", "emergency", "not_selected", "bye"]
            current_override = manual_lineup_overrides.get(override_player, {})
            current_status = str(current_override.get("status") or "confirmed")
            override_status = st.selectbox(
                "Status",
                status_options,
                index=status_options.index(current_status) if current_status in status_options else 0,
                format_func=lambda value: STATUS_LABELS[value],
                key="lineup_override_status",
            )
            save_col, clear_col = st.columns(2)
            if save_col.button("Save override", use_container_width=True):
                player_record = next(player for player in ROSTER if player["player"] == override_player)
                existing = dict(statuses.get(override_player) or {})
                existing.update({
                    "player": override_player,
                    "club": player_record["club"],
                    "status": override_status,
                    "status_label": STATUS_LABELS[override_status],
                    "manual_override": True,
                    "checked_at": datetime.now(perth_tz).isoformat(timespec="seconds"),
                })
                manual_lineup_overrides[override_player] = existing
                cache["lineup_manual_overrides"] = manual_lineup_overrides
                save_cache(cache, CACHE_PATH)
                st.rerun()
            if clear_col.button("Clear override", use_container_width=True):
                manual_lineup_overrides.pop(override_player, None)
                cache["lineup_manual_overrides"] = manual_lineup_overrides
                save_cache(cache, CACHE_PATH)
                st.rerun()

    def scoring_average_rows(use_last_four: bool = False) -> list[dict]:
        rows: list[dict] = []
        for player in ROSTER:
            player_games = [
                game for game in games
                if game.get("player") == player["player"]
                and int(game.get("season", 0) or 0) == 2026
            ]
            player_games = unique_completed_games(player_games)
            if use_last_four:
                player_games = player_games[-4:]
            if not player_games:
                continue
            def avg_stat(key: str) -> float:
                return sum(float((game.get("stats") or {}).get(key, 0) or 0) for game in player_games) / len(player_games)
            goal_scores = [
                10 * float((game.get("stats") or {}).get("G", 0) or 0)
                + float((game.get("stats") or {}).get("B", 0) or 0)
                for game in player_games
            ]
            stud_scores = [role_score(game.get("stats", {}), "STUD", floor_stud=True) for game in player_games]
            rows.append({
                "Player": player["player"],
                "Club": player["club"],
                "Games": len(player_games),
                "Goal score (10×G+B)": round(sum(goal_scores) / len(goal_scores), 1),
                "Disposals": round(avg_stat("D"), 1),
                "Marks": round(avg_stat("M"), 1),
                "Tackles": round(avg_stat("T"), 1),
                "Hit outs": round(avg_stat("HO"), 1),
                "Frees for": round(avg_stat("FF"), 1),
                "Stud score": round(sum(stud_scores) / len(stud_scores), 1),
            })
        return rows

    with tabs[4]:
        st.subheader("2026 season averages")
        st.caption("Only statistics that contribute to FCFC scoring are shown.")
        rows = scoring_average_rows(use_last_four=False)
        if not rows:
            st.info("No 2026 statistics are stored yet. Run Refresh AFL.com.au statistics.")
        else:
            df = pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, use_container_width=True, height=680)
            st.download_button(
                "Download 2026 season averages",
                df.to_csv(index=False).encode("utf-8"),
                "fcfc_2026_season_averages.csv",
                "text/csv",
            )

    with tabs[5]:
        st.subheader("Last 4 games averages")
        st.caption("The latest four 2026 games for each player. No prior-season games are included.")
        rows = scoring_average_rows(use_last_four=True)
        if not rows:
            st.info("No 2026 statistics are stored yet. Run Refresh AFL.com.au statistics.")
        else:
            df = pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, use_container_width=True, height=680)
            st.download_button(
                "Download last 4 averages",
                df.to_csv(index=False).encode("utf-8"),
                "fcfc_2026_last_4_averages.csv",
                "text/csv",
            )

    with tabs[6]:
        st.write("Automatic opponents can be overridden below.")
        clubs = sorted({player["club"] for player in ROSTER})
        options = [""] + sorted(TEAM_NAMES)
        current_overrides = dict(cache.get("opponent_overrides") or {})
        edited: dict[str, str] = {}
        columns = st.columns(3)
        for index, club in enumerate(clubs):
            automatic = (cache.get("next_opponents") or {}).get(club, "")
            current = current_overrides.get(club, "")
            edited[club] = columns[index % 3].selectbox(
                f"{club} (automatic: {automatic or 'not found'})",
                options,
                index=options.index(current) if current in options else 0,
                key=f"opponent_{club}",
            )
        if st.button("Save opponent overrides"):
            cache["opponent_overrides"] = edited
            save_cache(cache)
            st.success("Opponent overrides saved.")
            st.rerun()

    with tabs[7]:
        summary = cache.get("refresh_summary") or {}
        if summary:
            c1, c2, c3 = st.columns(3)
            c1.metric("Squad players found", summary.get("successful_players", 0))
            c2.metric("Completed matches checked", summary.get("completed_matches_checked", 0))
            c3.metric("Stored 2026 game records", summary.get("current_season_game_records", 0))
        errors = cache.get("errors") or []
        if errors:
            st.dataframe(pd.DataFrame(errors), hide_index=True, use_container_width=True, height=650)
        else:
            st.success("No stored refresh errors.")
        if cache.get("refresh_failure_message"):
            st.error(cache["refresh_failure_message"])
        st.caption(
            "A failed player request no longer removes previously stored records. The quick refresh makes one or two short requests per player and runs them concurrently."
        )

    with tabs[8]:
        st.markdown(
            """
            - **3 midfielders:** kicks + handballs
            - **2 forwards:** 10 × goals + behinds
            - **1 ruckman:** hitouts + marks
            - **1 marker:** 3 × marks
            - **1 tackler:** 5 × tackles
            - **1 free-kicker:** 6 × frees for
            - **1 Superstud:** `(10×goals + behinds + disposals + 3×marks + 5×tackles + 6×frees for) ÷ 2.5`, rounded down for actual game scores
            - **4 interchange:** ordered and assigned a preferred replacement role

            Projections use only 2026 games. They combine a recency-weighted average of the latest four completed games with the 2026 season average. The latest-four component has a 75% default weighting and opponent history is not used. The optimiser assigns unique players to the ten starting positions to maximise expected score.
            """
        )

    # Refresh work is deliberately performed after the current squad and cache have
    # rendered. The user can continue to see the squad while external requests run.
    if stats_refresh_clicked:
        st.divider()
        st.subheader("Refreshing statistics")
        progress_bar = st.progress(0.0, text="Starting fast refresh…")
        progress_text = st.empty()

        def update_progress(done: int, total: int, player: str, message: str) -> None:
            progress_bar.progress(done / max(total, 1), text=f"{done}/{total}: {player}")
            progress_text.caption(message)

        try:
            refresh_roster = list(ROSTER)
            match_state_for_refresh = dict(cache.get("match_centre") or {})
            opponent_for_refresh = dict(match_state_for_refresh.get("opponent_team") or {})
            existing_names = {player["player"].strip().lower() for player in refresh_roster}
            for value in opponent_for_refresh.values():
                if not isinstance(value, dict):
                    continue
                name = str(value.get("player") or "").strip()
                club = str(value.get("club") or "").strip()
                if name and club and name.lower() not in existing_names:
                    refresh_roster.append({"player": name, "club": club, "profile_url": "", "source": "Match Centre opponent"})
                    existing_names.add(name.lower())
            refreshed = refresh_all(
                season=season,
                progress=update_progress,
                roster=refresh_roster,
                previous_cache=cache,
                include_advanced=False,
                max_workers=8,
            )
            save_cache(refreshed, CACHE_PATH)
            summary = refreshed.get("refresh_summary") or {}
            if summary.get("current_season_game_records", 0) > 0:
                st.success(
                    f"Refresh complete: {summary.get('successful_players', 0)} squad players and "
                    f"{summary.get('current_season_game_records', 0)} game records stored."
                )
                st.rerun()
            else:
                st.error(
                    "The AFL refresh returned zero usable squad records. Existing data was retained. "
                    "Open the Refresh report tab for the exact match-level errors."
                )
        except Exception as exc:
            st.error("The refresh failed before completion. Existing cached data has not been deleted.")
            st.exception(exc)

    if lineup_refresh_clicked:
        st.divider()
        with st.spinner("Checking current AFL team announcements…"):
            try:
                result = AFLLineupClient(timeout=12).refresh(
                    season=season,
                    roster=ROSTER,
                    previous_statuses=dict(cache.get("team_status") or {}),
                    games=list(cache.get("games") or []),
                )
                cache.update(result)
                if result.get("afl_next_opponents"):
                    cache.setdefault("next_opponents", {}).update(result["afl_next_opponents"])
                save_cache(cache, CACHE_PATH)
                if result.get("lineup_failure_message"):
                    st.error(result["lineup_failure_message"])
                else:
                    st.success("Current-round team announcements checked.")
                st.rerun()
            except Exception as exc:
                st.error("The team-announcement check failed. Existing statuses were retained.")
                st.exception(exc)


if __name__ == "__main__":
    main()
