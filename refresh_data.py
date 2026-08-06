from __future__ import annotations

from fcfc_engine import CACHE_PATH, load_cache, refresh_all, save_cache


def progress(done: int, total: int, player: str, message: str) -> None:
    print(f"[{done:02d}/{total:02d}] {player}: {message}", flush=True)


if __name__ == "__main__":
    existing, warning = load_cache(CACHE_PATH)
    if warning:
        print(warning)
    cache = refresh_all(
        season=int(existing.get("season") or 2026),
        progress=progress,
        previous_cache=existing,
        include_advanced=False,
        max_workers=6,
    )
    save_cache(cache, CACHE_PATH)
    summary = cache.get("refresh_summary") or {}
    print(
        f"Saved {len(cache.get('games') or [])} game records; "
        f"{summary.get('successful_players', 0)} players succeeded and "
        f"{summary.get('failed_players', 0)} failed."
    )
