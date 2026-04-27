from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

trigger_server = importlib.import_module("trigger_server")


def test_health_reports_inbox_availability():
    client = TestClient(trigger_server.app)
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "inbox_available" in body


def test_diagnostics_shape():
    client = TestClient(trigger_server.app)
    res = client.get("/diagnostics")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "policy_mode" in body
    assert "keys_present" in body
    assert "files" in body
    assert "actions_jsonl" in body["files"]
    assert "live_imessage_handles" in body


def test_inbox_degraded_endpoints_return_503(monkeypatch):
    client = TestClient(trigger_server.app)
    monkeypatch.setattr(trigger_server, "_INBOX_IMPORT_ERR", "simulated import failure")

    recall_res = client.post("/recall", json={"name": "Daniel"})
    assert recall_res.status_code == 503
    assert "unavailable" in recall_res.json()["detail"]

    tick_res = client.post("/tick")
    assert tick_res.status_code == 503

    audio_res = client.post("/audio", content=b"\x00\x00")
    assert audio_res.status_code == 503


def test_proposal_endpoints(monkeypatch):
    client = TestClient(trigger_server.app)

    monkeypatch.setattr(
        "action_runtime.list_pending_proposals",
        lambda: [{"id": "p1", "action": "create_reminder", "status": "proposed"}],
    )
    list_res = client.get("/proposals")
    assert list_res.status_code == 200
    assert list_res.json()["proposals"][0]["id"] == "p1"

    monkeypatch.setattr(
        "action_runtime.confirm_proposal",
        lambda proposal_id: {"ok": True, "status": "fired", "proposal_id": proposal_id},
    )
    confirm_res = client.post("/proposals/p1/confirm")
    assert confirm_res.status_code == 200
    assert confirm_res.json()["status"] == "fired"

    monkeypatch.setattr(
        "action_runtime.reject_proposal",
        lambda proposal_id, reason="user_rejected": {
            "ok": True,
            "status": "rejected",
            "proposal_id": proposal_id,
            "reason": reason,
        },
    )
    reject_res = client.post("/proposals/p1/reject", json={"reason": "user cancelled"})
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "rejected"


def test_audit_and_action_history_endpoints(monkeypatch):
    client = TestClient(trigger_server.app)

    monkeypatch.setattr(
        "audit.summary",
        lambda: {"total": 2, "by_decision": {"fired": 1, "suppressed": 1}},
    )
    audit_res = client.get("/audit/summary")
    assert audit_res.status_code == 200
    assert audit_res.json()["summary"]["total"] == 2

    monkeypatch.setattr(
        "actions.recent_actions",
        lambda limit=50: [{"id": 1, "action": "send_imessage", "record": {"fired": False}}],
    )
    actions_res = client.get("/actions/recent?limit=5")
    assert actions_res.status_code == 200
    assert actions_res.json()["actions"][0]["action"] == "send_imessage"

    monkeypatch.setattr(
        "actions.list_memory_edges",
        lambda limit=50: [{"id": 1, "subject": "wearer", "relation": "confirmed_target_for"}],
    )
    edges_res = client.get("/memory/edges")
    assert edges_res.status_code == 200
    assert edges_res.json()["edges"][0]["subject"] == "wearer"

    monkeypatch.setattr(
        "actions.list_memories",
        lambda query=None: {"action": "list_memories", "items": [{"subject": "Sanjay"}]},
    )
    memories_res = client.get("/memories?query=Sanjay")
    assert memories_res.status_code == 200
    assert memories_res.json()["result"]["items"][0]["subject"] == "Sanjay"


def test_scheduled_imessage_endpoints(monkeypatch):
    client = TestClient(trigger_server.app)

    monkeypatch.setattr(
        "actions.list_scheduled_imessages",
        lambda limit=50: [{"id": 1, "handle": "+15551234567", "status": "scheduled"}],
    )
    list_res = client.get("/scheduled-imessages")
    assert list_res.status_code == 200
    assert list_res.json()["scheduled"][0]["status"] == "scheduled"

    monkeypatch.setattr(
        "actions.send_due_imessages",
        lambda: [{"job_id": 1, "status": "sent"}],
    )
    due_res = client.post("/scheduled-imessages/run-due")
    assert due_res.status_code == 200
    assert due_res.json()["sent"][0]["status"] == "sent"


def test_transcript_endpoint_accepts_perfect_asr_event(monkeypatch):
    client = TestClient(trigger_server.app)

    monkeypatch.setattr("actions.lookup_contact", lambda name: {"phone": "+15551234567"})
    monkeypatch.setattr(
        "action_runtime.evaluate_and_dispatch",
        lambda action, payload, transcript="", confidence=None: {
            "ok": True,
            "status": "proposed",
            "action": action,
            "payload": payload,
        },
    )

    res = client.post(
        "/transcript",
        json={
            "text": "I'll text Tarun the demo tonight",
            "event": {
                "action": "schedule_imessage",
                "payload": {"handle": "Tarun", "text": "demo link", "send_at": "2026-04-27T20:00:00-07:00"},
                "confidence": 0.94,
            },
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["result"]["status"] == "proposed"
    assert body["event"]["payload"]["handle"] == "+15551234567"
