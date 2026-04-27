"""Transcript -> structured action via OpenRouter (routed through ``g2.llm``).

Given a short ASR transcript the extractor returns either ``None`` (no
actionable event) or a payload usable by :func:`g2.actions.dispatch`::

    {
        "action": "create_reminder",
        "payload": {...},
        "confidence": 0.0-1.0,
        "reason": "..."
    }

Action types correspond to ``g2.actions.DISPATCH`` keys. We threshold on
confidence so ambient ramble doesn't produce spurious proposals; the
``remember_fact`` floor is higher because hallucinated facts are the worst
possible failure mode.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from .config import EXTRACTOR_SYSTEM, settings
from .llm import call_llm, strip_code_fence

_ALLOWED_ACTIONS: set[str] = {
    "send_imessage",
    "create_reminder",
    "add_calendar_event",
    "add_note",
    "send_email",
    "list_reminders",
    "list_calendar",
    "list_notes",
    "list_memories",
    "remember_fact",
    "answer_question",
}

# Lowercase first-person markers used as a soft filter for *write* actions.
# Matches "I", "I'm", "I'll", "I want", "remind me", "let me", "my", "we are",
# "we're". Without one of these we suppress write verbs as a guard against the
# extractor confabulating a wearer-directive from third-party speech.
_FIRST_PERSON_RE = re.compile(
    r"\b(i'?m|i'?ll|i\s+(?:want|need|should|will|am|have|gotta|got\s+to)|"
    r"remind\s+me|let\s+me|my\b|we\s+are|we'?re|we'?ve|we\s+have)\b",
    re.IGNORECASE,
)
_WRITE_ACTIONS = {
    "send_imessage",
    "create_reminder",
    "add_calendar_event",
    "add_note",
    "send_email",
}


def _strip_to_json(text: str) -> str:
    text = strip_code_fence(text)
    # Some models prefix JSON with leading commentary; chop everything before
    # the first opening brace.
    idx = text.find("{")
    return text[idx:] if idx > 0 else text


async def extract(transcript: str, *, model: str | None = None) -> dict[str, Any] | None:
    """Return action dict if confident, else ``None``."""
    transcript = (transcript or "").strip()
    if len(transcript) < settings.extractor_min_chars:
        return None

    raw = await call_llm(
        prompt=transcript,
        system=EXTRACTOR_SYSTEM,
        model=model or settings.model_extractor,
        max_tokens=256,
        temperature=0.0,
    )
    if not raw:
        return None

    try:
        obj = json.loads(_strip_to_json(raw))
    except json.JSONDecodeError:
        logger.debug(f"[extractor] non-JSON response: {raw[:200]!r}")
        return None

    if isinstance(obj, list):
        obj = next(
            (x for x in obj if isinstance(x, dict) and x.get("action") and x.get("action") != "none"),
            None,
        )
        if obj is None:
            return None
    if not isinstance(obj, dict):
        return None

    action = obj.get("action")
    if action in (None, "none", ""):
        return None
    if action not in _ALLOWED_ACTIONS:
        logger.debug(f"[extractor] dropping unknown action {action!r}")
        return None

    try:
        confidence = float(obj.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    needed = (
        settings.extractor_remember_fact_threshold
        if action == "remember_fact"
        else settings.extractor_confidence_threshold
    )
    if confidence < needed:
        return None

    if action in _WRITE_ACTIONS and not _FIRST_PERSON_RE.search(transcript):
        logger.debug(
            f"[extractor] suppressing {action!r} (no first-person marker in {transcript[:60]!r})"
        )
        return None

    payload = obj.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    return {
        "action": action,
        "payload": payload,
        "confidence": confidence,
        "reason": str(obj.get("reason", "") or ""),
    }
