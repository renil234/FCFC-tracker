from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import base64
import json
import os

import requests

GITHUB_OWNER = "renil234"
GITHUB_REPO = "fcfc-tracker"
GITHUB_BRANCH = "main"
GITHUB_PATH = "data/match_centre.json"
PERTH_TZ = timezone(timedelta(hours=8))


def competition_week_key(now: datetime | None = None) -> str:
    """Return the Thursday-based FCFC week key in Perth time.

    A saved matchup remains current through Wednesday night and becomes stale
    from 00:00 Thursday Perth time.
    """
    current = now or datetime.now(PERTH_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=PERTH_TZ)
    else:
        current = current.astimezone(PERTH_TZ)
    # Monday=0 ... Thursday=3. Find the most recent Thursday.
    days_since_thursday = (current.weekday() - 3) % 7
    thursday = (current - timedelta(days=days_since_thursday)).date()
    return thursday.isoformat()


def _api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"


def _headers(token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fcfc-tracker",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def load_match_centre_state(token: str = "", timeout: int = 12) -> tuple[dict[str, Any] | None, str]:
    """Load persistent Match Centre state from GitHub.

    Returns (state, message). state=None means no remote state is available and
    the caller may use its local cache fallback.
    """
    try:
        response = requests.get(
            _api_url(), params={"ref": GITHUB_BRANCH}, headers=_headers(token), timeout=timeout
        )
    except Exception as exc:
        return None, f"Persistent Match Centre storage could not be read: {exc}"

    if response.status_code == 404:
        return None, "Persistent Match Centre storage has not been created yet."
    if response.status_code >= 400:
        return None, f"Persistent Match Centre storage read failed ({response.status_code})."

    try:
        payload = response.json()
        raw = base64.b64decode(payload.get("content") or "").decode("utf-8")
        state = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        return None, f"Persistent Match Centre storage was unreadable: {exc}"

    if not isinstance(state, dict):
        return None, "Persistent Match Centre storage had an invalid format."

    current_key = competition_week_key()
    stored_key = str(state.get("week_key") or "")
    if stored_key and stored_key != current_key:
        # The previous round expired after Wednesday night. Return an empty
        # state immediately; if credentials exist, also persist the reset.
        empty = {"week_key": current_key, "round": "", "my_team": {}, "opponent_team": {}}
        if token:
            save_match_centre_state(empty, token=token, timeout=timeout)
        return empty, "Previous Match Centre teams were cleared for the new FCFC week."

    return state, ""


def save_match_centre_state(
    state: dict[str, Any], token: str = "", timeout: int = 12
) -> tuple[bool, str]:
    """Persist Match Centre state to GitHub using the Contents API."""
    if not token:
        return False, "GITHUB_TOKEN is not configured in Streamlit secrets."

    data = dict(state or {})
    data["week_key"] = competition_week_key()
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

    sha = ""
    try:
        existing = requests.get(
            _api_url(), params={"ref": GITHUB_BRANCH}, headers=_headers(token), timeout=timeout
        )
        if existing.status_code == 200:
            sha = str(existing.json().get("sha") or "")
        elif existing.status_code not in (404,):
            return False, f"Could not inspect persistent Match Centre storage ({existing.status_code})."

        body: dict[str, Any] = {
            "message": "Update Match Centre teams",
            "content": base64.b64encode(content).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            body["sha"] = sha
        response = requests.put(_api_url(), headers=_headers(token), json=body, timeout=timeout)
    except Exception as exc:
        return False, f"Persistent Match Centre save failed: {exc}"

    if response.status_code not in (200, 201):
        detail = ""
        try:
            detail = str(response.json().get("message") or "")
        except Exception:
            pass
        suffix = f": {detail}" if detail else ""
        return False, f"Persistent Match Centre save failed ({response.status_code}){suffix}"
    return True, ""
