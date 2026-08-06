from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

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




CLUB_CLICK_ALIASES: dict[str, tuple[str, ...]] = {
    "ADE": ("adelaide", "crows"),
    "BRL": ("brisbane", "lions"),
    "CAR": ("carlton", "blues"),
    "COL": ("collingwood", "magpies"),
    "ESS": ("essendon", "bombers"),
    "FRE": ("fremantle", "dockers"),
    "GCS": ("gold coast", "suns"),
    "GEE": ("geelong", "cats"),
    "GWS": ("greater western sydney", "gws", "giants"),
    "HAW": ("hawthorn", "hawks"),
    "MEL": ("melbourne", "demons"),
    "NM": ("north melbourne", "kangaroos", "north"),
    "POR": ("port adelaide", "power"),
    "RIC": ("richmond", "tigers"),
    "STK": ("st kilda", "saints"),
    "SYD": ("sydney", "swans"),
    "WBD": ("western bulldogs", "bulldogs"),
    "WCE": ("west coast", "eagles"),
}


def _normalise_click_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _candidate_score(candidate: dict[str, Any], fixture: dict[str, Any]) -> int:
    haystack = _normalise_click_text(candidate.get("search_text"))
    if not haystack:
        return 0
    score = 0
    match_id = _normalise_click_text(fixture.get("match_id"))
    if len(match_id) >= 8 and match_id in haystack:
        score += 1000
    clubs_found = 0
    for side in ("home", "away"):
        club = str(fixture.get(side) or "")
        aliases = CLUB_CLICK_ALIASES.get(club, ())
        code_present = bool(re.search(rf"(?<![a-z0-9]){re.escape(club.lower())}(?![a-z0-9])", haystack))
        if code_present or any(alias in haystack for alias in aliases):
            clubs_found += 1
    if clubs_found == 2:
        score += 500
    elif clubs_found == 1:
        score += 40
    role = _normalise_click_text(candidate.get("role"))
    tag = _normalise_click_text(candidate.get("tag"))
    if role == "tab":
        score += 40
    if tag in {"button", "a"}:
        score += 10
    if candidate.get("visible"):
        score += 20
    return score


def _interactive_candidates(page: Any) -> list[dict[str, Any]]:
    selector = "button, a, [role=button], [role=tab]"
    locator = page.locator(selector)
    count = min(locator.count(), 500)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        item = locator.nth(index)
        try:
            data = item.evaluate(
                """
                (el) => {
                  const bits = [
                    el.innerText || '', el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('href') || '',
                    el.getAttribute('data-match-id') || '',
                    el.getAttribute('data-provider-id') || '',
                    el.getAttribute('data-testid') || '',
                    el.getAttribute('value') || '',
                    ...Array.from(el.querySelectorAll('img')).map(img => img.alt || '')
                  ];
                  const rect = el.getBoundingClientRect();
                  return {
                    tag: (el.tagName || '').toLowerCase(),
                    role: el.getAttribute('role') || '',
                    search_text: bits.join(' '),
                    visible: rect.width > 0 && rect.height > 0
                  };
                }
                """
            )
            if isinstance(data, dict):
                data["index"] = index
                rows.append(data)
        except Exception:
            continue
    return rows


