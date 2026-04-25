"""Per-event context gathering — pulls related messages/notes from inbox.

Given an upcoming CalendarEvent, returns a compact list of relevant items
across iMessage, Gmail, Notes, Reminders. Used by ambient.py to feed Claude
when building a nudge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services import search_all  # type: ignore[import-not-found]

_STOP = {
    "the", "a", "an", "and", "or", "for", "with", "at", "in", "on", "to",
    "of", "by", "from", "is", "are", "be", "meeting", "call", "sync",
    "lunch", "dinner", "coffee", "chat", "1:1", "1on1", "check-in", "checkin",
}


@dataclass
class ContextItem:
    source: str        # imessage / gmail / notes / reminders
    sender: str
    timestamp: str
    snippet: str


def _topic_terms(summary: str) -> list[str]:
    """Pick salient words from an event title."""
    words = re.findall(r"[A-Za-z0-9']+", summary.lower())
    return [w for w in words if len(w) > 2 and w not in _STOP][:4]


def _attendee_emails(event) -> list[str]:
    out = []
    for a in (getattr(event, "attendees", None) or []):
        email = (a.get("email") or "").strip()
        if email and not email.endswith("@group.calendar.google.com"):
            out.append(email)
    return out[:5]


def gather_for_event(
    event,
    *,
    gmail_services: dict,
    cal_services: dict,
    limit: int = 4,
) -> list[ContextItem]:
    """Collect recent items related to this event.

    Strategy: for each (attendee, topic) pair search inbox, dedup, take freshest.
    Fast and dumb — relies on services.search_all which is already indexed.
    """
    queries: list[tuple[str, str]] = []  # (query, from_addr filter)
    terms = _topic_terms(event.summary)
    topic = " ".join(terms[:2]) if terms else event.summary

    attendees = _attendee_emails(event)
    if attendees:
        for email in attendees:
            queries.append((topic or email.split("@")[0], email))
    else:
        queries.append((topic or event.summary, ""))

    seen: set[tuple[str, str]] = set()
    items: list[ContextItem] = []

    for q, from_addr in queries:
        if not q.strip():
            continue
        try:
            res = search_all(
                q,
                sources=["imessage", "gmail", "notes"],
                limit=limit,
                gmail_services=gmail_services,
                cal_services=cal_services,
                from_addr=from_addr,
            )
        except Exception:
            continue
        for r in res.get("results", [])[:limit]:
            key = (r.get("source", ""), r.get("id", "") or r.get("snippet", "")[:60])
            if key in seen:
                continue
            seen.add(key)
            items.append(
                ContextItem(
                    source=r.get("source", ""),
                    sender=r.get("sender", "") or r.get("from", ""),
                    timestamp=r.get("timestamp", ""),
                    snippet=(r.get("snippet") or r.get("body") or "").strip()[:280],
                )
            )

    items.sort(key=lambda i: i.timestamp, reverse=True)
    return items[:limit]


def render_for_prompt(items: list[ContextItem]) -> str:
    if not items:
        return "(no related messages found)"
    lines = []
    for it in items:
        who = it.sender or "?"
        when = it.timestamp[:16].replace("T", " ") if it.timestamp else ""
        lines.append(f"- [{it.source}] {who} {when}: {it.snippet}")
    return "\n".join(lines)
