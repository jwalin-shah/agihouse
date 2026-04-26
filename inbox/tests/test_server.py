"""Tests for inbox_server.py — FastAPI endpoint tests via TestClient."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services import (
    CalendarEvent,
    Contact,
    Document,
    DriveFile,
    GitHubNotification,
    GoogleTask,
    Msg,
    Note,
    Reminder,
    Spreadsheet,
    ThreadSummary,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """TestClient with mocked lifespan (no real Google auth / contacts)."""
    import inbox_server

    # Patch lifespan dependencies so real auth doesn't run
    with (
        patch.dict(os.environ, {"INBOX_SERVER_TOKEN": ""}, clear=False),
        patch("inbox_server.init_contacts", return_value=0),
        patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {}, {}, {})),
        TestClient(inbox_server.app, raise_server_exceptions=False) as c,
    ):
        # Reset state after lifespan has run (lifespan sets from mocked return)
        inbox_server.state.gmail_services = {}
        inbox_server.state.cal_services = {}
        inbox_server.state.drive_services = {}
        inbox_server.state.sheets_services = {}
        inbox_server.state.conv_cache = {}
        inbox_server.state.events_cache = []
        # Replace ambient/dictation with mocks for testing
        mock_ambient = MagicMock()
        mock_ambient.is_running = False
        mock_dictation = MagicMock()
        mock_dictation.is_running = False
        mock_dictation.available = True
        inbox_server.state.ambient = mock_ambient
        inbox_server.state.dictation = mock_dictation
        yield c


@pytest.fixture
def populated_client(client):
    """Client with some fake data in the conv_cache."""
    import inbox_server

    inbox_server.state.conv_cache = {
        "imessage:42": Contact(
            id="42", name="Alice", source="imessage", guid="iMessage;-;+1234567890"
        ),
        "gmail:msg123": Contact(
            id="msg123",
            name="Bob",
            source="gmail",
            reply_to="bob@example.com",
            thread_id="thread456",
            gmail_account="me@gmail.com",
        ),
    }
    return client


# ── Health ──────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, client):
        with patch("services._github_token", return_value=None):
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_shows_account_lists(self, client):
        import inbox_server

        inbox_server.state.gmail_services = {"a@gmail.com": MagicMock()}
        with patch("services._github_token", return_value="ghp_xxx"):
            resp = client.get("/health")
        data = resp.json()
        assert "a@gmail.com" in data["gmail_accounts"]
        assert data["github_configured"] is True


class TestIndexEndpoints:
    def test_index_threads_endpoint_returns_materialized_threads(self, client):
        import inbox_server

        inbox_server.state.index_store.list_threads = MagicMock(
            return_value=[
                {
                    "thread_id": "t1",
                    "account": "me@gmail.com",
                    "participants_json": ["Mehak Bhatia"],
                    "latest_subject": "Consulting opportunity",
                    "latest_snippet": "Consulting opportunity",
                    "latest_item_at": "2026-04-18T01:00:00+00:00",
                    "summary": "Mehak Bhatia: Consulting opportunity [opportunity/reply]",
                    "open_loop": "Reply to Mehak Bhatia",
                    "topic": "opportunity",
                    "needs_reply": 1,
                    "message_count": 2,
                    "latest_sender": "Mehak Bhatia",
                }
            ]
        )

        resp = client.get("/index/threads")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_path"].endswith(".inbox_index.sqlite3")
        assert len(data["threads"]) == 1
        assert data["threads"][0]["thread_id"] == "t1"
        assert data["threads"][0]["needs_reply"] is True
        assert data["threads"][0]["workflow"] == ""

    def test_index_status_exposes_counts_and_sync_states(self, client):
        import inbox_server

        inbox_server.state.index_store.index_counts = MagicMock(
            return_value={"items": 12, "threads": 4}
        )
        inbox_server.state.index_store.list_sync_states = MagicMock(
            return_value=[
                {
                    "source": "gmail",
                    "account": "me@gmail.com",
                    "checkpoint_type": "internalDateMs",
                    "checkpoint_value": "123",
                    "last_success_at": "2026-04-18T01:00:00+00:00",
                    "last_full_sync_at": "2026-04-18T00:00:00+00:00",
                    "status": "idle",
                    "last_run_started_at": "2026-04-18T00:55:00+00:00",
                    "last_error": "",
                    "metadata": {"messages_processed": 12},
                }
            ]
        )

        resp = client.get("/index/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"] == {"items": 12, "threads": 4}
        assert len(data["sync_states"]) == 1
        assert data["sync_states"][0]["source"] == "gmail"
        assert data["sync_states"][0]["metadata"]["messages_processed"] == 12

    def test_index_view_actionable_routes_to_index_store(self, client):
        import inbox_server

        inbox_server.state.index_store.list_threads = MagicMock(
            return_value=[
                {
                    "thread_id": "t1",
                    "account": "me@gmail.com",
                    "participants_json": ["Mehak Bhatia"],
                    "latest_subject": "Consulting opportunity",
                    "latest_snippet": "Consulting opportunity",
                    "latest_item_at": "2026-04-18T01:00:00+00:00",
                    "summary": "Mehak Bhatia: Consulting opportunity [opportunity/reply]",
                    "open_loop": "Reply to Mehak Bhatia",
                    "topic": "opportunity",
                    "needs_reply": 1,
                    "message_count": 2,
                    "latest_sender": "Mehak Bhatia",
                }
            ]
        )

        resp = client.get("/index/views/actionable", params={"limit": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["view"] == "actionable"
        assert len(data["threads"]) == 1
        inbox_server.state.index_store.list_threads.assert_called_once_with(
            limit=5,
            actions=("reply", "review", "track"),
            newest_only=True,
            sort_mode="priority",
        )

    def test_index_view_waiting_on_routes_to_index_store(self, client):
        import inbox_server

        inbox_server.state.index_store.list_threads = MagicMock(return_value=[])

        resp = client.get("/index/views/waiting-on", params={"limit": 7})
        assert resp.status_code == 200
        data = resp.json()
        assert data["view"] == "waiting-on"
        assert data["threads"] == []
        inbox_server.state.index_store.list_threads.assert_called_once_with(
            limit=7,
            actions=("track",),
            has_open_loop=True,
            newest_only=True,
            sort_mode="recent",
        )


class TestAuth:
    def test_auth_not_required_when_token_unset(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_auth_required_when_token_set(self):
        import inbox_server

        with (
            patch.dict(os.environ, {"INBOX_SERVER_TOKEN": "secret-token"}, clear=False),
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {}, {}, {})),
            TestClient(inbox_server.app, raise_server_exceptions=False) as client,
        ):
            resp = client.get("/health")
        assert resp.status_code == 401

    def test_bearer_auth_allows_request(self):
        import inbox_server

        with (
            patch.dict(os.environ, {"INBOX_SERVER_TOKEN": "secret-token"}, clear=False),
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {}, {}, {})),
            TestClient(inbox_server.app, raise_server_exceptions=False) as client,
        ):
            resp = client.get("/health", headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200

    def test_x_api_key_auth_allows_request(self):
        import inbox_server

        with (
            patch.dict(os.environ, {"INBOX_SERVER_TOKEN": "secret-token"}, clear=False),
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {}, {}, {})),
            TestClient(inbox_server.app, raise_server_exceptions=False) as client,
        ):
            resp = client.get("/health", headers={"X-API-Key": "secret-token"})
        assert resp.status_code == 200


class TestLifespanCleanup:
    def test_shutdown_closes_sqlite_connections(self):
        import inbox_server

        with (
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {}, {}, {})),
            patch("inbox_server.close_sqlite_connections") as mock_close,
            TestClient(inbox_server.app, raise_server_exceptions=False),
        ):
            pass

        mock_close.assert_called_once_with()


# ── Conversations ───────────────────────────────────────────────────────────


class TestConversations:
    @patch("inbox_server.imsg_contacts")
    def test_list_imessage(self, mock_imsg, client):
        mock_imsg.return_value = [
            Contact(
                id="1",
                name="Alice",
                source="imessage",
                snippet="hey",
                last_ts=datetime(2025, 1, 1),
            ),
        ]
        resp = client.get("/conversations", params={"source": "imessage"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice"
        assert data[0]["source"] == "imessage"

    @patch("inbox_server.gmail_contacts")
    def test_list_gmail(self, mock_gmail, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_gmail.return_value = [
            Contact(
                id="msg1",
                name="Bob",
                source="gmail",
                last_ts=datetime(2025, 1, 2),
            ),
        ]
        resp = client.get("/conversations", params={"source": "gmail"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Bob"

    @patch("inbox_server.imsg_contacts")
    def test_list_all_sorts_by_ts(self, mock_imsg, client):
        mock_imsg.return_value = [
            Contact(id="1", name="Old", source="imessage", last_ts=datetime(2024, 1, 1)),
            Contact(id="2", name="New", source="imessage", last_ts=datetime(2025, 6, 1)),
        ]
        resp = client.get("/conversations", params={"source": "imessage"})
        data = resp.json()
        assert data[0]["name"] == "New"


# ── Messages ────────────────────────────────────────────────────────────────


class TestMessages:
    @patch("inbox_server.imsg_thread")
    def test_get_imessage_thread(self, mock_thread, populated_client):
        mock_thread.return_value = [
            Msg(sender="Alice", body="hi", ts=datetime(2025, 1, 1), is_me=False, source="imessage"),
        ]
        resp = populated_client.get("/messages/imessage/42")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sender"] == "Alice"

    def test_unknown_source_400(self, client):
        resp = client.get("/messages/foobar/123")
        assert resp.status_code == 400

    @patch("inbox_server.imsg_send")
    def test_send_imessage(self, mock_send, populated_client):
        mock_send.return_value = True
        resp = populated_client.post(
            "/messages/send",
            json={"conv_id": "42", "source": "imessage", "text": "hello"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_send_unknown_conv_404(self, client):
        resp = client.post(
            "/messages/send",
            json={"conv_id": "999", "source": "imessage", "text": "hello"},
        )
        assert resp.status_code == 404


# ── Calendar ────────────────────────────────────────────────────────────────


class TestCalendar:
    @patch("inbox_server.calendar_events")
    def test_list_events(self, mock_events, client):
        mock_events.return_value = [
            CalendarEvent(
                summary="Standup",
                start=datetime(2025, 6, 15, 9, 0),
                end=datetime(2025, 6, 15, 9, 30),
            ),
        ]
        resp = client.get("/calendar/events")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["summary"] == "Standup"

    def test_create_event_no_account_404(self, client):
        resp = client.post(
            "/calendar/events",
            json={
                "summary": "Test",
                "start": "2025-06-15T14:00:00",
                "end": "2025-06-15T15:00:00",
            },
        )
        assert resp.status_code == 404

    @patch("inbox_server.parse_quick_event")
    @patch("inbox_server.calendar_create_event")
    def test_quick_event(self, mock_create, mock_parse, client):
        import inbox_server

        inbox_server.state.cal_services = {"me@gmail.com": MagicMock()}
        mock_parse.return_value = {
            "summary": "Lunch",
            "start": datetime(2025, 6, 15, 12, 0),
            "end": datetime(2025, 6, 15, 13, 0),
            "location": "Cafe",
            "all_day": False,
        }
        mock_create.return_value = "evt123"
        resp = client.post("/calendar/events/quick", json={"text": "Lunch 12pm-1pm @ Cafe"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_event_no_account_404(self, client):
        resp = client.delete("/calendar/events/evt123")
        assert resp.status_code == 404

    @patch("inbox_server.calendar_create_event")
    def test_create_event_uses_default_account(self, mock_create, client):
        import inbox_server

        inbox_server.state.cal_services = {
            "other@gmail.com": MagicMock(),
            "jshah1331@gmail.com": MagicMock(),
        }
        mock_create.return_value = "evt_default"
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.post(
                "/calendar/events",
                json={
                    "summary": "Test",
                    "start": "2025-06-15T14:00:00",
                    "end": "2025-06-15T15:00:00",
                },
            )
        assert resp.status_code == 200
        mock_create.assert_called_once()
        # service passed should be the jshah1331 mock
        call_svc = mock_create.call_args[0][0]
        assert call_svc is inbox_server.state.cal_services["jshah1331@gmail.com"]

    @patch("inbox_server.parse_quick_event")
    @patch("inbox_server.calendar_create_event")
    def test_quick_event_uses_default_account(self, mock_create, mock_parse, client):
        import inbox_server

        inbox_server.state.cal_services = {
            "other@gmail.com": MagicMock(),
            "jshah1331@gmail.com": MagicMock(),
        }
        mock_parse.return_value = {
            "summary": "Lunch",
            "start": datetime(2025, 6, 15, 12, 0),
            "end": datetime(2025, 6, 15, 13, 0),
            "location": "",
            "all_day": False,
        }
        mock_create.return_value = "evt_default"
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.post("/calendar/events/quick", json={"text": "Lunch 12pm"})
        assert resp.status_code == 200
        call_svc = mock_create.call_args[0][0]
        assert call_svc is inbox_server.state.cal_services["jshah1331@gmail.com"]


# ── Notes ───────────────────────────────────────────────────────────────────


class TestNotes:
    @patch("inbox_server.notes_list")
    def test_list_notes(self, mock_list, client):
        mock_list.return_value = [
            Note(id="1", title="My Note", snippet="Hello...", modified=datetime(2025, 1, 1)),
        ]
        resp = client.get("/notes")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["title"] == "My Note"

    @patch("inbox_server.note_body")
    @patch("inbox_server.notes_list")
    def test_get_note(self, mock_list, mock_body, client):
        mock_list.return_value = [
            Note(id="42", title="Test", snippet="snip", modified=datetime(2025, 1, 1)),
        ]
        mock_body.return_value = "Full body text"
        resp = client.get("/notes/42")
        assert resp.status_code == 200
        assert resp.json()["body"] == "Full body text"

    @patch("inbox_server.notes_list")
    def test_get_note_not_found(self, mock_list, client):
        mock_list.return_value = []
        resp = client.get("/notes/999")
        assert resp.status_code == 404


# ── Ambient ─────────────────────────────────────────────────────────────────


class TestAmbient:
    def test_start_ambient(self, client):
        import inbox_server

        inbox_server.state.ambient.is_running = False
        resp = client.post("/ambient/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_start_ambient_already_running(self, client):
        import inbox_server

        inbox_server.state.ambient.is_running = True
        resp = client.post("/ambient/start")
        assert resp.json()["status"] == "already_running"

    def test_stop_ambient(self, client):
        import inbox_server

        inbox_server.state.ambient.is_running = True
        resp = client.post("/ambient/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_ambient_status(self, client):
        import inbox_server

        inbox_server.state.ambient.is_running = False
        inbox_server.state.dictation.is_running = False
        inbox_server.state.dictation.available = True
        resp = client.get("/ambient/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambient"] is False
        assert data["dictation_available"] is True


# ── Ambient Notes ───────────────────────────────────────────────────────────


class TestAmbientNotes:
    @patch("inbox_server.ambient_notes.list_daily_notes")
    def test_list_ambient_notes(self, mock_list, client):
        mock_list.return_value = [
            {"date": "2025-04-01", "path": "/tmp/daily/2025-04-01.md", "size": 100},
        ]
        resp = client.get("/ambient/notes")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["date"] == "2025-04-01"

    @patch("inbox_server.ambient_notes.read_daily_note")
    def test_get_ambient_note(self, mock_read, client):
        mock_read.return_value = "# Today\nSome content"
        resp = client.get("/ambient/notes/2025-04-01")
        assert resp.status_code == 200
        assert resp.json()["content"] == "# Today\nSome content"

    @patch("inbox_server.ambient_notes.read_daily_note")
    def test_get_ambient_note_not_found(self, mock_read, client):
        mock_read.return_value = None
        resp = client.get("/ambient/notes/1999-01-01")
        assert resp.status_code == 404


# ── Dictation ───────────────────────────────────────────────────────────────


class TestDictation:
    def test_start_dictation_no_binary(self, client):
        import inbox_server

        inbox_server.state.dictation.is_running = False
        inbox_server.state.dictation.available = False
        resp = client.post("/dictation/start")
        assert resp.status_code == 400

    def test_stop_dictation_not_running(self, client):
        import inbox_server

        inbox_server.state.dictation.is_running = False
        resp = client.post("/dictation/stop")
        assert resp.json()["status"] == "not_running"


# ── Reminders ───────────────────────────────────────────────────────────────


class TestReminders:
    @patch("inbox_server.reminders_lists")
    def test_list_reminder_lists(self, mock_lists, client):
        mock_lists.return_value = [{"name": "Shopping", "incomplete_count": 3}]
        resp = client.get("/reminders/lists")
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "Shopping"

    @patch("inbox_server.reminders_list")
    def test_list_reminders(self, mock_list, client):
        mock_list.return_value = [
            Reminder(id="1", title="Buy milk", completed=False, list_name="Shopping"),
        ]
        resp = client.get("/reminders")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["title"] == "Buy milk"

    @patch("inbox_server.reminder_complete")
    @patch("inbox_server.reminder_by_id")
    @patch("inbox_server.reminders_list")
    def test_complete_reminder(self, mock_list, mock_by_id, mock_complete, client):
        reminder = Reminder(id="99", title="Buy milk", completed=False)
        mock_list.return_value = [reminder]
        mock_by_id.return_value = reminder
        mock_complete.return_value = True
        resp = client.post("/reminders/99/complete")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("inbox_server.reminder_by_id")
    @patch("inbox_server.reminders_list")
    def test_complete_reminder_not_found(self, mock_list, mock_by_id, client):
        mock_list.return_value = []
        mock_by_id.return_value = None
        resp = client.post("/reminders/999/complete")
        assert resp.status_code == 404


# ── GitHub ──────────────────────────────────────────────────────────────────


class TestGitHub:
    @patch("inbox_server.github_notifications")
    def test_list_notifications(self, mock_notifs, client):
        mock_notifs.return_value = [
            GitHubNotification(
                id="1",
                title="PR Review",
                repo="owner/repo",
                type="PullRequest",
                reason="review_requested",
                unread=True,
                updated_at=datetime(2025, 1, 1),
                url="https://github.com/owner/repo/pull/1",
            ),
        ]
        resp = client.get("/github/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["title"] == "PR Review"
        assert data[0]["unread"] is True


# ── Accounts ────────────────────────────────────────────────────────────────


class TestAccounts:
    def test_list_accounts(self, client):
        import inbox_server

        inbox_server.state.gmail_services = {"a@g.com": MagicMock()}
        with patch("services._github_token", return_value=None):
            resp = client.get("/accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert "a@g.com" in data["gmail"]
        assert data["github"] is False


# ── Drive ───────────────────────────────────────────────────────────────────


class TestDrive:
    @patch("inbox_server.drive_files")
    def test_list_drive_files(self, mock_files, client):
        import inbox_server

        inbox_server.state.drive_services = {"me@gmail.com": MagicMock()}
        mock_files.return_value = [
            DriveFile(
                id="f1",
                name="doc.pdf",
                mime_type="application/pdf",
                modified=datetime(2025, 1, 1),
                size=1024,
            ),
        ]
        resp = client.get("/drive/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["name"] == "doc.pdf"
        assert data[0]["account"] == "me@gmail.com"

    def test_get_drive_file_no_account_404(self, client):
        resp = client.get("/drive/files/abc123")
        assert resp.status_code == 404

    def test_delete_drive_file_no_account_404(self, client):
        resp = client.delete("/drive/files/abc123")
        assert resp.status_code == 404

    @patch("inbox_server.drive_create_folder")
    def test_create_folder_uses_default_account(self, mock_create, client):
        import inbox_server

        default_svc = MagicMock()
        inbox_server.state.drive_services = {
            "other@gmail.com": MagicMock(),
            "jshah1331@gmail.com": default_svc,
        }
        mock_create.return_value = DriveFile(
            id="fold1",
            name="MyFolder",
            mime_type="application/vnd.google-apps.folder",
            modified=datetime(2025, 1, 1),
        )
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.post("/drive/folder", json={"name": "MyFolder"})
        assert resp.status_code == 200
        assert resp.json()["account"] == "jshah1331@gmail.com"
        mock_create.assert_called_once_with(default_svc, "MyFolder", parent_id="")


class TestGmailExtensions:
    @patch("inbox_server.gmail_search")
    def test_gmail_search(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="msg1",
                name="Alice",
                source="gmail",
                reply_to="alice@example.com",
                thread_id="thread1",
                gmail_account="me@gmail.com",
                last_ts=datetime(2025, 1, 1),
            )
        ]
        resp = client.get("/gmail/search", params={"q": "invoice", "account": "me@gmail.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    @patch("inbox_server.gmail_reply")
    def test_gmail_reply(self, mock_reply, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_reply.return_value = True
        resp = client.post(
            "/messages/gmail/reply",
            json={"msg_id": "msg1", "body": "Thanks", "account": "me@gmail.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("inbox_server.gmail_batch_modify")
    def test_gmail_batch_modify(self, mock_batch_modify, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_batch_modify.return_value = True
        resp = client.post(
            "/gmail/batch-modify",
            json={
                "msg_ids": ["a", "b"],
                "add_label_ids": ["STARRED"],
                "account": "me@gmail.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] == 2


class TestCalendarExtensions:
    @patch("inbox_server.calendar_events")
    def test_calendar_upcoming(self, mock_events, client):
        mock_events.return_value = [
            CalendarEvent(
                summary="Standup",
                start=datetime(2025, 6, 15, 9, 0),
                end=datetime(2025, 6, 15, 9, 30),
            ),
        ]
        resp = client.get("/calendar/upcoming", params={"days": 7})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["summary"] == "Standup"

    @patch("inbox_server.calendar_create_event")
    def test_create_event_with_attendees(self, mock_create, client):
        import inbox_server

        inbox_server.state.cal_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = "evt123"
        resp = client.post(
            "/calendar/events",
            json={
                "summary": "Interview",
                "start": "2025-06-15T14:00:00",
                "end": "2025-06-15T15:00:00",
                "account": "me@gmail.com",
                "attendees": [{"email": "alice@example.com", "name": "Alice"}],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["event_id"] == "evt123"


# ── Autocomplete ────────────────────────────────────────────────────────────


class TestTasks:
    @patch("inbox_server.tasks_list")
    def test_list_tasks_includes_account(self, mock_list, client):
        import inbox_server

        inbox_server.state.tasks_services = {"jshah1331@gmail.com": MagicMock()}
        mock_list.return_value = [
            GoogleTask(
                id="t1",
                title="Apply to Acme",
                status="needsAction",
                list_id="@default",
                list_title="My Tasks",
            )
        ]
        resp = client.get("/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["id"] == "t1"
        assert data[0]["account"] == "jshah1331@gmail.com"

    @patch("inbox_server.tasks_list")
    def test_list_tasks_uses_default_account(self, mock_list, client):
        import inbox_server

        other_svc = MagicMock()
        default_svc = MagicMock()
        inbox_server.state.tasks_services = {
            "other@gmail.com": other_svc,
            "jshah1331@gmail.com": default_svc,
        }
        mock_list.return_value = []
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get("/tasks")
        assert resp.status_code == 200
        mock_list.assert_called_once()
        assert mock_list.call_args[0][0] is default_svc


class TestAutocomplete:
    @patch("inbox_server.services_autocomplete")
    def test_complete_mode(self, mock_autocomplete, client):
        """Test autocomplete endpoint in complete mode."""
        mock_autocomplete.return_value = "sounds good"
        resp = client.post(
            "/autocomplete",
            json={
                "draft": "That ",
                "messages": [{"sender": "Alice", "body": "Wanna meet?"}],
                "mode": "complete",
                "max_tokens": 32,
                "temperature": 0.5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completion"] == "sounds good"
        # Verify params passed correctly
        call_args = mock_autocomplete.call_args
        assert call_args[0][0] == "That "
        assert call_args[0][1] == [{"sender": "Alice", "body": "Wanna meet?"}]
        assert call_args[0][2] == 32
        assert call_args[0][3] == 0.5
        assert call_args[0][4] == "complete"

    @patch("inbox_server.services_autocomplete")
    def test_reply_mode(self, mock_autocomplete, client):
        """Test autocomplete endpoint in reply mode."""
        mock_autocomplete.return_value = "Absolutely!"
        resp = client.post(
            "/autocomplete",
            json={
                "messages": [
                    {"sender": "Alice", "body": "Want to meet up?"},
                ],
                "mode": "reply",
                "max_tokens": 32,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completion"] == "Absolutely!"

    @patch("inbox_server.services_autocomplete")
    def test_autocomplete_with_temperature(self, mock_autocomplete, client):
        """Test that temperature parameter is passed."""
        mock_autocomplete.return_value = "result"
        client.post(
            "/autocomplete",
            json={
                "draft": "Hello",
                "temperature": 0.8,
            },
        )
        call_args = mock_autocomplete.call_args
        assert call_args[0][3] == 0.8

    @patch("inbox_server.services_autocomplete")
    def test_autocomplete_returns_none(self, mock_autocomplete, client):
        """Test that None completions are returned."""
        mock_autocomplete.return_value = None
        resp = client.post(
            "/autocomplete",
            json={"draft": "Hi"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completion"] is None

    @patch("inbox_server.services_autocomplete")
    def test_autocomplete_error_handling(self, mock_autocomplete, client):
        """Test that errors are caught and returned gracefully."""
        mock_autocomplete.side_effect = RuntimeError("LLM failed")
        resp = client.post(
            "/autocomplete",
            json={"draft": "Hello"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completion"] is None
        assert "error" in data

    @patch("inbox_server.services_autocomplete")
    def test_autocomplete_defaults(self, mock_ac, client):
        """Test that defaults are applied to request."""
        mock_ac.return_value = None
        client.post(
            "/autocomplete",
            json={"draft": "Hi there"},
        )
        call_args = mock_ac.call_args
        # Should use defaults: max_tokens=32, temp=0.5, mode=complete
        assert call_args[0][2] == 32
        assert call_args[0][3] == 0.5
        assert call_args[0][4] == "complete"


# ── Ambient Note Callback Wiring ────────────────────────────────────────────


class TestGmailThreadSummary:
    def test_classify_workflow_job_hunt(self):
        import inbox_server

        assert inbox_server._classify_workflow("interview with recruiter at Acme") == "job_hunt"

    def test_classify_workflow_finance(self):
        import inbox_server

        assert inbox_server._classify_workflow("invoice payment due this week") == "finance"

    def test_classify_workflow_unknown(self):
        import inbox_server

        assert inbox_server._classify_workflow("happy birthday to you") == ""

    def test_extract_action_items_please(self):
        import inbox_server

        items = inbox_server._extract_action_items("Hi, please send the report by Friday.")
        assert len(items) >= 1
        assert any("please" in item.lower() for item in items)

    def test_extract_action_items_empty(self):
        import inbox_server

        assert inbox_server._extract_action_items("Thanks for reaching out.") == []

    def test_needs_reply_true_when_last_sender_not_me(self):
        import inbox_server

        ts = ThreadSummary(
            thread_id="t1",
            owning_account="me@gmail.com",
            participants=["Alice"],
            subject="Job offer",
            last_message_at=datetime(2025, 6, 1),
            label_ids=["INBOX"],
            body_text="Please send your resume.",
            last_message_body="Please send your resume.",
            last_sender_is_me=False,
            message_count=1,
        )
        out = inbox_server._thread_summary_to_out(ts, {"INBOX": "INBOX"})
        assert out.needs_reply is True
        assert out.workflow == "job_hunt"

    def test_needs_reply_false_when_last_sender_is_me(self):
        import inbox_server

        ts = ThreadSummary(
            thread_id="t2",
            owning_account="me@gmail.com",
            participants=["Me", "Bob"],
            subject="Invoice payment due tomorrow",
            last_message_at=datetime(2025, 6, 1),
            label_ids=[],
            body_text="I sent the payment.",
            last_message_body="I sent the payment.",
            last_sender_is_me=True,
            message_count=2,
        )
        out = inbox_server._thread_summary_to_out(ts, {})
        assert out.needs_reply is False
        assert out.workflow == "finance"

    def test_category_labels_filtered(self):
        import inbox_server

        ts = ThreadSummary(
            thread_id="t3",
            owning_account="me@gmail.com",
            participants=["Bob"],
            subject="Hello",
            last_message_at=datetime(2025, 6, 1),
            label_ids=["INBOX", "CATEGORY_PROMOTIONS", "Label_123"],
            body_text="",
            last_message_body="",
            last_sender_is_me=False,
            message_count=1,
        )
        out = inbox_server._thread_summary_to_out(ts, {"INBOX": "INBOX", "Label_123": "Jobs"})
        assert "CATEGORY_PROMOTIONS" not in out.labels
        assert "INBOX" in out.labels
        assert "Jobs" in out.labels

    @patch("inbox_server.gmail_thread_summary")
    @patch("inbox_server.gmail_labels")
    def test_single_thread_summary_endpoint(self, mock_labels, mock_summary, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_summary.return_value = ThreadSummary(
            thread_id="thread123",
            owning_account="me@gmail.com",
            participants=["Alice"],
            subject="Job opportunity at Acme",
            last_message_at=datetime(2025, 6, 1, 12, 0),
            label_ids=["INBOX"],
            body_text="Please send your resume by Friday.",
            last_message_body="Please send your resume by Friday.",
            last_sender_is_me=False,
            message_count=2,
        )
        mock_labels.return_value = [{"id": "INBOX", "name": "INBOX", "type": "system"}]
        resp = client.get("/gmail/threads/thread123/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == "thread123"
        assert data["needs_reply"] is True
        assert data["workflow"] == "job_hunt"
        assert any("resume" in item.lower() for item in data["action_items"])
        assert data["owning_account"] == "me@gmail.com"

    @patch("inbox_server.gmail_thread_summary")
    @patch("inbox_server.gmail_labels")
    def test_single_thread_summary_404_on_none(self, mock_labels, mock_summary, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_summary.return_value = None
        mock_labels.return_value = []
        resp = client.get("/gmail/threads/missing/summary")
        assert resp.status_code == 404

    @patch("inbox_server.gmail_search")
    def test_search_summaries_endpoint(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="msg1",
                name="Bob from Acme",
                source="gmail",
                snippet="Interview at Acme Corp",
                unread=1,
                last_ts=datetime(2025, 6, 1),
                thread_id="thread123",
                gmail_account="me@gmail.com",
            )
        ]
        resp = client.get("/gmail/thread-summaries", params={"q": "interview"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["workflow"] == "job_hunt"
        assert data[0]["thread_id"] == "thread123"
        assert data[0]["needs_reply"] is True

    @patch("inbox_server.gmail_search")
    def test_search_summaries_workflow_filter(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="msg1",
                name="Recruiter",
                source="gmail",
                snippet="Interview at Acme",
                unread=1,
                last_ts=datetime(2025, 6, 1),
                thread_id="t1",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="msg2",
                name="Bank",
                source="gmail",
                snippet="Invoice payment due",
                unread=0,
                last_ts=datetime(2025, 6, 2),
                thread_id="t2",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/thread-summaries", params={"workflow": "job_hunt"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["thread_id"] == "t1"


class TestPreflight:
    def test_no_service_returns_invalid(self, client):
        import inbox_server

        inbox_server.state.drive_services = {}
        resp = client.get("/preflight/google-write", params={"kind": "doc"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert "No Drive account available" in data["warnings"]

    def test_doc_resolves_default_account(self, client):
        import inbox_server

        inbox_server.state.drive_services = {
            "other@gmail.com": MagicMock(),
            "jshah1331@gmail.com": MagicMock(),
        }
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get("/preflight/google-write", params={"kind": "doc", "title": "My Doc"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["resolved_account"] == "jshah1331@gmail.com"
        assert "jshah1331@gmail.com" in data["explanation"]
        assert "My Doc" in data["explanation"]

    def test_task_resolves_default_account(self, client):
        import inbox_server

        inbox_server.state.tasks_services = {
            "other@gmail.com": MagicMock(),
            "jshah1331@gmail.com": MagicMock(),
        }
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get("/preflight/google-write", params={"kind": "task"})
        data = resp.json()
        assert data["valid"] is True
        assert data["resolved_account"] == "jshah1331@gmail.com"
        assert data["destination"] == "My Tasks"
        assert data["destination_id"] == "@default"

    def test_calendar_event_resolves_default_account(self, client):
        import inbox_server

        inbox_server.state.cal_services = {
            "other@gmail.com": MagicMock(),
            "jshah1331@gmail.com": MagicMock(),
        }
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get(
                "/preflight/google-write", params={"kind": "calendar_event", "title": "Interview"}
            )
        data = resp.json()
        assert data["valid"] is True
        assert data["resolved_account"] == "jshah1331@gmail.com"
        assert data["destination"] == "primary calendar"
        assert "Interview" in data["explanation"]

    @patch("inbox_server.drive_get")
    def test_valid_folder_id(self, mock_get, client):
        import inbox_server

        svc = MagicMock()
        inbox_server.state.drive_services = {"jshah1331@gmail.com": svc}
        mock_get.return_value = DriveFile(
            id="fold1",
            name="Job Hunt",
            mime_type="application/vnd.google-apps.folder",
            modified=datetime(2025, 1, 1),
        )
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get(
                "/preflight/google-write", params={"kind": "sheet", "folder_id": "fold1"}
            )
        data = resp.json()
        assert data["valid"] is True
        assert data["destination"] == "Folder 'Job Hunt'"
        assert data["destination_id"] == "fold1"

    @patch("inbox_server.drive_get")
    def test_invalid_folder_id(self, mock_get, client):
        import inbox_server

        inbox_server.state.drive_services = {"jshah1331@gmail.com": MagicMock()}
        mock_get.return_value = None
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get(
                "/preflight/google-write", params={"kind": "drive_folder", "folder_id": "bad_id"}
            )
        data = resp.json()
        assert data["valid"] is False
        assert "bad_id" in data["warnings"][0]

    @patch("inbox_server.tasks_lists")
    def test_valid_task_list_id(self, mock_lists, client):
        import inbox_server

        inbox_server.state.tasks_services = {"jshah1331@gmail.com": MagicMock()}
        mock_lists.return_value = [{"id": "list123", "title": "Job Hunt"}]
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get(
                "/preflight/google-write", params={"kind": "task", "list_id": "list123"}
            )
        data = resp.json()
        assert data["valid"] is True
        assert data["destination"] == "Task list 'Job Hunt'"

    @patch("inbox_server.tasks_lists")
    def test_invalid_task_list_id(self, mock_lists, client):
        import inbox_server

        inbox_server.state.tasks_services = {"jshah1331@gmail.com": MagicMock()}
        mock_lists.return_value = [{"id": "other", "title": "Personal"}]
        with patch.dict(os.environ, {"INBOX_DEFAULT_GOOGLE_ACCOUNT": "jshah1331@gmail.com"}):
            resp = client.get(
                "/preflight/google-write", params={"kind": "task", "list_id": "no_such_list"}
            )
        data = resp.json()
        assert data["valid"] is False
        assert "no_such_list" in data["warnings"][0]

    def test_unknown_kind(self, client):
        resp = client.get("/preflight/google-write", params={"kind": "banana"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert "banana" in data["warnings"][0]


class TestPhase4:
    def test_task_out_has_workflow_field(self, client):
        import inbox_server
        from services import GoogleTask

        t = GoogleTask(
            id="t1",
            title="Interview with Acme Corp",
            status="needs_action",
            list_id="@default",
            list_title="My Tasks",
        )
        out = inbox_server._task_to_out(t, "me@gmail.com")
        assert out.workflow == "job_hunt"

    def test_task_workflow_empty_for_unrelated(self, client):
        import inbox_server
        from services import GoogleTask

        t = GoogleTask(
            id="t2",
            title="Buy groceries",
            status="needs_action",
            list_id="@default",
            list_title="My Tasks",
        )
        out = inbox_server._task_to_out(t, "me@gmail.com")
        assert out.workflow == ""

    @patch("inbox_server.tasks_list")
    def test_task_workflow_filter(self, mock_list, client):
        import inbox_server
        from services import GoogleTask

        inbox_server.state.tasks_services = {"me@gmail.com": MagicMock()}
        mock_list.return_value = [
            GoogleTask(
                id="t1",
                title="Interview prep",
                status="needs_action",
                list_id="@default",
                list_title="My Tasks",
            ),
            GoogleTask(
                id="t2",
                title="Pay invoice",
                status="needs_action",
                list_id="@default",
                list_title="My Tasks",
            ),
        ]
        resp = client.get("/tasks", params={"workflow": "job_hunt"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"
        assert data[0]["workflow"] == "job_hunt"

    def test_calendar_event_out_has_workflow(self, client):
        import inbox_server

        e = CalendarEvent(
            summary="Interview at Acme",
            start=datetime(2025, 6, 1, 10),
            end=datetime(2025, 6, 1, 11),
        )
        out = inbox_server._event_to_out(e)
        assert out.workflow == "job_hunt"

    @patch("inbox_server.gmail_search")
    def test_needing_reply_returns_unread_stale(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="msg1",
                name="Recruiter",
                source="gmail",
                snippet="Interview at Acme",
                unread=1,
                last_ts=datetime(2020, 1, 1),  # very old
                thread_id="t1",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/threads/needing-reply")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["needs_reply"] is True
        assert data[0]["workflow"] == "job_hunt"

    @patch("inbox_server.gmail_search")
    def test_needing_reply_excludes_read(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="msg2",
                name="Bank",
                source="gmail",
                snippet="Invoice",
                unread=0,
                last_ts=datetime(2020, 1, 1),
                thread_id="t2",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/threads/needing-reply")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("inbox_server.gmail_search")
    def test_needing_reply_excludes_recent(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="msg3",
                name="Boss",
                source="gmail",
                snippet="Meeting tomorrow",
                unread=1,
                last_ts=datetime(2099, 1, 1),  # future = recent
                thread_id="t3",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/threads/needing-reply", params={"days_stale": 3})
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("inbox_server.calendar_create_event")
    def test_workflow_event_kind_prefix(self, mock_create, client):
        import inbox_server

        inbox_server.state.cal_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = "ev123"
        resp = client.post(
            "/calendar/workflow-event",
            json={
                "kind": "interview",
                "title": "Acme Corp",
                "start": "2025-06-01T10:00:00",
                "end": "2025-06-01T11:00:00",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "[Interview] Acme Corp"
        assert data["event_id"] == "ev123"

    @patch("inbox_server.calendar_create_event")
    def test_workflow_event_auto_detects_workflow(self, mock_create, client):
        import inbox_server

        inbox_server.state.cal_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = "ev456"
        resp = client.post(
            "/calendar/workflow-event",
            json={
                "title": "Review contract with attorney",
                "start": "2025-06-02T09:00:00",
                "end": "2025-06-02T10:00:00",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow"] == "legal"
        assert "#legal" in data["description"]

    @patch("inbox_server.calendar_create_event")
    def test_workflow_event_no_duplicate_prefix(self, mock_create, client):
        import inbox_server

        inbox_server.state.cal_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = "ev789"
        resp = client.post(
            "/calendar/workflow-event",
            json={
                "kind": "deadline",
                "title": "[Deadline] Submit tax return",
                "start": "2025-04-15T00:00:00",
                "end": "2025-04-15T01:00:00",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["summary"] == "[Deadline] Submit tax return"

    @patch("inbox_server.gmail_search")
    @patch("inbox_server.tasks_list")
    @patch("inbox_server.calendar_events")
    def test_needs_action_structure(self, mock_events, mock_tasks, mock_gmail, client):
        import inbox_server

        inbox_server.state.index_store.list_threads = MagicMock(return_value=[])
        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        inbox_server.state.tasks_services = {"me@gmail.com": MagicMock()}
        inbox_server.state.cal_services = {"me@gmail.com": MagicMock()}
        mock_gmail.return_value = []
        mock_tasks.return_value = []
        mock_events.return_value = []
        resp = client.get("/inbox/needs-action")
        assert resp.status_code == 200
        data = resp.json()
        assert "threads" in data
        assert "tasks" in data
        assert "events" in data
        assert "workflow_counts" in data

    @patch("inbox_server.gmail_search")
    @patch("inbox_server.tasks_list")
    @patch("inbox_server.calendar_events")
    def test_needs_action_workflow_counts(self, mock_events, mock_tasks, mock_gmail, client):
        import inbox_server

        inbox_server.state.index_store.list_threads = MagicMock(return_value=[])
        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        inbox_server.state.tasks_services = {"me@gmail.com": MagicMock()}
        inbox_server.state.cal_services = {}
        mock_gmail.return_value = [
            Contact(
                id="m1",
                name="HR",
                source="gmail",
                snippet="Interview at Acme",
                unread=1,
                last_ts=datetime(2020, 1, 1),
                thread_id="t1",
                gmail_account="me@gmail.com",
            ),
        ]
        mock_tasks.return_value = []
        mock_events.return_value = []
        resp = client.get("/inbox/needs-action")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow_counts"].get("job_hunt", 0) >= 1

    @patch("inbox_server.gmail_search")
    @patch("inbox_server.tasks_list")
    @patch("inbox_server.calendar_events")
    def test_needs_action_prefers_index_threads(self, mock_events, mock_tasks, mock_gmail, client):
        import inbox_server

        inbox_server.state.index_store.list_threads = MagicMock(
            return_value=[
                {
                    "thread_id": "t1",
                    "account": "me@gmail.com",
                    "participants_json": ["Mehak Bhatia"],
                    "latest_subject": "Consulting opportunity",
                    "latest_snippet": "Consulting opportunity",
                    "latest_item_at": "2026-04-18T01:00:00+00:00",
                    "summary": "Mehak Bhatia: Consulting opportunity [opportunity/reply]",
                    "open_loop": "Reply to Mehak Bhatia",
                    "topic": "opportunity",
                    "needs_reply": 1,
                    "message_count": 2,
                    "latest_sender": "Mehak Bhatia",
                }
            ]
        )
        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        inbox_server.state.tasks_services = {"me@gmail.com": MagicMock()}
        inbox_server.state.cal_services = {}
        mock_tasks.return_value = []
        mock_events.return_value = []

        resp = client.get("/inbox/needs-action")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["threads"]) == 1
        assert data["threads"][0]["thread_id"] == "t1"
        mock_gmail.assert_not_called()

    @patch("inbox_server.drive_create_folder")
    def test_workflow_folder_display_name(self, mock_create, client):
        import inbox_server

        inbox_server.state.drive_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = DriveFile(
            id="fold1",
            name="Job Hunt",
            mime_type="application/vnd.google-apps.folder",
            modified=datetime(2025, 1, 1),
        )
        resp = client.post("/drive/workflow-folder", json={"workflow": "job_hunt"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Job Hunt"
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs.get("parent_id", "") == ""

    @patch("inbox_server.docs_create")
    def test_workflow_doc_endpoint(self, mock_create, client):
        import inbox_server

        inbox_server.state.docs_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = Document(
            id="doc1",
            title="Job Applications",
            url="https://docs.google.com/document/d/doc1/edit",
            mime_type="application/vnd.google-apps.document",
        )
        resp = client.post(
            "/docs/workflow-doc", json={"title": "Job Applications", "workflow": "job_hunt"}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "doc1"

    @patch("inbox_server.sheets_create")
    def test_workflow_sheet_endpoint(self, mock_create, client):
        import inbox_server

        inbox_server.state.sheets_services = {"me@gmail.com": MagicMock()}
        mock_create.return_value = Spreadsheet(
            id="sh1",
            title="Finance Tracker",
            url="https://docs.google.com/spreadsheets/d/sh1/edit",
            sheets=[],
            account="me@gmail.com",
        )
        resp = client.post(
            "/sheets/workflow-sheet", json={"title": "Finance Tracker", "workflow": "finance"}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "sh1"


class TestPhase5:
    def test_thread_summary_out_has_rank_brief_rich(self, client):
        import inbox_server
        from services import ThreadSummary

        ts = ThreadSummary(
            thread_id="t1",
            owning_account="me@gmail.com",
            participants=["Recruiter"],
            subject="Interview at Acme Corp",
            last_message_at=datetime(2020, 1, 1),
            label_ids=["INBOX"],
            body_text="Please review the job offer at Acme Corp",
            last_message_body="Please review the offer",
            last_sender_is_me=False,
            message_count=3,
        )
        out = inbox_server._thread_summary_to_out(ts, {})
        assert out.rank > 0
        assert "Recruiter" in out.brief
        assert "[needs reply]" in out.brief
        assert "[job_hunt]" in out.brief
        assert isinstance(out.rich_data, dict)

    def test_rank_thread_needs_reply_boost(self, client):
        import inbox_server

        iso = datetime(2020, 1, 1).isoformat()
        r_with = inbox_server._rank_thread(iso, True, False, "", 1)
        r_without = inbox_server._rank_thread(iso, False, False, "", 1)
        assert r_with > r_without

    def test_rank_thread_fresh_higher(self, client):
        import inbox_server

        old_iso = datetime(2000, 1, 1).isoformat()
        new_iso = datetime(2099, 1, 1).isoformat()  # future = very fresh
        r_old = inbox_server._rank_thread(old_iso, False, False, "", 1)
        r_new = inbox_server._rank_thread(new_iso, False, False, "", 1)
        assert r_new > r_old

    def test_extract_rich_data_finance_amount(self, client):
        import inbox_server

        data = inbox_server._extract_rich_data("finance", "Invoice total: $1,250.00 due April 30")
        assert "amount" in data
        assert "1,250" in data["amount"]

    def test_extract_rich_data_finance_due_date(self, client):
        import inbox_server

        data = inbox_server._extract_rich_data("finance", "Payment due by April 15, 2025")
        assert "due_date" in data

    def test_extract_rich_data_empty_for_unknown_workflow(self, client):
        import inbox_server

        data = inbox_server._extract_rich_data("", "some random text")
        assert data == {}

    def test_contact_to_thread_summary_brief_format(self, client):
        import inbox_server

        c = Contact(
            id="msg1",
            name="Alice",
            source="gmail",
            snippet="Invoice payment due",
            unread=1,
            last_ts=datetime(2025, 1, 1),
            thread_id="t1",
            gmail_account="me@gmail.com",
        )
        ts = inbox_server._contact_to_thread_summary(c)
        assert ts.brief.startswith("Alice ·")
        assert "[needs reply]" in ts.brief
        assert "[finance]" in ts.brief
        assert ts.rank > 0
        assert "amount" in ts.rich_data or ts.rich_data == {}

    @patch("inbox_server.gmail_search")
    def test_search_summaries_deduped_and_sorted(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="m1",
                name="A",
                source="gmail",
                snippet="Interview",
                unread=1,
                last_ts=datetime(2020, 6, 1),
                thread_id="same_thread",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="m2",
                name="A",
                source="gmail",
                snippet="Interview",
                unread=1,
                last_ts=datetime(2020, 6, 2),
                thread_id="same_thread",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="m3",
                name="B",
                source="gmail",
                snippet="Other topic",
                unread=0,
                last_ts=datetime(2025, 6, 1),
                thread_id="other_thread",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/thread-summaries")
        assert resp.status_code == 200
        data = resp.json()
        thread_ids = [d["thread_id"] for d in data]
        assert thread_ids.count("same_thread") == 1

    @patch("inbox_server.gmail_search")
    def test_search_summaries_sorted_by_rank(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="m1",
                name="A",
                source="gmail",
                snippet="Hello",
                unread=0,
                last_ts=datetime(2000, 1, 1),
                thread_id="t1",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="m2",
                name="B",
                source="gmail",
                snippet="Interview at Acme",
                unread=1,
                last_ts=datetime(2025, 6, 1),
                thread_id="t2",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/thread-summaries")
        assert resp.status_code == 200
        data = resp.json()
        ranks = [d["rank"] for d in data]
        assert ranks == sorted(ranks, reverse=True)

    @patch("inbox_server.gmail_search")
    def test_thread_briefs_endpoint(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="m1",
                name="Recruiter",
                source="gmail",
                snippet="Interview at Acme",
                unread=1,
                last_ts=datetime(2025, 6, 1),
                thread_id="t1",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/thread-briefs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert "thread_id" in item
        assert "brief" in item
        assert "rank" in item
        assert "workflow" in item
        assert "needs_reply" in item
        assert "summary" not in item  # compact shape
        assert "participants" not in item

    @patch("inbox_server.gmail_search")
    def test_thread_briefs_deduped(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="m1",
                name="A",
                source="gmail",
                snippet="Test",
                unread=1,
                last_ts=datetime(2025, 1, 1),
                thread_id="dup",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="m2",
                name="A",
                source="gmail",
                snippet="Test",
                unread=1,
                last_ts=datetime(2025, 1, 2),
                thread_id="dup",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/thread-briefs")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    @patch("inbox_server.gmail_search")
    def test_needing_reply_deduped_and_sorted(self, mock_search, client):
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_search.return_value = [
            Contact(
                id="m1",
                name="HR",
                source="gmail",
                snippet="Interview offer",
                unread=1,
                last_ts=datetime(2020, 1, 1),
                thread_id="same",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="m2",
                name="HR",
                source="gmail",
                snippet="Interview offer",
                unread=1,
                last_ts=datetime(2020, 1, 2),
                thread_id="same",
                gmail_account="me@gmail.com",
            ),
            Contact(
                id="m3",
                name="Doctor",
                source="gmail",
                snippet="Medical appointment",
                unread=1,
                last_ts=datetime(2019, 1, 1),
                thread_id="other",
                gmail_account="me@gmail.com",
            ),
        ]
        resp = client.get("/gmail/threads/needing-reply")  # default days_stale=3, all dates are old
        assert resp.status_code == 200
        data = resp.json()
        thread_ids = [d["thread_id"] for d in data]
        assert thread_ids.count("same") == 1
        ranks = [d["rank"] for d in data]
        assert ranks == sorted(ranks, reverse=True)


class TestAmbientNoteCallback:
    def test_server_state_ambient_callback_calls_save_note(self):
        """Verify that ServerState wires AmbientService on_note to ambient_notes.save_note."""
        with patch("inbox_server.ambient_notes.save_note") as mock_save:
            import inbox_server

            fresh_state = inbox_server.ServerState()
            # Invoke the callback that AmbientService was initialized with
            fresh_state.ambient._on_note("raw transcript text", "summary of note")
            mock_save.assert_called_once_with("raw transcript text", "summary of note")

    def test_server_state_ambient_callback_handles_none_summary(self):
        """Verify callback works when summary is None."""
        with patch("inbox_server.ambient_notes.save_note") as mock_save:
            import inbox_server

            fresh_state = inbox_server.ServerState()
            fresh_state.ambient._on_note("raw transcript only", None)
            mock_save.assert_called_once_with("raw transcript only", None)

    def test_ambient_callback_is_not_noop(self):
        """Verify the callback is not a no-op lambda."""
        import inbox_server

        fresh_state = inbox_server.ServerState()
        # The old code had: on_note=lambda raw, summary: None
        # A real callback should not just return None silently
        callback = fresh_state.ambient._on_note
        # Verify it's not a trivial lambda by checking it references save_note
        assert callback is not None
        # Call it with mock to ensure it actually does something
        with patch("inbox_server.ambient_notes.save_note") as mock_save:
            callback("test", "test summary")
            assert mock_save.called
