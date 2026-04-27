"""Tests for inbox.g2.extractor — confidence floors and first-person filter."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from g2 import extractor  # noqa: E402


def _llm_returns(payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    return AsyncMock(return_value=payload)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_extractor_returns_none_for_short_input():
    out = _run(extractor.extract("hi"))
    assert out is None


def test_extractor_drops_action_below_threshold():
    payload = {
        "action": "create_reminder",
        "payload": {"title": "x"},
        "confidence": 0.4,
        "reason": "low",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("remind me to buy milk"))
    assert out is None


def test_extractor_returns_action_above_threshold():
    payload = {
        "action": "create_reminder",
        "payload": {"title": "buy milk", "due": None},
        "confidence": 0.92,
        "reason": "first-person directive",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("remind me to buy milk tomorrow"))
    assert out is not None
    assert out["action"] == "create_reminder"
    assert out["payload"]["title"] == "buy milk"


def test_extractor_remember_fact_uses_higher_threshold():
    payload = {
        "action": "remember_fact",
        "payload": {"subject": "Anita", "fact": "moved to SF"},
        "confidence": 0.78,
        "reason": "named fact",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("Anita moved to San Francisco last month"))
    assert out is None


def test_extractor_remember_fact_passes_high_confidence():
    payload = {
        "action": "remember_fact",
        "payload": {"subject": "Anita", "fact": "moved to SF"},
        "confidence": 0.93,
        "reason": "named fact",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("Anita moved to San Francisco last month"))
    assert out is not None
    assert out["action"] == "remember_fact"


def test_extractor_first_person_filter_blocks_third_person_writes():
    """Third-person speech ("Tarun is going to text...") must NOT trigger send_imessage."""
    payload = {
        "action": "send_imessage",
        "payload": {"handle": "Tarun", "text": "demo"},
        "confidence": 0.92,
        "reason": "looks directive",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("Tarun is going to text the demo notes later"))
    assert out is None


def test_extractor_first_person_filter_allows_wearer_directive():
    payload = {
        "action": "send_imessage",
        "payload": {"handle": "Tarun", "text": "demo notes"},
        "confidence": 0.92,
        "reason": "first-person",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("I'll text Tarun the demo notes"))
    assert out is not None
    assert out["action"] == "send_imessage"


def test_extractor_first_person_filter_skips_read_actions():
    """READ actions (list_*, answer_question) bypass the first-person regex."""
    payload = {
        "action": "list_reminders",
        "payload": {"query": None},
        "confidence": 0.92,
        "reason": "question to assistant",
    }
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("show me reminders"))
    assert out is not None
    assert out["action"] == "list_reminders"


def test_extractor_handles_code_fence_response():
    raw = "```json\n" + json.dumps({
        "action": "create_reminder",
        "payload": {"title": "buy milk"},
        "confidence": 0.92,
    }) + "\n```"
    with patch("g2.extractor.call_llm", _llm_returns(raw)):
        out = _run(extractor.extract("remind me to buy milk"))
    assert out is not None


def test_extractor_picks_first_concrete_action_from_list():
    raw = json.dumps([
        {"action": "none"},
        {"action": "create_reminder", "payload": {"title": "x"}, "confidence": 0.9},
    ])
    with patch("g2.extractor.call_llm", _llm_returns(raw)):
        out = _run(extractor.extract("remind me about something"))
    assert out is not None
    assert out["action"] == "create_reminder"


def test_extractor_drops_unknown_action():
    payload = {"action": "delete_everything", "payload": {}, "confidence": 0.99}
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("erase everything please"))
    assert out is None


def test_extractor_drops_action_none():
    payload = {"action": "none", "payload": {}, "confidence": 0.99}
    with patch("g2.extractor.call_llm", _llm_returns(payload)):
        out = _run(extractor.extract("just chatting about the weather"))
    assert out is None


def test_extractor_handles_garbage_response():
    with patch("g2.extractor.call_llm", _llm_returns("nope, no JSON here")):
        out = _run(extractor.extract("remind me to buy milk"))
    assert out is None
