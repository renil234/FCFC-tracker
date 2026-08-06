from __future__ import annotations

from afl_lineups import AFLLineupClient
from fcfc_engine import CACHE_PATH, ROSTER, load_cache, save_cache


if __name__ == "__main__":
    cache, warning = load_cache(CACHE_PATH)
    if warning:
        print(warning)
    season = int(cache.get("season") or 2026)
    result = AFLLineupClient().refresh(
        season=season,
        roster=ROSTER,
        previous_statuses=dict(cache.get("team_status") or {}),
    )
    cache.update(result)
    if result.get("afl_next_opponents"):
        cache.setdefault("next_opponents", {}).update(result["afl_next_opponents"])
    save_cache(cache, CACHE_PATH)
    status_counts: dict[str, int] = {}
    for record in (cache.get("team_status") or {}).values():
        status = str(record.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    print(f"Loaded saved team statuses for {len(cache.get('team_status') or {})} squad players")
    print(status_counts)
