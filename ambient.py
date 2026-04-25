"""Ambient loop — proactive nudges for upcoming events, with memory.

For each upcoming event:
  1. Compute departure time (if it has a location).
  2. Pull related recent messages from inbox (iMessage/Gmail/Notes).
  3. Have Claude synthesize a one-line whisper.
  4. Emit through the output adapter (terminal+TTS today, G2 tomorrow).

Run:
    cd ~/projects/agihouse
    uv run --with anthropic python ambient.py            # forever, 60s tick
    uv run --with anthropic python ambient.py --once     # one tick, then exit
    uv run --with anthropic python ambient.py --once --force --include-far  # debug
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Make sibling inbox project importable -------------------------------
INBOX_DIR = Path.home() / "projects" / "inbox"
if str(INBOX_DIR) not in sys.path:
    sys.path.insert(0, str(INBOX_DIR))
os.chdir(INBOX_DIR)  # services.py uses BASE_DIR-relative paths

from services import (  # noqa: E402
    calendar_events,
    departure_times_for_events,
    get_current_location,
    google_auth_all,
)

sys.path.insert(0, str(Path(__file__).parent))
from context import gather_for_event, render_for_prompt  # noqa: E402
from llm import synthesize_nudge  # noqa: E402
from output import notify  # noqa: E402

STATE_PATH = Path(__file__).parent / "state.json"
TICK_SECONDS = 60
LOOKAHEAD_HOURS = 6
NUDGE_WINDOW_MIN = 60          # synthesize a nudge once event is within this window
DEPART_WINDOW_MIN = 45         # also nudge when departure is within this window
HOME = os.environ.get("INBOX_HOME_ADDRESS", "").strip()


# --- State (dedup) -------------------------------------------------------
def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"alerted": []}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


def _event_key(event) -> str:
    return f"{event.event_id or event.summary}:{event.start.isoformat()}"


# --- One tick -------------------------------------------------------------
def tick(state: dict, *, force: bool = False, include_far: bool = False) -> None:
    gmail_svcs, cal_svcs, *_ = google_auth_all()
    if not cal_svcs:
        notify("No Google Calendar accounts authed — open inbox.py and re-auth first.", speak=False)
        return

    now = datetime.now(timezone.utc).astimezone()
    end = now + timedelta(hours=LOOKAHEAD_HOURS)
    events = calendar_events(cal_svcs, start_date=now, end_date=end)
    events = [e for e in events if not e.all_day and e.start > now]

    origin = get_current_location() or HOME
    deps_by_key = {}
    if origin:
        for d in departure_times_for_events(
            events, origin=origin, mode="driving",
            buffer_minutes=10, lookahead_hours=LOOKAHEAD_HOURS,
        ):
            deps_by_key[f"{d.event_summary}:{d.event_start.isoformat()}"] = d

    alerted = set(state.get("alerted", []))

    for event in events:
        key = _event_key(event)
        if key in alerted and not force:
            continue

        starts_in = (event.start - now).total_seconds() / 60
        dep = deps_by_key.get(f"{event.summary}:{event.start.isoformat()}")
        depart_in = ((dep.departure_time - now).total_seconds() / 60) if dep else None

        # When to fire: event soon, OR we need to leave soon.
        within_event = starts_in <= NUDGE_WINDOW_MIN
        within_depart = depart_in is not None and depart_in <= DEPART_WINDOW_MIN
        if not (within_event or within_depart or include_far):
            continue

        # Build context from inbox memory.
        try:
            items = gather_for_event(event, gmail_services=gmail_svcs, cal_services=cal_svcs)
        except Exception as e:
            print(f"[ambient] context lookup failed for {event.summary!r}: {e!r}", file=sys.stderr)
            items = []
        context_block = render_for_prompt(items)

        # Departure line (deterministic — Claude shouldn't paraphrase travel time).
        if dep:
            if depart_in is not None and depart_in <= 0:
                dep_line = f"Leave NOW — {dep.duration_text} to {dep.event_location}."
            else:
                dep_line = (
                    f"Leave in {int(depart_in)} min — {dep.duration_text} "
                    f"({dep.distance_text}) to {dep.event_location}."
                )
        else:
            dep_line = ""

        # Ask Claude for the memory whisper.
        try:
            whisper = synthesize_nudge(
                event_summary=event.summary,
                starts_in_minutes=int(starts_in),
                location=event.location,
                departure_line=dep_line,
                context_block=context_block,
            )
        except Exception as e:
            print(f"[ambient] llm failed: {e!r}", file=sys.stderr)
            whisper = None

        # Emit. Departure line is the spine; whisper is the memory layer on top.
        parts = []
        if dep_line:
            parts.append(dep_line)
        elif within_event:
            parts.append(f"In {int(starts_in)} min: {event.summary}.")
        if whisper:
            parts.append(whisper)

        if parts:
            notify(" ".join(parts))
            alerted.add(key)

    state["alerted"] = sorted(alerted)
    _save_state(state)


# --- Entry point ----------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore dedup state")
    p.add_argument("--include-far", action="store_true",
                   help="nudge for all upcoming events, ignore time windows (debug)")
    args = p.parse_args()

    state = _load_state()
    if args.force:
        state["alerted"] = []

    if args.once:
        tick(state, force=args.force, include_far=args.include_far)
        return

    while True:
        try:
            tick(state)
        except Exception as e:
            print(f"[ambient] tick failed: {e!r}", file=sys.stderr)
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
