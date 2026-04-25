"""Claude wrapper — single call that turns event+context into an ambient whisper.

Uses prompt caching on the system prompt so the per-tick cost is just the
event-specific user message.
"""

from __future__ import annotations

import os

from anthropic import Anthropic

_client: Anthropic | None = None
_MODEL = "claude-haiku-4-5-20251001"  # cheap + fast — we ship many small calls

SYSTEM = """You are an ambient assistant whispering into a heads-up display.

Your job: produce ONE short, useful sentence (max ~22 words) about an
upcoming calendar event, drawing on related recent messages the user got.
Voice is calm, present, helpful. No greetings, no preamble, no emoji.

Pattern when a reply is owed: "<Person> texted yesterday about <thing> — you
haven't replied."
Pattern when there's no signal: state the next concrete prep step or the
most relevant fact from context.
Pattern when nothing useful: respond with the single token "skip".

Never invent facts. If context is empty or irrelevant, output "skip"."""


def _client_singleton() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def synthesize_nudge(
    *,
    event_summary: str,
    starts_in_minutes: int,
    location: str,
    departure_line: str,
    context_block: str,
) -> str | None:
    """Return a one-line nudge, or None to skip this event."""
    user = f"""Event: {event_summary}
Starts in: {starts_in_minutes} minutes
Location: {location or "(none)"}
Departure: {departure_line or "(no travel time computed)"}

Recent related messages:
{context_block}

Write the whisper now."""

    resp = _client_singleton().messages.create(
        model=_MODEL,
        max_tokens=80,
        system=[
            {
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text or text.lower() == "skip":
        return None
    return text
