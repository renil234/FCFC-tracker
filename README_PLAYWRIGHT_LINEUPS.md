from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from afl_lineups import (  # noqa: E402
    AFLLineupClient,
    AFL_TEAM_LINEUPS_PAGE,
    PERTH_TZ,
    merge_club_rosters,
    parse_club_rosters_payload,
    parse_rendered_lineups_html,
    validate_lineup_document,
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _browser_fetch(page: Any, url: str) -> dict[str, Any]:
    """Fetch an AFL JSON endpoint from inside the real afl.com.au page context."""
    return page.evaluate(
        """
        async ({url}) => {
          let token = window.__fcfcAflToken || '';
          let tokenStatus = 200;
          let tokenPayload = {};
          if (!token) {
            const tokenResponse = await fetch('https://api.afl.com.au/cfs/afl/WMCTok', {
              method: 'POST',
              credentials: 'include',
              headers: {
                'Accept': 'application/json, text/plain, */*'
              }
            });
            tokenStatus = tokenResponse.status;
            try { tokenPayload = await tokenResponse.json(); } catch (_) {}
            token = tokenPayload.token || tokenPayload.accessToken || '';
            if (token) window.__fcfcAflToken = token;
          }
          if (!token) {
            return {
              ok: false,
              stage: 'token',
              status: tokenStatus,
              text: JSON.stringify(tokenPayload)
            };
          }
          const response = await fetch(url, {
            method: 'GET',
            credentials: 'include',
            headers: {
              'Accept': 'application/json, text/plain, */*',
              'x-media-mis-token': token
            }
          });
          const text = await response.text();
          return {ok: response.ok, stage: 'data', status: response.status, text};
        }
        """,
        {"url": url},
    )


def collect_with_browser(
    fixtures: list[dict[str, Any]],
    artifacts_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - workflow dependency
        raise RuntimeError("Playwright is not installed. Run: pip install playwright") from exc

    round_provider_id = next(
        (str(row.get("round_provider_id") or "") for row in fixtures if row.get("round_provider_id")),
        "",
    )
    if not round_provider_id:
        raise RuntimeError("The public AFL fixture did not include a round provider ID.")

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    captured_payloads: list[tuple[str, Any]] = []
    captured_urls: list[str] = []
    browser_errors: list[str] = []
    direct_results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(
            locale="en-AU",
            timezone_id="Australia/Perth",
            viewport={"width": 1440, "height": 1800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def capture_response(response: Any) -> None:
            url = str(response.url)
            lowered = url.lower()
            if not any(token in lowered for token in ("matchroster", "matchrosters", "team-lineup", "/cfs/afl/")):
                return
            content_type = str(response.headers.get("content-type") or "").lower()
            if "json" not in content_type and "text/plain" not in content_type:
                return
            try:
                payload = response.json()
            except Exception:
                try:
                    payload = _safe_json_text(response.text())
                except Exception:
                    payload = None
            if payload is not None:
                captured_payloads.append((url, payload))
                captured_urls.append(url)

        page.on("response", capture_response)
        page.on("console", lambda message: browser_errors.append(f"console {message.type}: {message.text}") if message.type == "error" else None)
        page.on("pageerror", lambda error: browser_errors.append(f"pageerror: {error}"))

        page.goto(AFL_TEAM_LINEUPS_PAGE, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4_000)

        # Dismiss common consent buttons if one is blocking page interaction.
        for label in ("Accept All", "Accept all", "I agree", "Agree"):
            try:
                locator = page.get_by_role("button", name=label, exact=False)
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=2_000)
                    break
            except Exception:
                continue

        # Give the page's own JavaScript time to request its line-up data.
        page.wait_for_timeout(10_000)

        round_urls = [
            f"https://api.afl.com.au/cfs/afl/matchRosters/round/{round_provider_id}?minimal=false",
            f"https://api.afl.com.au/cfs/afl/matchRosters/round/{round_provider_id}?minimal=true",
        ]
        for url in round_urls:
            try:
                result = _browser_fetch(page, url)
                result["url"] = url
                direct_results.append(result)
                payload = _safe_json_text(str(result.get("text") or ""))
                if result.get("ok") and payload is not None:
                    captured_payloads.append((url, payload))
                    captured_urls.append(url)
            except Exception as exc:
                browser_errors.append(f"round fetch {url}: {exc}")

        # If the round endpoint changes or is incomplete, capture the one-match
        # endpoints through the same authenticated browser context.
        for fixture in fixtures:
            match_id = str(fixture.get("match_id") or "")
            if not match_id:
                continue
            for operation in ("matchRoster/full", "matchItem"):
                url = f"https://api.afl.com.au/cfs/afl/{operation}/{match_id}"
                try:
                    result = _browser_fetch(page, url)
                    result["url"] = url
                    direct_results.append(result)
                    payload = _safe_json_text(str(result.get("text") or ""))
                    if result.get("ok") and payload is not None:
                        captured_payloads.append((url, payload))
                        captured_urls.append(url)
                except Exception as exc:
                    browser_errors.append(f"match fetch {url}: {exc}")

        page.wait_for_timeout(2_000)
        rendered_html = page.content()
        page.screenshot(path=str(artifacts_dir / "team-lineups.png"), full_page=True)
        (artifacts_dir / "team-lineups.html").write_text(rendered_html, encoding="utf-8")
        browser.close()

    clubs: dict[str, dict[str, Any]] = {}
    payload_summaries: list[dict[str, Any]] = []
    for index, (url, payload) in enumerate(captured_payloads):
        parsed = parse_club_rosters_payload(payload)
        clubs = merge_club_rosters(clubs, parsed)
        payload_summaries.append({
            "index": index,
            "url": url,
            "clubs_found": sorted(parsed),
            "selected_counts": {club: int(side.get("raw_selected_count", 0)) for club, side in parsed.items()},
        })
        # Keep the raw response only as a workflow artifact, never in app data.
        try:
            _write_json(artifacts_dir / f"network-{index:02d}.json", payload)
        except Exception as exc:
            browser_errors.append(f"save network payload {index}: {exc}")

    rendered_round, rendered_clubs = parse_rendered_lineups_html(rendered_html)
    clubs = merge_club_rosters(clubs, rendered_clubs)

    diagnostics = {
        "round_provider_id": round_provider_id,
        "captured_response_count": len(captured_payloads),
        "captured_urls": sorted(set(captured_urls)),
        "payload_summaries": payload_summaries,
        "rendered_round_number": rendered_round,
        "rendered_clubs": sorted(rendered_clubs),
        "direct_fetches": [
            {"url": row.get("url"), "ok": row.get("ok"), "stage": row.get("stage"), "status": row.get("status")}
            for row in direct_results
        ],
        "browser_errors": browser_errors[-50:],
    }
    _write_json(artifacts_dir / "collector-diagnostics.json", diagnostics)
    return clubs, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect AFL team selections in a real browser.")
    parser.add_argument("--season", type=int, default=int(os.environ.get("FCFC_SEASON", datetime.now().year)))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "lineups_latest.json")
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "data" / "lineups")
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "artifacts" / "lineups")
    args = parser.parse_args()

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    checked_utc = datetime.now(timezone.utc)
    checked_perth = checked_utc.astimezone(PERTH_TZ)

    try:
        fixtures = AFLLineupClient(timeout=25).upcoming_matches(args.season)
        if not fixtures:
            raise RuntimeError("The public AFL API returned no upcoming round.")
        round_number = next(
            (int(row["round_number"]) for row in fixtures if row.get("round_number") not in (None, "")),
            None,
        )
        round_name = next((str(row.get("round_name") or "") for row in fixtures if row.get("round_name")), "")
        round_provider_id = next((str(row.get("round_provider_id") or "") for row in fixtures if row.get("round_provider_id")), "")

        clubs, diagnostics = collect_with_browser(fixtures, args.artifacts_dir)
        document: dict[str, Any] = {
            "schema_version": 1,
            "season": args.season,
            "round_number": round_number,
            "round_name": round_name,
            "round_provider_id": round_provider_id,
            "checked_at_utc": checked_utc.isoformat(timespec="seconds"),
            "checked_at_perth": checked_perth.isoformat(timespec="seconds"),
            "source": "AFL Team Line-ups browser collector",
            "source_url": AFL_TEAM_LINEUPS_PAGE,
            "source_title": f"AFL Team Line-ups — {round_name or f'Round {round_number}'}",
            "collection_method": "Playwright browser + authenticated AFL roster responses",
            "fixtures": fixtures,
            "clubs": clubs,
            "diagnostics": diagnostics,
            "warnings": [],
        }
        validation = validate_lineup_document(document, expected_fixtures=fixtures)
        document["validation"] = validation
        _write_json(args.artifacts_dir / "candidate-lineups.json", document)

        if not validation.get("valid"):
            raise RuntimeError("Line-up validation failed: " + "; ".join(validation.get("errors") or []))

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.archive_dir.mkdir(parents=True, exist_ok=True)
        _write_json(args.output, document)
        archive_name = f"{args.season}_round_{round_number}.json" if round_number is not None else f"{args.season}_latest.json"
        _write_json(args.archive_dir / archive_name, document)
        print(
            f"Saved validated {round_name or round_number}: "
            f"{len(clubs)} clubs, {sum(len(side.get('selected') or []) for side in clubs.values())} selected players."
        )
        return 0
    except Exception as exc:
        failure = {
            "failed_at_utc": checked_utc.isoformat(timespec="seconds"),
            "season": args.season,
            "error": str(exc),
            "last_good_file_preserved": args.output.exists(),
        }
        _write_json(args.artifacts_dir / "failure.json", failure)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