def _collect_rendered_state(
    page: Any,
    label: str,
    artifacts_dir: Path,
    current: dict[str, dict[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    html = page.content()
    detected_round, parsed = parse_rendered_lineups_html(html)
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-")[:80] or "state"
    try:
        (artifacts_dir / f"rendered-{safe_label}.html").write_text(html, encoding="utf-8")
        page.screenshot(path=str(artifacts_dir / f"rendered-{safe_label}.png"), full_page=True)
    except Exception:
        pass
    diagnostics.append({
        "label": label,
        "url": page.url,
        "round": detected_round,
        "clubs_found": sorted(parsed),
        "selected_counts": {club: int(side.get("raw_selected_count", 0)) for club, side in parsed.items()},
    })
    return merge_club_rosters(current, parsed)


def _click_fixture_selector(page: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    candidates = _interactive_candidates(page)
    ranked = sorted(
        (( _candidate_score(row, fixture), row) for row in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = ranked[0] if ranked else (0, {})
    result: dict[str, Any] = {
        "match_id": fixture.get("match_id"),
        "home": fixture.get("home"),
        "away": fixture.get("away"),
        "candidate_count": len(candidates),
        "best_score": best_score,
        "clicked": False,
    }
    if best_score < 100:
        return result
    try:
        target = page.locator("button, a, [role=button], [role=tab]").nth(int(best["index"]))
        target.scroll_into_view_if_needed(timeout=3_000)
        target.click(timeout=6_000, force=True)
        page.wait_for_timeout(2_500)
        result["clicked"] = True
        result["candidate_text"] = _normalise_click_text(best.get("search_text"))[:500]
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _cycle_navigation_buttons(
    page: Any,
    artifacts_dir: Path,
    clubs: dict[str, dict[str, Any]],
    state_diagnostics: list[dict[str, Any]],
    max_clicks: int = 12,
) -> dict[str, dict[str, Any]]:
    """Fallback for carousel implementations where matchup cards are not text-addressable."""
    seen_signatures: set[tuple[str, ...]] = set()
    for step in range(max_clicks):
        _, parsed = parse_rendered_lineups_html(page.content())
        signature = tuple(sorted(parsed))
        if signature:
            if signature in seen_signatures and step > 1:
                break
            seen_signatures.add(signature)
        candidates = _interactive_candidates(page)
        next_rows: list[tuple[int, dict[str, Any]]] = []
        for row in candidates:
            text = _normalise_click_text(row.get("search_text"))
            score = 0
            if re.search(r"\b(next|right|forward)\b", text):
                score += 100
            if "carousel" in text or "slider" in text:
                score += 30
            if row.get("visible"):
                score += 10
            if score:
                next_rows.append((score, row))
        if not next_rows:
            break
        _, best = max(next_rows, key=lambda pair: pair[0])
        try:
            target = page.locator("button, a, [role=button], [role=tab]").nth(int(best["index"]))
            target.scroll_into_view_if_needed(timeout=2_000)
            target.click(timeout=4_000, force=True)
            page.wait_for_timeout(2_000)
            clubs = _collect_rendered_state(
                page, f"carousel-{step + 1}", artifacts_dir, clubs, state_diagnostics
            )
        except Exception:
            break
    return clubs


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




def _request_rosters_direct(
    fixtures: list[dict[str, Any]], artifacts_dir: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Call the official roster endpoint from the GitHub runner.

    This follows the same token + matchRoster/full flow used by the maintained
    fitzRoy package. It is attempted outside Streamlit because GitHub-hosted
    runners and Streamlit Cloud can receive different edge/CDN treatment.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.afl.com.au",
        "Referer": AFL_TEAM_LINEUPS_PAGE,
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    })
    diagnostics: dict[str, Any] = {"token_status": None, "matches": []}
    clubs: dict[str, dict[str, Any]] = {}
    try:
        token_response = session.post("https://api.afl.com.au/cfs/afl/WMCTok", timeout=30)
        diagnostics["token_status"] = token_response.status_code
        token_payload = token_response.json() if token_response.content else {}
        token = str(token_payload.get("token") or token_payload.get("accessToken") or "")
        if not token:
            diagnostics["token_error"] = f"No token in response: {token_response.text[:500]}"
            return clubs, diagnostics
    except Exception as exc:
        diagnostics["token_error"] = str(exc)
        return clubs, diagnostics

    headers = {"x-media-mis-token": token}
    for fixture in fixtures:
        match_id = str(fixture.get("match_id") or "")
        if not match_id:
            continue
        url = f"https://api.afl.com.au/cfs/afl/matchRoster/full/{match_id}"
        row: dict[str, Any] = {
            "match_id": match_id, "home": fixture.get("home"), "away": fixture.get("away"), "url": url
        }
        try:
            response = session.get(url, headers=headers, timeout=30)
            row["status"] = response.status_code
            row["content_type"] = response.headers.get("content-type", "")
            if response.ok:
                payload = response.json()
                parsed = parse_club_rosters_payload(payload)
                clubs = merge_club_rosters(clubs, parsed)
                row["clubs_found"] = sorted(parsed)
                row["selected_counts"] = {
                    club: int(side.get("raw_selected_count", 0)) for club, side in parsed.items()
                }
                _write_json(artifacts_dir / f"direct-{match_id}.json", payload)
            else:
                row["response"] = response.text[:1000]
        except Exception as exc:
            row["error"] = str(exc)
        diagnostics["matches"].append(row)
    return clubs, diagnostics


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
    direct_request_clubs, direct_request_diagnostics = _request_rosters_direct(fixtures, artifacts_dir)
    captured_payloads: list[tuple[str, Any]] = []
    captured_urls: list[str] = []
    browser_errors: list[str] = []
    direct_results: list[dict[str, Any]] = []
    rendered_state_diagnostics: list[dict[str, Any]] = []
    rendered_clubs_accum: dict[str, dict[str, Any]] = {}
    fixture_click_diagnostics: list[dict[str, Any]] = []

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

        # Give the page's own JavaScript time to request its first visible match.
        page.wait_for_timeout(8_000)
        rendered_clubs_accum = _collect_rendered_state(
            page, "initial", artifacts_dir, rendered_clubs_accum, rendered_state_diagnostics
        )

        # The AFL page shows one matchup at a time. Iterate the round's matchup
        # controls so the browser loads and renders every club, not only the
        # default fixture. Network responses remain captured during these clicks.
        for fixture in fixtures:
            click_result = _click_fixture_selector(page, fixture)
            fixture_click_diagnostics.append(click_result)
            if click_result.get("clicked"):
                label = f"{fixture.get('home')}-{fixture.get('away')}-{fixture.get('match_id')}"
                rendered_clubs_accum = _collect_rendered_state(
                    page, label, artifacts_dir, rendered_clubs_accum, rendered_state_diagnostics
                )

        # Some versions of the component expose only carousel arrows rather than
        # text-addressable matchup tabs. Cycle those controls as a secondary path.
        rendered_clubs_accum = _cycle_navigation_buttons(
            page, artifacts_dir, rendered_clubs_accum, rendered_state_diagnostics
        )

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

    clubs: dict[str, dict[str, Any]] = merge_club_rosters(direct_request_clubs, rendered_clubs_accum)
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
        "direct_request": direct_request_diagnostics,
        "direct_request_clubs": sorted(direct_request_clubs),
        "captured_response_count": len(captured_payloads),
        "captured_urls": sorted(set(captured_urls)),
        "payload_summaries": payload_summaries,
        "rendered_round_number": rendered_round,
        "rendered_clubs": sorted(rendered_clubs),
        "rendered_states": rendered_state_diagnostics,
        "fixture_clicks": fixture_click_diagnostics,
        "clubs_after_browser_iteration": sorted(rendered_clubs_accum),
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
