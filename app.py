from __future__ import annotations

from datetime import datetime, timedelta, timezone


def main() -> None:
    import pandas as pd
    import streamlit as st

    from afl_lineups import AFLLineupClient, eligible_players, lineup_check_due
    from fcfc_engine import (
        CACHE_PATH,
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
        save_cache,
        season_summary,
    )

    perth_tz = timezone(timedelta(hours=8))
    st.set_page_config(page_title="FCFC Squad Optimiser", page_icon="🏉", layout="wide")
    st.title("FCFC Squad Optimiser")
    st.caption(
        "Your full squad is always shown. Statistics come from AFL Tables, and the final suggested team only uses players confirmed in the AFL team announcements."
    )

    st.caption("Statistics source: AFL Tables. Team selections source: AFL.com.au.")

    cache, cache_warning = load_cache()
    if cache_warning:
        st.warning(cache_warning)
    season = int(cache.get("season") or datetime.now(perth_tz).year)
    games = list(cache.get("games") or [])
    statuses = dict(cache.get("team_status") or {})

    with st.sidebar:
        st.header("Controls")
        season = int(st.number_input("Season", min_value=2024, max_value=2035, value=season, step=1))
        recent_weight = float(
            st.slider(
                "Latest-three-games weighting",
                min_value=0.0,
                max_value=1.0,
                value=0.65,
                step=0.05,
                help="The balance is applied to the broader season average.",
            )
        )
        manually_unavailable = set(
            st.multiselect("Manually unavailable", [player["player"] for player in ROSTER])
        )
        stats_refresh_clicked = st.button(
            "Refresh AFL Tables statistics", type="primary", use_container_width=True
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
            "Team announcements",
            "Raw statistics",
            "Game logs",
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
        c3.metric("Latest-three weight", f"{recent_weight:.0%}")

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
                    "Recent avg": round(row["recent_average"], 1),
                    "Opponent factor": round(row["opponent_factor"], 3),
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
                    "Statistics source": "AFL Tables",
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

    with tabs[3]:
        totals, averages = season_summary(games, season)
        if not totals:
            st.info("No current-season statistics are stored yet. The Squad tab still shows every player.")
        else:
            totals_tab, averages_tab = st.tabs(["Season totals", "Per-game averages"])
            with totals_tab:
                totals_df = pd.DataFrame(totals)
                st.dataframe(totals_df, hide_index=True, use_container_width=True, height=650)
                st.download_button(
                    "Download season totals",
                    totals_df.to_csv(index=False).encode("utf-8"),
                    "fcfc_season_totals.csv",
                    "text/csv",
                )
            with averages_tab:
                averages_df = pd.DataFrame(averages)
                st.dataframe(averages_df, hide_index=True, use_container_width=True, height=650)
                st.download_button(
                    "Download per-game averages",
                    averages_df.to_csv(index=False).encode("utf-8"),
                    "fcfc_per_game_averages.csv",
                    "text/csv",
                )

    with tabs[4]:
        log_rows = flatten_games(games)
        if not log_rows:
            st.info("No game logs are stored yet.")
        else:
            log_df = pd.DataFrame(log_rows)
            selected = st.multiselect(
                "Filter players", sorted(log_df["Player"].dropna().unique()), key="game_log_filter"
            )
            if selected:
                log_df = log_df[log_df["Player"].isin(selected)]
            st.dataframe(
                log_df.sort_values(["Season", "Date", "Player"], ascending=[False, False, True]),
                hide_index=True,
                use_container_width=True,
                height=680,
            )
            st.download_button(
                "Download all game logs",
                log_df.to_csv(index=False).encode("utf-8"),
                "fcfc_game_logs.csv",
                "text/csv",
            )
            players_with_logs = sorted(log_df["Player"].dropna().unique())
            if players_with_logs:
                history_player = st.selectbox("Scoring history player", players_with_logs)
                history_role = st.selectbox("Scoring role", ROLE_TYPES)
                chart_rows = [
                    {
                        "Date": game.get("date"),
                        "Score": role_score(game.get("stats", {}), history_role),
                    }
                    for game in games
                    if game.get("player") == history_player and game.get("date")
                ]
                if chart_rows:
                    st.line_chart(pd.DataFrame(chart_rows).sort_values("Date").set_index("Date"))

    with tabs[5]:
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

    with tabs[6]:
        summary = cache.get("refresh_summary") or {}
        if summary:
            c1, c2, c3 = st.columns(3)
            c1.metric("Successful player pages", summary.get("successful_players", 0))
            c2.metric("Failed player pages", summary.get("failed_players", 0))
            c3.metric("Stored current-season games", summary.get("current_season_game_records", 0))
        errors = cache.get("errors") or []
        if errors:
            st.dataframe(pd.DataFrame(errors), hide_index=True, use_container_width=True, height=650)
        else:
            st.success("No stored refresh errors.")
        st.caption(
            "A failed player request no longer removes previously stored records. The quick refresh makes one or two short requests per player and runs them concurrently."
        )

    with tabs[7]:
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

            Projections combine the latest three games with the broader season average, then apply a conservative opponent adjustment. The optimiser assigns unique players to the ten starting positions to maximise expected score.
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
            refreshed = refresh_all(
                season=season,
                progress=update_progress,
                previous_cache=cache,
                include_advanced=False,
                max_workers=8,
            )
            save_cache(refreshed, CACHE_PATH)
            summary = refreshed.get("refresh_summary") or {}
            st.success(
                f"Refresh complete: {summary.get('successful_players', 0)} player pages succeeded; "
                f"{summary.get('failed_players', 0)} failed without deleting older data."
            )
            st.rerun()
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
