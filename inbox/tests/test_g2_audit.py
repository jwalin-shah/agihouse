"""Tests for the inbox.g2.audit policy gate.

These tests redirect both the policy.yaml path and the audit log path to a
temporary directory so the real ~/.inbox/audit.log is never touched.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from g2 import audit  # noqa: E402


_BASE_POLICY: dict = {
    "mode": "live",
    "log_path": "audit.log",
    "allowed_actions": [
        "create_reminder",
        "send_imessage",
        "recall_person",
        "answer_question",
    ],
    "denied_actions": ["send_email", "delete_anything"],
    "restraint": {
        "recall_cooldown_seconds": 60,
        "require_prior_correspondence": True,
        "max_proposals_per_minute": 6,
    },
    "privacy": {
        "denylist_names": [],
        "denylist_keywords": ["therapist", "lawyer"],
        "suppress_in_contexts": ["off the record", "between us"],
    },
}


@pytest.fixture
def isolated_policy(tmp_path, monkeypatch):
    """Point audit at a tmp policy.yaml + log file and reset all state."""
    policy_path = tmp_path / "policy.yaml"
    log_path = tmp_path / "audit.log"

    pol = dict(_BASE_POLICY)
    pol["log_path"] = str(log_path)
    policy_path.write_text(yaml.safe_dump(pol))

    monkeypatch.setattr(audit, "_POLICY_PATH", policy_path)
    monkeypatch.setattr(audit, "_DIR", tmp_path)
    audit.reset_state_for_tests()

    def _write_policy(updates: dict) -> None:
        merged = dict(_BASE_POLICY)
        for k, v in updates.items():
            merged[k] = v
        merged["log_path"] = str(log_path)
        policy_path.write_text(yaml.safe_dump(merged))
        # bust the mtime cache
        audit.reset_state_for_tests()

    return _write_policy, log_path


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    import json

    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_allow_listed_action_passes(isolated_policy):
    write, log = isolated_policy
    d = audit.gate("create_reminder", title="buy milk")
    assert d.allow is True
    rows = _read_log(log)
    assert rows[-1]["decision"] == "considered"
    assert rows[-1]["action"] == "create_reminder"


def test_denied_listed_action_blocks(isolated_policy):
    _, log = isolated_policy
    d = audit.gate("send_email", to="x@example.com", body="hello")
    assert d.allow is False
    assert "denied list" in d.reason
    rows = _read_log(log)
    assert rows[-1]["decision"] == "suppressed"


def test_unknown_action_blocks(isolated_policy):
    _, _ = isolated_policy
    d = audit.gate("nuclear_launch")
    assert d.allow is False
    assert "not on allowed list" in d.reason


def test_recall_cooldown_blocks_repeats(isolated_policy):
    write, _ = isolated_policy
    write({"restraint": {**_BASE_POLICY["restraint"], "require_prior_correspondence": False}})

    d1 = audit.gate("recall_person", person="Anita", known_message_count=4)
    assert d1.allow is True
    audit.mark_fired("recall_person", person="Anita")

    d2 = audit.gate("recall_person", person="Anita", known_message_count=4)
    assert d2.allow is False
    assert "cooldown" in d2.reason


def test_recall_requires_prior_correspondence(isolated_policy):
    _, _ = isolated_policy
    d = audit.gate("recall_person", person="Stranger", known_message_count=0)
    assert d.allow is False
    assert "prior correspondence" in d.reason


def test_denylist_keyword_in_transcript_chunk_blocks(isolated_policy):
    _, _ = isolated_policy
    d = audit.gate(
        "create_reminder",
        title="follow up",
        transcript_chunk="see the therapist next Tuesday",
    )
    assert d.allow is False
    assert "denied keyword" in d.reason


def test_sensitive_context_phrase_suppresses(isolated_policy):
    _, _ = isolated_policy
    d = audit.gate(
        "create_reminder",
        title="x",
        transcript_chunk="off the record, remind me to call him",
    )
    assert d.allow is False
    assert "sensitive context" in d.reason


def test_max_proposals_per_minute_ceiling(isolated_policy):
    write, _ = isolated_policy
    write({"restraint": {"recall_cooldown_seconds": 0, "max_proposals_per_minute": 3,
                         "require_prior_correspondence": False}})

    for _ in range(3):
        d = audit.gate("create_reminder", title="x")
        assert d.allow is True
        audit.mark_fired("create_reminder")

    d = audit.gate("create_reminder", title="x")
    assert d.allow is False
    assert "ceiling" in d.reason


def test_dry_run_marks_fired_as_dry_run(isolated_policy):
    write, log = isolated_policy
    write({"mode": "dry_run"})
    assert audit.is_dry_run() is True
    audit.mark_fired("create_reminder", title="x")
    rows = _read_log(log)
    assert any(r["decision"] == "dry_run" for r in rows)


def test_policy_hot_reload_on_mtime_change(isolated_policy):
    write, _ = isolated_policy

    d1 = audit.gate("send_email", to="x")
    assert d1.allow is False

    # Sleep briefly so mtime advances on case-sensitive FS, then move
    # send_email to allowed.
    time.sleep(0.01)
    write({
        "allowed_actions": [*_BASE_POLICY["allowed_actions"], "send_email"],
        "denied_actions": [a for a in _BASE_POLICY["denied_actions"] if a != "send_email"],
    })
    d2 = audit.gate("send_email", to="x")
    assert d2.allow is True


def test_summary_aggregates_decisions(isolated_policy):
    write, log = isolated_policy
    write({"restraint": {"recall_cooldown_seconds": 0,
                         "max_proposals_per_minute": 0,
                         "require_prior_correspondence": False}})

    audit.gate("create_reminder", title="x")
    audit.gate("send_email", to="x")  # suppressed
    audit.mark_fired("create_reminder")

    s = audit.summary()
    assert s["total"] >= 3
    assert "considered" in s["by_decision"]
    assert "suppressed" in s["by_decision"]
    assert "create_reminder" in s["by_action"]
