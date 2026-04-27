"""Tests for inbox.g2.actions dispatch, gating, and backing-service rewiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from g2 import actions, audit  # noqa: E402


_BASE_POLICY: dict = {
    "mode": "live",
    "log_path": "audit.log",
    "allowed_actions": [
        "create_reminder",
        "list_reminders",
        "add_calendar_event",
        "list_calendar",
        "add_note",
        "list_notes",
        "remember_fact",
        "list_memories",
        "send_imessage",
        "answer_question",
        "recall_person",
    ],
    "denied_actions": ["send_email", "delete_anything"],
    "restraint": {
        "recall_cooldown_seconds": 0,
        "require_prior_correspondence": False,
        "max_proposals_per_minute": 0,
    },
    "privacy": {
        "denylist_names": [],
        "denylist_keywords": [],
        "suppress_in_contexts": [],
    },
}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Tmp policy + log + sandbox dir; resets all in-memory action wiring."""
    policy_path = tmp_path / "policy.yaml"
    log_path = tmp_path / "audit.log"
    pol = dict(_BASE_POLICY)
    pol["log_path"] = str(log_path)
    policy_path.write_text(yaml.safe_dump(pol))

    monkeypatch.setattr(audit, "_POLICY_PATH", policy_path)
    monkeypatch.setattr(audit, "_DIR", tmp_path)
    audit.reset_state_for_tests()

    sandbox = tmp_path / "inbox_state"
    sandbox.mkdir()
    monkeypatch.setattr(actions, "_INBOX_DIR", sandbox)
    monkeypatch.setattr(actions, "ACTIONS_LOG", sandbox / "actions.jsonl")
    monkeypatch.setattr(actions, "NOTES_PATH", sandbox / "notes.json")
    monkeypatch.setattr(actions, "CALENDAR_SANDBOX", sandbox / "calendar.json")
    monkeypatch.setattr(actions, "LEGACY_MEMORIES", sandbox / "memories.jsonl")
    monkeypatch.setattr(actions, "CONTACTS_PATH", sandbox / "contacts.json")
    monkeypatch.setattr(actions, "_loop", None)
    monkeypatch.setattr(actions, "_blackboard", None)
    monkeypatch.setattr(actions, "LIVE_IMESSAGE_HANDLES", set())

    def _write_policy(updates: dict) -> None:
        merged = dict(_BASE_POLICY)
        for k, v in updates.items():
            merged[k] = v
        merged["log_path"] = str(log_path)
        policy_path.write_text(yaml.safe_dump(merged))
        audit.reset_state_for_tests()

    return _write_policy, sandbox


def _read_actions_log(sandbox: Path) -> list[dict]:
    p = sandbox / "actions.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_create_reminder_calls_services(isolated):
    write_policy, sandbox = isolated
    fake_services = MagicMock()
    fake_services.reminder_create.return_value = True

    with patch.dict(sys.modules, {"services": fake_services}):
        rec = actions.create_reminder(title="buy milk", due="tomorrow 5pm")

    assert rec["fired"] is True
    assert rec["title"] == "buy milk"
    fake_services.reminder_create.assert_called_once_with(title="buy milk", due_date="tomorrow 5pm")
    log = _read_actions_log(sandbox)
    assert any(r["action"] == "create_reminder" and r["fired"] for r in log)


def test_create_reminder_blocked_by_policy(isolated):
    write_policy, sandbox = isolated
    write_policy({"allowed_actions": []})
    rec = actions.create_reminder(title="x")
    assert rec["fired"] is False
    assert rec["decision"] == "suppressed"


def test_send_imessage_dryruns_unlisted_handle(isolated):
    write_policy, sandbox = isolated
    fake_services = MagicMock()

    with patch.dict(sys.modules, {"services": fake_services}):
        rec = actions.send_imessage(handle="+15555550001", text="hi")

    assert rec["fired"] is False
    assert "LIVE_IMESSAGE_HANDLES" in rec["reason"]
    fake_services.imsg_send.assert_not_called()


def test_send_imessage_fires_for_allow_listed_handle(isolated, monkeypatch):
    write_policy, sandbox = isolated
    monkeypatch.setattr(actions, "LIVE_IMESSAGE_HANDLES", {"+15555550001"})

    fake_services = MagicMock()
    fake_services.imsg_send.return_value = True

    class _Contact:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    fake_services.Contact = _Contact

    with patch.dict(sys.modules, {"services": fake_services}):
        rec = actions.send_imessage(handle="+15555550001", text="hi")

    assert rec["fired"] is True
    fake_services.imsg_send.assert_called_once()
    sent_contact, sent_text = fake_services.imsg_send.call_args[0]
    assert sent_contact.guid == "+15555550001"
    assert sent_text == "hi"


