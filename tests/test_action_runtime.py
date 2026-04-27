from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import action_runtime


def test_invalid_payload_is_suppressed():
    res = action_runtime.evaluate_and_dispatch(
        "create_reminder",
        {"due": "tomorrow"},
        transcript="remind me",
        proposal_only=True,
    )
    assert res["ok"] is False
    assert res["status"] == "suppressed"
    assert res["reason"] == "invalid_payload"


def test_policy_denial_suppresses(monkeypatch):
    class Deny:
        allow = False
        reason = "blocked by policy"

    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Deny())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)

    res = action_runtime.evaluate_and_dispatch(
        "create_reminder",
        {"title": "Call mom"},
        transcript="remind me to call mom",
    )
    assert res["ok"] is False
    assert res["status"] == "suppressed"
    assert "blocked" in res["reason"]


def test_proposal_mode_does_not_execute(monkeypatch):
    class Allow:
        allow = True
        reason = "ok"

    called = {"dispatch": 0}
    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Allow())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "raw_dispatch", lambda action, payload: called.__setitem__("dispatch", 1))

    res = action_runtime.evaluate_and_dispatch(
        "create_reminder",
        {"title": "Call mom"},
        proposal_only=True,
    )
    assert res["ok"] is True
    assert res["status"] == "proposed"
    assert res["preview"] == "Reminder: Call mom"
    assert called["dispatch"] == 0


def test_allowed_execution_calls_dispatch(monkeypatch):
    class Allow:
        allow = True
        reason = "ok"

    called = {"dispatch": 0}
    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Allow())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "mark_fired", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "learn_from_proposal_feedback", lambda **kwargs: [{"subject": "wearer"}])
    monkeypatch.setattr(action_runtime, "learn_from_proposal_feedback", lambda **kwargs: [{"subject": "wearer"}])
    monkeypatch.setattr(action_runtime, "learn_from_proposal_feedback", lambda **kwargs: [])

    def fake_dispatch(action, payload):
        called["dispatch"] += 1
        return {"ok": True, "action": action}

    monkeypatch.setattr(action_runtime, "raw_dispatch", fake_dispatch)
    res = action_runtime.evaluate_and_dispatch(
        "list_reminders",
        {"query": "mom"},
        proposal_only=False,
    )
    assert res["ok"] is True
    assert res["status"] == "fired"
    assert called["dispatch"] == 1


def test_policy_auto_fire_list_action_by_default(monkeypatch):
    class Allow:
        allow = True
        reason = "ok"

    called = {"dispatch": 0}
    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Allow())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "mark_fired", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "learn_from_proposal_feedback", lambda **kwargs: [])
    monkeypatch.setattr(
        action_runtime,
        "policy",
        lambda: {"execution": {"auto_fire_actions": ["list_reminders"]}},
    )

    def fake_dispatch(action, payload):
        called["dispatch"] += 1
        return {"ok": True, "action": action}

    monkeypatch.setattr(action_runtime, "raw_dispatch", fake_dispatch)
    res = action_runtime.evaluate_and_dispatch("list_reminders", {"query": "mom"})
    assert res["ok"] is True
    assert res["status"] == "fired"
    assert called["dispatch"] == 1


def test_confirm_proposal_executes_pending_action(monkeypatch):
    class Allow:
        allow = True
        reason = "ok"

    called = {"dispatch": 0}
    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Allow())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "mark_fired", lambda *args, **kwargs: None)

    def fake_dispatch(action, payload):
        called["dispatch"] += 1
        return {"ok": True, "action": action, "payload": payload}

    monkeypatch.setattr(action_runtime, "raw_dispatch", fake_dispatch)
    proposed = action_runtime.evaluate_and_dispatch(
        "send_imessage",
        {"handle": "+15551234567", "text": "demo link"},
        proposal_only=True,
    )

    confirmed = action_runtime.confirm_proposal(proposed["proposal_id"])
    assert confirmed["ok"] is True
    assert confirmed["status"] == "fired"
    assert called["dispatch"] == 1
    assert any(edge["subject"] == "wearer" for edge in confirmed["learned"])
    assert any(edge["subject"] == "demo_link" for edge in confirmed["learned"])


def test_confirm_and_reject_proposal(monkeypatch):
    class Allow:
        allow = True
        reason = "ok"

    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Allow())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "mark_fired", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "learn_from_proposal_feedback", lambda **kwargs: [])
    monkeypatch.setattr(action_runtime, "raw_dispatch", lambda action, payload: {"ok": True, "action": action})

    created = action_runtime.evaluate_and_dispatch(
        "create_reminder",
        {"title": "Call mom"},
        proposal_only=True,
    )
    pid = created["proposal_id"]
    confirmed = action_runtime.confirm_proposal(pid)
    assert confirmed["ok"] is True
    assert confirmed["status"] == "fired"

    created2 = action_runtime.evaluate_and_dispatch(
        "create_reminder",
        {"title": "Second"},
        proposal_only=True,
    )
    pid2 = created2["proposal_id"]
    rejected = action_runtime.reject_proposal(pid2, reason="not now")
    assert rejected["ok"] is True
    assert rejected["status"] == "rejected"


def test_schedule_imessage_is_write_proposal(monkeypatch):
    class Allow:
        allow = True
        reason = "ok"

    called = {"dispatch": 0}
    monkeypatch.setattr(action_runtime, "gate", lambda action, **ctx: Allow())
    monkeypatch.setattr(action_runtime, "notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(action_runtime, "raw_dispatch", lambda action, payload: called.__setitem__("dispatch", 1))

    res = action_runtime.evaluate_and_dispatch(
        "schedule_imessage",
        {"handle": "+15551234567", "text": "demo link", "send_at": "2026-04-27T20:00:00-07:00"},
        proposal_only=True,
    )

    assert res["ok"] is True
    assert res["status"] == "proposed"
    assert "Schedule message" in res["preview"]
    assert called["dispatch"] == 0
