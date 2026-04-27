"""
Pull Google Calendar events for the user's accounts using OAuth tokens already
saved by the inbox project. Returns a list of normalized event dicts.

No google-api-python-client dependency — just requests + the saved refresh_token.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests

TOKENS_DIR = Path(os.path.expanduser("~/projects/inbox/tokens"))
WINDOW_DAYS = 60


def _refresh(tok: dict) -> str:
    r = requests.post(tok["token_uri"], data={
        "client_id": tok["client_id"],
        "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_events_for_account(account: str, days: int = WINDOW_DAYS) -> list[dict]:
    tok_path = TOKENS_DIR / f"{account}.json"
    if not tok_path.exists():
        print(f"[calendar] no token for {account}")
        return []
    tok = json.loads(tok_path.read_text())
    access = _refresh(tok)

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days)).isoformat()
    time_max = (now + timedelta(days=days)).isoformat()  # include upcoming too

    out, page_token = [], None
    while True:
        params = {
            "timeMin": time_min, "timeMax": time_max,
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
        }
        if page_token: params["pageToken"] = page_token
        r = requests.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {access}"}, params=params, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for ev in data.get("items", []):
            if ev.get("status") == "cancelled": continue
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
            if not start: continue
            out.append({
                "id":         f"gcal:{account}:{ev['id']}",
                "summary":    ev.get("summary", ""),
                "description": ev.get("description", ""),
                "start":      start,
                "organizer":  (ev.get("organizer") or {}).get("email", ""),
                "creator":    (ev.get("creator") or {}).get("email", ""),
                "attendees":  [a.get("email", "") for a in ev.get("attendees", []) if a.get("email")],
                "account":    account,
            })
        page_token = data.get("nextPageToken")
        if not page_token: break
    return out


def fetch_all_events(days: int = WINDOW_DAYS) -> list[dict]:
    accounts = [p.stem for p in TOKENS_DIR.glob("*.json") if not p.name.endswith(".lock")]
    seen, out = set(), []
    for acc in accounts:
        try:
            for ev in fetch_events_for_account(acc, days=days):
                if ev["id"] in seen: continue
                seen.add(ev["id"])
                out.append(ev)
        except Exception as e:
            print(f"[calendar] {acc}: {e}")
    return out


if __name__ == "__main__":
    evs = fetch_all_events()
    print(f"[calendar] fetched {len(evs)} events")
    for ev in evs[:5]:
        print(f"  {ev['start'][:16]}  {ev['summary'][:50]:50s}  attendees={len(ev['attendees'])}  org={ev['organizer'][:30]}")