def test_send_email_always_dry_run(isolated):
    _, sandbox = isolated
    rec = actions.send_email(to="x@y.com", subject="s", body="b")
    assert rec["fired"] is False
    log = _read_actions_log(sandbox)
    assert any(r["action"] == "send_email" and r["fired"] is False for r in log)


def test_dispatch_unknown_action_logs(isolated):
    _, sandbox = isolated
    rec = actions.dispatch("nuclear_launch", {})
    assert rec["fired"] is False
    assert "unknown action" in rec["reason"]


def test_dispatch_routes_to_create_reminder(isolated):
    _, sandbox = isolated
    fake_services = MagicMock()
    fake_services.reminder_create.return_value = True

    with patch.dict(sys.modules, {"services": fake_services}):
        rec = actions.dispatch("create_reminder", {"title": "x"})

    assert rec["action"] == "create_reminder"
    assert rec["fired"] is True


def test_add_calendar_event_writes_sandbox(isolated):
    _, sandbox = isolated
    rec = actions.add_calendar_event(title="standup", when="tomorrow 9am")
    assert rec["fired"] is True
    cal = json.loads((sandbox / "calendar.json").read_text())
    assert cal[-1]["title"] == "standup"


def test_add_note_persists_to_sandbox(isolated):
    _, sandbox = isolated
    rec = actions.add_note(title="todo", body="finish demo")
    assert rec["fired"] is True
    notes = json.loads((sandbox / "notes.json").read_text())
    assert notes[-1] == {"title": "todo", "body": "finish demo", "created": rec["created"]}


def test_remember_fact_writes_legacy_and_memory_store(isolated):
    _, sandbox = isolated
    fake_module = MagicMock()
    fake_store = MagicMock()
    fake_store.save_entry.return_value = {"id": 7}
    fake_module.MemoryStore.return_value = fake_store

    with patch.dict(sys.modules, {"memory_store": fake_module}):
        rec = actions.remember_fact(subject="Anita", fact="loves matcha")

    assert rec["fired"] is True
    assert rec["memory_id"] == 7
    fake_store.save_entry.assert_called_once()
    # legacy file mirror
    legacy = (sandbox / "memories.jsonl").read_text().splitlines()
    assert any("Anita" in line for line in legacy)


def test_list_memories_falls_back_to_legacy(isolated):
    _, sandbox = isolated
    (sandbox / "memories.jsonl").write_text(
        json.dumps({"subject": "Bo", "fact": "vegan", "created": 0}) + "\n"
    )
    # Force MemoryStore import to fail
    with patch.dict(sys.modules, {"memory_store": None}):
        rec = actions.list_memories(query="vegan")

    assert rec["fired"] is True
    assert rec["count"] >= 1


def test_dispatch_swallows_action_exceptions(isolated):
    _, sandbox = isolated
    fake_services = MagicMock()
    fake_services.reminder_create.side_effect = RuntimeError("boom")

    with patch.dict(sys.modules, {"services": fake_services}):
        rec = actions.dispatch("create_reminder", {"title": "x"})

    # Action records reason but NEVER raises
    assert rec["action"] == "create_reminder"
    assert rec["fired"] is False
    assert "boom" in rec.get("reason", "")


def test_recent_actions_returns_tail(isolated):
    _, sandbox = isolated
    actions._log({"action": "x", "fired": True})
    actions._log({"action": "y", "fired": True})
    actions._log({"action": "z", "fired": False})
    rows = actions.recent_actions(limit=2)
    assert [r["action"] for r in rows] == ["y", "z"]


def test_dry_run_mode_marks_imessage_dry(isolated):
    write_policy, sandbox = isolated
    write_policy({"mode": "dry_run"})
    actions.LIVE_IMESSAGE_HANDLES.add("+15555550001")
    fake_services = MagicMock()

    with patch.dict(sys.modules, {"services": fake_services}):
        rec = actions.send_imessage(handle="+15555550001", text="hi")

    assert rec["fired"] is False
    assert "dry_run" in rec["reason"]
    fake_services.imsg_send.assert_not_called()
