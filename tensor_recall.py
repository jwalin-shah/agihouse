"""One-line wrappers around the tensor-logic demo store."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

STORE = Path(__file__).parent / "demos" / "assistant_store.pt"


def _capture_query(mode: str, topic: str | None = None) -> str | None:
    if not STORE.exists():
        return None
    try:
        from demos import assistant_query

        store = assistant_query.load_store()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if mode == "meetings":
                assistant_query.from_meeting_contacts(store, topic_query=topic, k=3)
            elif mode == "upcoming":
                assistant_query.upcoming_events_with_msgs(store, k=3)
            else:
                assistant_query.followups(store, topic_query=topic, k=3)
    except Exception:
        return None

    lines = [line.strip(" │") for line in buf.getvalue().splitlines()]
    sender = next((line.split("sender:", 1)[1].strip() for line in lines if "sender:" in line), "")
    snippet = next((line.split("snippet:", 1)[1].strip().strip("'") for line in lines if "snippet:" in line), "")
    event = next((line[2:].strip() for line in lines if line.startswith("📅 ")), "")
    if mode == "upcoming" and event:
        return f"TL upcoming: {event[:110]}"
    if sender and snippet:
        label = "TL meetings" if mode == "meetings" else "TL followup"
        return f"{label}: {sender} — {snippet[:120]}"
    return None


def maybe_tensor_oneliner(transcript: str) -> str | None:
    text = transcript.lower()
    topic = None
    match = re.search(r"\b(?:about|on)\s+([a-z][a-z0-9 .'-]{1,40})", text)
    if match:
        topic = match.group(1).strip()

    if "upcoming" in text and ("message" in text or "context" in text or "event" in text):
        return _capture_query("upcoming")
    if "meeting" in text and ("message" in text or "contact" in text or "context" in text):
        return _capture_query("meetings", topic)
    if "followup" in text or "follow up" in text or "unanswered" in text:
        return _capture_query("followups", topic)
    return None
