"""Tests for Gmail action endpoints, compose, labels, attachments."""

from __future__ import annotations

import base64
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Create a test client with mocked startup."""
    import os

    with (
        patch.dict(os.environ, {"INBOX_SERVER_TOKEN": ""}, clear=False),
        patch("inbox_server.init_contacts", return_value=0),
        patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
    ):
        from inbox_server import app, state

        state.gmail_services = {}
        state.cal_services = {}
        state.drive_services = {}
        with TestClient(app) as c:
            yield c, state


@pytest.fixture()
def client_with_gmail():
    """Create a test client with a mock Gmail service."""
    import os

    with (
        patch.dict(os.environ, {"INBOX_SERVER_TOKEN": ""}, clear=False),
        patch("inbox_server.init_contacts", return_value=0),
        patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
    ):
        from inbox_server import app, state

        mock_svc = MagicMock()
        with TestClient(app) as c:
            # Set state AFTER lifespan runs (TestClient context triggers lifespan)
            state.gmail_services = {"test@gmail.com": mock_svc}
            state.cal_services = {}
            state.drive_services = {}
            state.conv_cache = {}
            yield c, state, mock_svc


def _mock_gmail_service_for_ids(
    *, message_ids: set[str] | None = None, thread_ids: set[str] | None = None
):
    """Create a mock Gmail service that only recognizes selected messages/threads."""
    message_ids = message_ids or set()
    thread_ids = thread_ids or set()
    mock_svc = MagicMock()

    def _messages_get(*args, **kwargs):
        msg_id = kwargs.get("id")
        request = MagicMock()
        if msg_id in message_ids:
            request.execute.return_value = {"id": msg_id}
        else:
            request.execute.side_effect = Exception("not found")
        return request

    def _threads_get(*args, **kwargs):
        thread_id = kwargs.get("id")
        request = MagicMock()
        if thread_id in thread_ids:
            request.execute.return_value = {"id": thread_id}
        else:
            request.execute.side_effect = Exception("not found")
        return request

    mock_svc.users().messages().get.side_effect = _messages_get
    mock_svc.users().threads().get.side_effect = _threads_get
    return mock_svc


# ── Archive ──────────────────────────────────────────────────────────────────


class TestGmailArchive:
    def test_archive_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_archive", return_value=True):
            resp = c.post("/messages/gmail/msg123/archive")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_archive_failure(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_archive", return_value=False):
            resp = c.post("/messages/gmail/msg123/archive")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_archive_no_gmail_service(self, client):
        c, _ = client
        resp = c.post("/messages/gmail/msg123/archive")
        assert resp.status_code == 404


# ── Delete ───────────────────────────────────────────────────────────────────


class TestGmailDelete:
    def test_delete_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_delete", return_value=True):
            resp = c.post("/messages/gmail/msg123/delete")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_failure(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_delete", return_value=False):
            resp = c.post("/messages/gmail/msg123/delete")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


# ── Star / Unstar ────────────────────────────────────────────────────────────


class TestGmailStar:
    def test_star_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_star", return_value=True):
            resp = c.post("/messages/gmail/msg123/star")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_unstar_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_unstar", return_value=True):
            resp = c.post("/messages/gmail/msg123/unstar")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ── Read / Unread ────────────────────────────────────────────────────────────


class TestGmailReadUnread:
    def test_mark_read_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_mark_read", return_value=True):
            resp = c.post("/messages/gmail/msg123/read")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_mark_unread_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_mark_unread", return_value=True):
            resp = c.post("/messages/gmail/msg123/unread")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_mark_read_no_service(self, client):
        c, _ = client
        resp = c.post("/messages/gmail/msg123/read")
        assert resp.status_code == 404


# ── Labels ───────────────────────────────────────────────────────────────────


class TestGmailLabels:
    def test_list_labels(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        mock_labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "STARRED", "name": "STARRED", "type": "system"},
            {"id": "Label_1", "name": "Work", "type": "user"},
        ]
        with patch("inbox_server.gmail_labels", return_value=mock_labels):
            resp = c.get("/gmail/labels")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[2]["name"] == "Work"

    def test_list_labels_no_service(self, client):
        c, _ = client
        resp = c.get("/gmail/labels")
        assert resp.status_code == 404


# ── Attachments ──────────────────────────────────────────────────────────────


class TestGmailAttachments:
    def test_download_attachment(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        test_data = b"Hello, World!"
        with patch("inbox_server.gmail_attachment_download", return_value=test_data):
            resp = c.get("/messages/gmail/msg123/attachments/att456")
        assert resp.status_code == 200
        data = resp.json()
        assert data["size"] == len(test_data)
        # Verify base64 roundtrip
        decoded = base64.urlsafe_b64decode(data["data"])
        assert decoded == test_data

    def test_download_attachment_not_found(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_attachment_download", return_value=None):
            resp = c.get("/messages/gmail/msg123/attachments/att456")
        assert resp.status_code == 404

    def test_download_attachment_no_service(self, client):
        c, _ = client
        resp = c.get("/messages/gmail/msg123/attachments/att456")
        assert resp.status_code == 404


# ── Compose ──────────────────────────────────────────────────────────────────


class TestGmailCompose:
    def test_compose_send_success(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_compose_send", return_value=True):
            resp = c.post(
                "/messages/compose",
                json={"to": "bob@example.com", "subject": "Hello", "body": "Hi Bob"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_compose_send_with_account(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_compose_send", return_value=True):
            resp = c.post(
                "/messages/compose",
                json={
                    "to": "bob@example.com",
                    "subject": "Hello",
                    "body": "Hi Bob",
                    "account": "test@gmail.com",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_compose_send_failure(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_compose_send", return_value=False):
            resp = c.post(
                "/messages/compose",
                json={"to": "bob@example.com", "subject": "Hello", "body": "Hi Bob"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_compose_no_service(self, client):
        c, _ = client
        resp = c.post(
            "/messages/compose",
            json={"to": "bob@example.com", "subject": "Hello", "body": "Hi"},
        )
        assert resp.status_code == 404


class TestGmailReplyRouting:
    def test_reply_auto_routes_to_message_owner(self, client):
        c, state = client
        owner_svc = _mock_gmail_service_for_ids(message_ids={"owner-msg"})
        other_svc = _mock_gmail_service_for_ids()
        state.gmail_services = {
            "default@gmail.com": other_svc,
            "owner@gmail.com": owner_svc,
        }

        with patch("inbox_server.gmail_reply", return_value=True) as mock_reply:
            resp = c.post(
                "/messages/gmail/reply",
                json={
                    "msg_id": "owner-msg",
                    "body": "test body",
                },
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "account": "owner@gmail.com"}
        assert mock_reply.call_args[0][0] is owner_svc

    def test_reply_auto_routes_to_thread_owner(self, client):
        c, state = client
        owner_svc = _mock_gmail_service_for_ids(thread_ids={"owner-thread"})
        other_svc = _mock_gmail_service_for_ids()
        state.gmail_services = {
            "default@gmail.com": other_svc,
            "owner@gmail.com": owner_svc,
        }

        with patch("inbox_server.gmail_reply", return_value=True) as mock_reply:
            resp = c.post(
                "/messages/gmail/reply",
                json={
                    "msg_id": "",
                    "thread_id": "owner-thread",
                    "body": "test body",
                },
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "account": "owner@gmail.com"}
        assert mock_reply.call_args[0][0] is owner_svc

    def test_reply_explicit_account_bypasses_auto_routing(self, client):
        c, state = client
        explicit_svc = _mock_gmail_service_for_ids()
        owner_svc = _mock_gmail_service_for_ids(message_ids={"owner-msg"})
        state.gmail_services = {
            "explicit@gmail.com": explicit_svc,
            "owner@gmail.com": owner_svc,
        }

        with patch("inbox_server.gmail_reply", return_value=True) as mock_reply:
            resp = c.post(
                "/messages/gmail/reply",
                json={
                    "account": "explicit@gmail.com",
                    "msg_id": "owner-msg",
                    "body": "test body",
                },
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "account": "explicit@gmail.com"}
        assert mock_reply.call_args[0][0] is explicit_svc


# ── Gmail conversations by label ─────────────────────────────────────────────


class TestGmailConversationsByLabel:
    def test_list_by_label(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        from services import Contact

        mock_contacts = [
            Contact(
                id="msg1",
                name="Alice",
                source="gmail",
                snippet="Test subject",
                unread=0,
                last_ts=datetime(2026, 4, 9),
                gmail_account="test@gmail.com",
            )
        ]
        with patch("inbox_server.gmail_contacts_by_label", return_value=mock_contacts):
            resp = c.get("/gmail/conversations", params={"label": "STARRED"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice"

    def test_list_by_label_default_inbox(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        with patch("inbox_server.gmail_contacts_by_label", return_value=[]) as mock_fn:
            resp = c.get("/gmail/conversations")
        assert resp.status_code == 200
        mock_fn.assert_called_once()
        # Verify INBOX was used as default
        call_kwargs = mock_fn.call_args
        assert call_kwargs[1]["label_id"] == "INBOX"


class TestConversationsAccountFiltering:
    def test_list_conversations_respects_gmail_account_filter(self, client):
        c, state = client
        state.gmail_services = {
            "one@gmail.com": MagicMock(),
            "two@gmail.com": MagicMock(),
        }

        from services import Contact

        def _contacts_for_account(service, account_email, limit=20):
            return [
                Contact(
                    id=f"{account_email}-msg",
                    name=account_email,
                    source="gmail",
                    snippet="subject",
                    unread=0,
                    last_ts=datetime(2026, 4, 10),
                    gmail_account=account_email,
                )
            ]

        with patch("inbox_server.gmail_contacts", side_effect=_contacts_for_account):
            resp = c.get("/conversations", params={"source": "gmail", "account": "two@gmail.com"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["gmail_account"] == "two@gmail.com"


# ── Messages with attachments ────────────────────────────────────────────────


class TestGmailMessagesWithAttachments:
    def test_messages_include_attachments(self, client_with_gmail):
        c, state, mock_svc = client_with_gmail
        from services import Contact, Msg

        mock_contact = Contact(
            id="msg1",
            name="Alice",
            source="gmail",
            gmail_account="test@gmail.com",
            thread_id="t1",
        )
        state.conv_cache["gmail:msg1"] = mock_contact

        mock_msgs = [
            Msg(
                sender="Alice",
                body="Check the attachment",
                ts=datetime(2026, 4, 9),
                is_me=False,
                source="gmail",
                attachments=[
                    {
                        "filename": "report.pdf",
                        "mimeType": "application/pdf",
                        "size": 2500000,
                        "attachmentId": "att1",
                        "messageId": "msg1",
                    }
                ],
            )
        ]
        with patch("inbox_server.gmail_thread", return_value=mock_msgs):
            resp = c.get("/messages/gmail/msg1", params={"thread_id": "t1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert len(data[0]["attachments"]) == 1
        assert data[0]["attachments"][0]["filename"] == "report.pdf"
        assert data[0]["attachments"][0]["size"] == 2500000


# ── Service function tests ───────────────────────────────────────────────────


class TestGmailServiceFunctions:
    def test_gmail_archive(self):
        from services import gmail_archive

        mock_svc = MagicMock()
        mock_svc.users().messages().modify().execute.return_value = {}
        assert gmail_archive(mock_svc, "msg1") is True

    def test_gmail_delete(self):
        from services import gmail_delete

        mock_svc = MagicMock()
        mock_svc.users().messages().trash().execute.return_value = {}
        assert gmail_delete(mock_svc, "msg1") is True

    def test_gmail_star(self):
        from services import gmail_star

        mock_svc = MagicMock()
        mock_svc.users().messages().modify().execute.return_value = {}
        assert gmail_star(mock_svc, "msg1") is True

    def test_gmail_unstar(self):
        from services import gmail_unstar

        mock_svc = MagicMock()
        mock_svc.users().messages().modify().execute.return_value = {}
        assert gmail_unstar(mock_svc, "msg1") is True

    def test_gmail_mark_read(self):
        from services import gmail_mark_read

        mock_svc = MagicMock()
        mock_svc.users().messages().modify().execute.return_value = {}
        assert gmail_mark_read(mock_svc, "msg1") is True

    def test_gmail_mark_unread(self):
        from services import gmail_mark_unread

        mock_svc = MagicMock()
        mock_svc.users().messages().modify().execute.return_value = {}
        assert gmail_mark_unread(mock_svc, "msg1") is True

    def test_gmail_labels(self):
        from services import gmail_labels

        mock_svc = MagicMock()
        mock_svc.users().labels().list().execute.return_value = {
            "labels": [
                {"id": "INBOX", "name": "INBOX", "type": "system"},
                {"id": "Label_1", "name": "Work", "type": "user"},
            ]
        }
        result = gmail_labels(mock_svc)
        assert len(result) == 2
        assert result[0]["id"] == "INBOX"
        assert result[1]["name"] == "Work"

    def test_gmail_attachment_download(self):
        from services import gmail_attachment_download

        mock_svc = MagicMock()
        test_data = base64.urlsafe_b64encode(b"file contents").decode()
        mock_svc.users().messages().attachments().get().execute.return_value = {
            "data": test_data,
        }
        result = gmail_attachment_download(mock_svc, "msg1", "att1")
        assert result == b"file contents"

    def test_gmail_attachment_download_empty(self):
        from services import gmail_attachment_download

        mock_svc = MagicMock()
        mock_svc.users().messages().attachments().get().execute.return_value = {"data": ""}
        result = gmail_attachment_download(mock_svc, "msg1", "att1")
        assert result is None

    def test_gmail_compose_send(self):
        from services import gmail_compose_send

        mock_svc = MagicMock()
        mock_svc.users().messages().send().execute.return_value = {"id": "sent1"}
        assert gmail_compose_send(mock_svc, "bob@test.com", "Hello", "Body") is True

    def test_gmail_compose_send_failure(self):
        from services import gmail_compose_send

        mock_svc = MagicMock()
        mock_svc.users().messages().send().execute.side_effect = Exception("API error")
        assert gmail_compose_send(mock_svc, "bob@test.com", "Hello", "Body") is False

    def test_extract_attachments(self):
        from services import _extract_attachments

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "aGVsbG8="},
                },
                {
                    "filename": "doc.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att123", "size": 1024},
                },
            ],
        }
        result = _extract_attachments(payload, "msg1")
        assert len(result) == 1
        assert result[0]["filename"] == "doc.pdf"
        assert result[0]["attachmentId"] == "att123"
        assert result[0]["size"] == 1024
        assert result[0]["messageId"] == "msg1"

    def test_extract_attachments_nested(self):
        from services import _extract_attachments

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": "aGVsbG8="}},
                    ],
                },
                {
                    "filename": "image.png",
                    "mimeType": "image/png",
                    "body": {"attachmentId": "att456", "size": 5000},
                },
            ],
        }
        result = _extract_attachments(payload, "msg2")
        assert len(result) == 1
        assert result[0]["filename"] == "image.png"

    def test_extract_attachments_empty(self):
        from services import _extract_attachments

        payload = {"mimeType": "text/plain", "body": {"data": "aGVsbG8="}}
        result = _extract_attachments(payload, "msg3")
        assert result == []

    def test_gmail_contacts_by_label(self):
        from services import gmail_contacts_by_label

        mock_svc = MagicMock()
        # Mock the messages.list call
        mock_svc.users().messages().list().execute.return_value = {
            "messages": [{"id": "msg1", "threadId": "t1"}]
        }
        # Mock metadata batch - since no batch support in mock
        mock_svc.new_batch_http_request = None
        delattr(mock_svc, "new_batch_http_request")
        mock_svc.users().messages().get().execute.return_value = {
            "id": "msg1",
            "threadId": "t1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Alice <alice@test.com>"},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Date", "value": "2026-04-09"},
                    {"name": "Message-ID", "value": "<msg@test.com>"},
                ],
            },
            "labelIds": ["STARRED", "UNREAD"],
            "internalDate": "1744156800000",
        }

        result = gmail_contacts_by_label(mock_svc, "test@gmail.com", "STARRED", limit=10)
        assert len(result) == 1
        assert result[0].name == "Alice"
        assert result[0].unread == 1


# ── Client method tests ──────────────────────────────────────────────────────


class TestGmailClientMethods:
    @pytest.fixture
    def mock_client(self):
        from inbox_client import InboxClient

        c = InboxClient.__new__(InboxClient)
        c._client = MagicMock()
        return c

    def _mock_response(self, data, status_code=200):
        import httpx

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()
        return resp

    def test_gmail_archive(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_archive("msg1") is True
        mock_client._client.post.assert_called_once_with("/messages/gmail/msg1/archive")

    def test_gmail_delete(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_delete("msg1") is True
        mock_client._client.post.assert_called_once_with("/messages/gmail/msg1/delete")

    def test_gmail_star(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_star("msg1") is True
        mock_client._client.post.assert_called_once_with("/messages/gmail/msg1/star")

    def test_gmail_unstar(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_unstar("msg1") is True
        mock_client._client.post.assert_called_once_with("/messages/gmail/msg1/unstar")

    def test_gmail_mark_read(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_mark_read("msg1") is True
        mock_client._client.post.assert_called_once_with("/messages/gmail/msg1/read")

    def test_gmail_mark_unread(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_mark_unread("msg1") is True
        mock_client._client.post.assert_called_once_with("/messages/gmail/msg1/unread")

    def test_gmail_labels(self, mock_client):
        mock_client._client.get.return_value = self._mock_response(
            [{"id": "INBOX", "name": "INBOX"}]
        )
        result = mock_client.gmail_labels()
        assert len(result) == 1

    def test_gmail_attachment(self, mock_client):
        mock_client._client.get.return_value = self._mock_response({"data": "dGVzdA==", "size": 4})
        result = mock_client.gmail_attachment("msg1", "att1")
        assert result["size"] == 4

    def test_gmail_compose(self, mock_client):
        mock_client._client.post.return_value = self._mock_response({"ok": True})
        assert mock_client.gmail_compose("bob@test.com", "Hi", "Body") is True

    def test_gmail_conversations_by_label(self, mock_client):
        mock_client._client.get.return_value = self._mock_response([{"name": "Alice"}])
        result = mock_client.gmail_conversations_by_label(label="STARRED")
        assert len(result) == 1
        call_args = mock_client._client.get.call_args
        assert call_args[1]["params"]["label"] == "STARRED"
