"""Tests for inbox_client.py — HTTP client with mocked transport."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from inbox_client import InboxClient

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_transport():
    """Mock httpx transport for controlled responses."""
    transport = MagicMock(spec=httpx.BaseTransport)
    return transport


@pytest.fixture
def client():
    """InboxClient with a mocked httpx.Client underneath."""
    c = InboxClient.__new__(InboxClient)
    c._client = MagicMock(spec=httpx.Client)
    return c


def _mock_response(data, status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ── Health ──────────────────────────────────────────────────────────────────


class TestClientHealth:
    def test_health(self, client):
        client._client.get.return_value = _mock_response({"status": "ok"})
        result = client.health()
        assert result == {"status": "ok"}
        client._client.get.assert_called_once_with("/health")

    def test_is_server_running_true(self, client):
        client._client.get.return_value = _mock_response({"status": "ok"})
        assert client.is_server_running() is True

    def test_is_server_running_false_on_connect_error(self, client):
        client._client.get.side_effect = httpx.ConnectError("refused")
        assert client.is_server_running() is False

    def test_is_server_running_false_on_timeout(self, client):
        client._client.get.side_effect = httpx.TimeoutException("timeout")
        assert client.is_server_running() is False


# ── Conversations ───────────────────────────────────────────────────────────


class TestClientConversations:
    def test_conversations(self, client):
        client._client.get.return_value = _mock_response([{"name": "Alice"}])
        result = client.conversations(source="imessage", limit=10)
        assert result == [{"name": "Alice"}]
        client._client.get.assert_called_once_with(
            "/conversations", params={"source": "imessage", "limit": 10}
        )


# ── Messages ────────────────────────────────────────────────────────────────


class TestClientMessages:
    def test_messages(self, client):
        client._client.get.return_value = _mock_response([{"body": "hi"}])
        result = client.messages("imessage", "42")
        assert result == [{"body": "hi"}]

    def test_messages_with_thread_id(self, client):
        client._client.get.return_value = _mock_response([])
        client.messages("gmail", "msg1", thread_id="t1")
        call_args = client._client.get.call_args
        assert call_args[1]["params"]["thread_id"] == "t1"

    def test_send(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.send("42", "imessage", "hello") is True


# ── Calendar ────────────────────────────────────────────────────────────────


class TestClientCalendar:
    def test_calendar_events(self, client):
        client._client.get.return_value = _mock_response([{"summary": "Standup"}])
        result = client.calendar_events(date="2025-06-15")
        assert result[0]["summary"] == "Standup"

    def test_create_event(self, client):
        client._client.post.return_value = _mock_response({"ok": True, "event_id": "e1"})
        result = client.create_event("Meeting", "2025-06-15T14:00", "2025-06-15T15:00")
        assert result["ok"] is True

    def test_create_quick_event(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        result = client.create_quick_event("Lunch 12pm-1pm")
        assert result["ok"] is True

    def test_update_event(self, client):
        client._client.put.return_value = _mock_response({"ok": True})
        assert client.update_event("e1") is True

    def test_delete_event(self, client):
        client._client.delete.return_value = _mock_response({"ok": True})
        assert client.delete_event("e1") is True


# ── Notes ───────────────────────────────────────────────────────────────────


class TestClientNotes:
    def test_notes(self, client):
        client._client.get.return_value = _mock_response([{"title": "Note 1"}])
        result = client.notes()
        assert result[0]["title"] == "Note 1"

    def test_note(self, client):
        client._client.get.return_value = _mock_response({"body": "Full text"})
        result = client.note("42")
        assert result["body"] == "Full text"


# ── Reminders ───────────────────────────────────────────────────────────────


class TestClientReminders:
    def test_reminder_lists(self, client):
        client._client.get.return_value = _mock_response([{"name": "Shopping"}])
        assert client.reminder_lists()[0]["name"] == "Shopping"

    def test_reminders(self, client):
        client._client.get.return_value = _mock_response([{"title": "Buy milk"}])
        assert client.reminders()[0]["title"] == "Buy milk"

    def test_reminder_complete(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.reminder_complete("1") is True

    def test_reminder_create(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.reminder_create("New task") is True

    def test_reminder_edit(self, client):
        client._client.put.return_value = _mock_response({"ok": True})
        assert client.reminder_edit("1", title="Updated task") is True
        client._client.put.assert_called_once_with("/reminders/1", json={"title": "Updated task"})

    def test_reminder_edit_with_all_fields(self, client):
        client._client.put.return_value = _mock_response({"ok": True})
        assert (
            client.reminder_edit("1", title="Updated", due_date="4/15/2026", notes="details")
            is True
        )
        call_args = client._client.put.call_args
        assert call_args[0][0] == "/reminders/1"
        assert call_args[1]["json"]["title"] == "Updated"
        assert call_args[1]["json"]["due_date"] == "4/15/2026"
        assert call_args[1]["json"]["notes"] == "details"

    def test_reminder_delete(self, client):
        client._client.delete.return_value = _mock_response({"ok": True})
        assert client.reminder_delete("1") is True
        client._client.delete.assert_called_once_with("/reminders/1")


# ── GitHub ──────────────────────────────────────────────────────────────────


class TestClientGitHub:
    def test_notifications(self, client):
        client._client.get.return_value = _mock_response([{"title": "PR"}])
        assert client.github_notifications()[0]["title"] == "PR"

    def test_mark_read(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.github_mark_read("123") is True

    def test_mark_all_read(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.github_mark_all_read() is True

    def test_pulls(self, client):
        client._client.get.return_value = _mock_response([{"title": "Fix bug"}])
        assert client.github_pulls(repo="owner/repo")[0]["title"] == "Fix bug"


# ── Drive ───────────────────────────────────────────────────────────────────


class TestClientDrive:
    def test_drive_files(self, client):
        client._client.get.return_value = _mock_response([{"name": "doc.pdf"}])
        assert client.drive_files()[0]["name"] == "doc.pdf"

    def test_drive_file(self, client):
        client._client.get.return_value = _mock_response({"name": "doc.pdf"})
        assert client.drive_file("f1")["name"] == "doc.pdf"

    def test_drive_create_folder(self, client):
        client._client.post.return_value = _mock_response({"id": "folder1"})
        assert client.drive_create_folder("New Folder")["id"] == "folder1"

    def test_drive_delete(self, client):
        client._client.delete.return_value = _mock_response({"ok": True})
        assert client.drive_delete("f1") is True

    def test_drive_files_with_folder_id(self, client):
        client._client.get.return_value = _mock_response([{"name": "child.txt"}])
        result = client.drive_files(folder_id="folder-abc")
        assert result[0]["name"] == "child.txt"
        call_args = client._client.get.call_args
        assert call_args[1]["params"]["folder_id"] == "folder-abc"

    def test_drive_download(self, client):
        resp = MagicMock(spec=httpx.Response)
        resp.content = b"binary file data"
        resp.raise_for_status = MagicMock()
        client._client.get.return_value = resp
        result = client.drive_download("f1")
        assert result == b"binary file data"
        client._client.get.assert_called_once_with(
            "/drive/files/f1/download",
            params={"account": ""},
            timeout=120,
        )

    def test_drive_download_with_account(self, client):
        resp = MagicMock(spec=httpx.Response)
        resp.content = b"data"
        resp.raise_for_status = MagicMock()
        client._client.get.return_value = resp
        client.drive_download("f1", account="user@gmail.com")
        call_args = client._client.get.call_args
        assert call_args[1]["params"]["account"] == "user@gmail.com"


# ── Ambient / Dictation / LLM ──────────────────────────────────────────────


class TestClientAmbient:
    def test_ambient_start(self, client):
        client._client.post.return_value = _mock_response({"status": "started"})
        assert client.ambient_start()["status"] == "started"

    def test_ambient_stop(self, client):
        client._client.post.return_value = _mock_response({"status": "stopped"})
        assert client.ambient_stop()["status"] == "stopped"

    def test_ambient_status(self, client):
        client._client.get.return_value = _mock_response({"ambient": False})
        assert client.ambient_status()["ambient"] is False

    def test_ambient_notes(self, client):
        client._client.get.return_value = _mock_response([{"date": "2025-04-01"}])
        assert client.ambient_notes()[0]["date"] == "2025-04-01"

    def test_ambient_note(self, client):
        client._client.get.return_value = _mock_response({"content": "note text"})
        assert client.ambient_note("2025-04-01")["content"] == "note text"

    def test_dictation_start(self, client):
        client._client.post.return_value = _mock_response({"status": "started"})
        assert client.dictation_start()["status"] == "started"

    def test_dictation_stop(self, client):
        client._client.post.return_value = _mock_response({"status": "stopped"})
        assert client.dictation_stop()["status"] == "stopped"

    def test_dictation_status(self, client):
        client._client.get.return_value = _mock_response({"running": False, "available": True})
        result = client.dictation_status()
        assert result["running"] is False
        assert result["available"] is True

    def test_ambient_transcript(self, client):
        client._client.get.return_value = _mock_response({"segments": ["hello"], "count": 1})
        result = client.ambient_transcript()
        assert result["segments"] == ["hello"]
        assert result["count"] == 1

    def test_voice_config(self, client):
        cfg = {"ambient_autostart": True, "dictation_hotkey": "f5", "vault_dir": "/tmp"}
        client._client.get.return_value = _mock_response(cfg)
        result = client.voice_config()
        assert result["dictation_hotkey"] == "f5"

    def test_voice_config_update(self, client):
        updated = {"ambient_autostart": False, "dictation_hotkey": "ctrl+d", "vault_dir": "/tmp"}
        client._client.put.return_value = _mock_response(updated)
        result = client.voice_config_update(ambient_autostart=False, dictation_hotkey="ctrl+d")
        assert result["ambient_autostart"] is False

    def test_autocomplete(self, client):
        client._client.post.return_value = _mock_response({"completion": "world"})
        assert client.autocomplete("hello ") == "world"

    def test_llm_status(self, client):
        client._client.get.return_value = _mock_response({"loaded": False})
        assert client.llm_status()["loaded"] is False

    def test_llm_warmup(self, client):
        client._client.post.return_value = _mock_response({"status": "ready"})
        assert client.llm_warmup()["status"] == "ready"


# ── Close ───────────────────────────────────────────────────────────────────


class TestClientClose:
    def test_close(self, client):
        client.close()
        client._client.close.assert_called_once()


# ── GitHub ──────────────────────────────────────────────────────────────────


class TestGitHubClient:
    def test_github_notifications(self, client):
        client._client.get.return_value = _mock_response(
            [{"id": "123", "title": "Fix bug", "repo": "owner/repo", "unread": True}]
        )
        result = client.github_notifications()
        assert len(result) == 1
        assert result[0]["title"] == "Fix bug"
        assert result[0]["unread"] is True

    def test_github_notifications_all(self, client):
        client._client.get.return_value = _mock_response([])
        client.github_notifications(all_notifs=True)
        # Verify the 'all' param is passed
        call_args = client._client.get.call_args
        assert call_args[1]["params"]["all"] is True

    def test_github_mark_read(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.github_mark_read("123") is True

    def test_github_mark_all_read(self, client):
        client._client.post.return_value = _mock_response({"ok": True})
        assert client.github_mark_all_read() is True

    def test_github_pulls(self, client):
        client._client.get.return_value = _mock_response(
            [{"id": 456, "number": 42, "title": "Fix bug"}]
        )
        result = client.github_pulls()
        assert len(result) == 1
        assert result[0]["number"] == 42

    def test_github_pulls_with_repo(self, client):
        client._client.get.return_value = _mock_response([])
        client.github_pulls(repo="owner/repo")
        call_args = client._client.get.call_args
        assert call_args[1]["params"]["repo"] == "owner/repo"


# ── Search ──────────────────────────────────────────────────────────────────


class TestClientSearch:
    def test_search_basic(self, client):
        mock_result = {
            "query": "standup",
            "total": 1,
            "results": [
                {
                    "source": "calendar",
                    "id": "evt1",
                    "title": "Team standup",
                    "snippet": "standup call",
                    "timestamp": "2026-04-10T10:00:00",
                    "metadata": {},
                }
            ],
        }
        client._client.post.return_value = _mock_response(mock_result)
        result = client.search("standup")
        assert result["query"] == "standup"
        assert result["total"] == 1
        client._client.post.assert_called_once_with("/search", json={"q": "standup", "limit": 50})

    def test_search_with_sources(self, client):
        client._client.post.return_value = _mock_response({"query": "x", "total": 0, "results": []})
        client.search("x", sources=["imessage", "notes"], limit=20)
        call_args = client._client.post.call_args
        payload = call_args[1]["json"]
        assert payload["sources"] == ["imessage", "notes"]
        assert payload["limit"] == 20

    def test_search_default_no_sources_param(self, client):
        client._client.post.return_value = _mock_response({"query": "x", "total": 0, "results": []})
        client.search("x")
        call_args = client._client.post.call_args
        payload = call_args[1]["json"]
        # sources should not be in payload when None
        assert "sources" not in payload

    def test_search_empty_result(self, client):
        client._client.post.return_value = _mock_response(
            {"query": "xyz", "total": 0, "results": []}
        )
        result = client.search("xyz")
        assert result["total"] == 0
        assert result["results"] == []


# ── AI ──────────────────────────────────────────────────────────────────────


class TestAIClient:
    def test_ai_briefing(self, client):
        expected = {
            "events": [],
            "pending_reminders": [],
            "unread_counts": {
                "imessage": 0,
                "gmail": 0,
                "github_notifications": 0,
                "github_prs": 0,
            },
            "summary": None,
        }
        client._client.post.return_value = _mock_response(expected)
        result = client.ai_briefing()
        assert "events" in result
        assert "unread_counts" in result
        client._client.post.assert_called_once()
        call_args = client._client.post.call_args
        assert call_args[0][0] == "/ai/briefing"

    def test_ai_triage_empty(self, client):
        client._client.post.return_value = _mock_response({})
        result = client.ai_triage([])
        assert result == {}

    def test_ai_triage_with_conversations(self, client):
        client._client.post.return_value = _mock_response({"c1": "urgent", "c2": "normal"})
        convos = [
            {"id": "c1", "source": "gmail", "name": "Boss", "snippet": "urgent", "unread": 1},
            {"id": "c2", "source": "imessage", "name": "Alice", "snippet": "hi", "unread": 0},
        ]
        result = client.ai_triage(convos)
        assert result["c1"] == "urgent"
        client._client.post.assert_called_once()
        call_kwargs = client._client.post.call_args
        assert call_kwargs[0][0] == "/ai/triage"
        assert "conversations" in call_kwargs[1]["json"]

    def test_ai_summarize(self, client):
        expected = {
            "summary": "Thread about project updates.",
            "key_points": ["Point A"],
            "action_items": ["Review PR"],
            "decisions": [],
            "skipped": False,
        }
        client._client.post.return_value = _mock_response(expected)
        msgs = [{"sender": f"U{i}", "body": f"msg {i}"} for i in range(6)]
        result = client.ai_summarize("t1", msgs)
        assert result["summary"] == "Thread about project updates."
        assert result["skipped"] is False
        call_kwargs = client._client.post.call_args
        assert call_kwargs[0][0] == "/ai/summarize"
        assert call_kwargs[1]["json"]["thread_id"] == "t1"

    def test_ai_extract_actions(self, client):
        expected = {"actions": [{"text": "Follow up", "deadline": None, "type": "task"}]}
        client._client.post.return_value = _mock_response(expected)
        result = client.ai_extract_actions("Please follow up with the team about deployment.")
        assert len(result["actions"]) == 1
        call_kwargs = client._client.post.call_args
        assert call_kwargs[0][0] == "/ai/extract-actions"
        assert "text" in call_kwargs[1]["json"]

    def test_llm_status_extended(self, client):
        expected = {
            "loaded": True,
            "small": {"loaded": True, "model_id": "mlx-community/Qwen3.5-0.8B-MLX-4bit"},
            "large": {
                "loaded": False,
                "model_id": "mlx-community/Qwen2.5-3B-Instruct-4bit",
                "loading": False,
            },
        }
        client._client.get.return_value = _mock_response(expected)
        result = client.llm_status()
        assert "small" in result
        assert "large" in result
        assert result["large"]["loading"] is False
