from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import event_extractor


class _Resp:
    def __init__(self, status_code: int, content: str):
        self.status_code = status_code
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_low_confidence_write_returns_none(monkeypatch):
    body = '{"action":"create_reminder","payload":{"title":"Call mom"},"confidence":0.4,"reason":"weak"}'
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(event_extractor.httpx, "post", lambda *args, **kwargs: _Resp(200, body))
    assert event_extractor.extract("Remind me to call mom tomorrow") is None


def test_remember_fact_needs_higher_threshold(monkeypatch):
    body = '{"action":"remember_fact","payload":{"subject":"Sarah","fact":"got married last summer"},"confidence":0.8,"reason":"fact"}'
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(event_extractor.httpx, "post", lambda *args, **kwargs: _Resp(200, body))
    assert event_extractor.extract("Sarah got married last summer") is None


def test_confident_action_passes(monkeypatch):
    body = '{"action":"create_reminder","payload":{"title":"Call mom","due":"tomorrow"},"confidence":0.92,"reason":"clear"}'
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(event_extractor.httpx, "post", lambda *args, **kwargs: _Resp(200, body))
    out = event_extractor.extract("Remind me to call mom tomorrow")
    assert out is not None
    assert out["action"] == "create_reminder"


def test_list_response_uses_first_non_none(monkeypatch):
    body = '[{"action":"none","payload":{},"confidence":1.0},{"action":"add_note","payload":{"title":"x","body":"y"},"confidence":0.9}]'
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(event_extractor.httpx, "post", lambda *args, **kwargs: _Resp(200, body))
    out = event_extractor.extract("note this down")
    assert out is not None
    assert out["action"] == "add_note"
