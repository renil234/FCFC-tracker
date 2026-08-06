from __future__ import annotations

from datetime import datetime, timedelta, timezone


def main() -> None:
    import pandas as pd
    import streamlit as st

    from afl_lineups import AFLLineupClient, eligible_players, lineup_check_due
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
    st.title("FCFC Squad Optimiser")
    st.caption(
        "Your full squad is always shown. Statistics come from AFL.com.au, and the final suggested team only uses players confirmed in the AFL team announcements."
    )

    st.caption("Statistics and team selections source: AFL.com.au.")

    cache, cache_warning = load_cache()
    if cache_warning:
        st.warning(cache_warning)
    season = 2026
    games = list(cache.get("games") or [])
    statuses = dict(cache.get("team_status") or {})

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

    top1, top2, top3, top4 = st.columns(4)
    top1.metric("Squad players", len(ROSTER))
    top2.metric("Players with current data", sum(count > 0 for count in current_game_counts.values()))
    top3.metric("Current-season game records", sum(current_game_counts.values()))
    top4.metric("Confirmed this week", len(confirmed_players))

    if cache_is_stale(cache):
        st.info("Statistics are due for refresh. The squad remains visible while the refresh runs.")
    if lineup_check_due(cache.get("lineups_refreshed_at")):
        st.info("This week's AFL team announcements are due to be checked.")

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
        st.caption("This list is embedded in the app and is shown even when every external data request fails.")
        roster_rows = []
        for player in ROSTER:
            status_record = statuses.get(player["player"], {})
            roster_rows.append(
                {
                    "Player": player["player"],
                    "Club": player["club"],
                    "Current-season games": current_game_counts[player["player"]],
                    "Team status": status_record.get("status_label", "Not checked"),
                    "Opponent": status_record.get("opponent") or opponents.get(player["club"], ""),
                    "Data source": player.get("source", "Squad"),
                    "Statistics source": "AFL.com.au",
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
        st.caption(
            "Save your submitted team and your opponent's team, then refresh official AFL.com.au statistics to keep score for the selected round."
        )

        match_state = dict(cache.get("match_centre") or {})
        my_saved = dict(match_state.get("my_team") or {})
        opponent_saved = dict(match_state.get("opponent_team") or {})
        round_options = sorted(
            {str(game.get("description") or "") for game in games if game.get("description")},
            key=lambda value: max(
                [game_sort_key(game) for game in games if str(game.get("description") or "") == value]
                or [(0, 0, "")]
            ),
        )
        status_rounds = sorted({str(v.get("round") or "") for v in statuses.values() if v.get("round")})
        for value in status_rounds:
            if value not in round_options:
                round_options.append(value)
        if not round_options:
            round_options = [""]
        saved_round = str(match_state.get("round") or "")
        default_round_index = round_options.index(saved_round) if saved_round in round_options else len(round_options) - 1
        selected_round = st.selectbox(
            "Round to score",
            round_options,
            index=max(0, default_round_index),
            format_func=lambda value: value or "No round data available",
        )

        player_options = [""] + sorted({player["player"] for player in ROSTER})
        club_options = [""] + sorted(TEAM_NAMES)
        role_labels = {
            "SUPERSTUD": "Superstud", "FWD1": "Forward 1", "FWD2": "Forward 2",
            "MID1": "Midfielder 1", "MID2": "Midfielder 2", "MID3": "Midfielder 3",
            "RUCK": "Ruck", "MARKER": "Marker", "TACKLER": "Tackler", "FREE-KICKER": "Free-kicker",
        }
        ordered_slots = ["SUPERSTUD", "FWD1", "FWD2", "MID1", "MID2", "MID3", "RUCK", "MARKER", "TACKLER", "FREE-KICKER"]
        slot_roles = {slot: role for slot, role in ROLE_SLOTS}

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
            st.caption("Enter official AFL player names and clubs. These players are added to the next statistics refresh.")
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
                cleaned[slot] = value.get("player", "") if isinstance(value, dict) else value
            for idx in range(1, 5):
                value = saved.get(f"INT{idx}") or {}
                if isinstance(value, str): value = {"player": value, "preferred_role": ""}
                cleaned[f"INT{idx}"] = {
                    "player": value.get("player", ""),
                    "preferred_role": value.get("preferred_role", ""),
                }
            return score_submitted_team(cleaned, games, selected_round)

        my_score = score_selection(my_saved)
        opponent_score = score_selection(opponent_saved, opponent=True)
        m1, m2, m3 = st.columns(3)
        m1.metric("My score", f"{my_score['total']:.0f}")
        m2.metric("Opponent score", f"{opponent_score['total']:.0f}")
        margin = my_score["total"] - opponent_score["total"]
        m3.metric("Margin", f"{margin:+.0f}")
        if selected_round:
            st.caption(f"Scoring round: {selected_round}. Use **Refresh AFL.com.au statistics** during or after matches to update scores.")

        score_left, score_right = st.columns(2)
        def score_frame(result: dict) -> pd.DataFrame:
            return pd.DataFrame([{
                "Position": row["position"],
                "Submitted": row["submitted_player"],
                "Scoring player": row["scoring_player"],
                "Played": "Yes" if row["played"] else "No",
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
        with st.spinner("Checking official AFL team announcements…"):
            try:
                result = AFLLineupClient(timeout=12).refresh(
                    season=season,
                    roster=ROSTER,
                    previous_statuses=dict(cache.get("team_status") or {}),
                )
                cache.update(result)
                if result.get("afl_next_opponents"):
                    cache.setdefault("next_opponents", {}).update(result["afl_next_opponents"])
                save_cache(cache, CACHE_PATH)
                st.success("AFL team announcements checked.")
                st.rerun()
            except Exception as exc:
                st.error("The team-announcement check failed. Existing statuses were retained.")
                st.exception(exc)


if __name__ == "__main__":
    main()
