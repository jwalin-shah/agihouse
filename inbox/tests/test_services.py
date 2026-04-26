"""Tests for services.py — pure helper functions (no external dependencies)."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

from loguru import logger

import services
from services import (
    _build_event_body,
    _clean_body,
    _clean_email_body,
    _escape_applescript,
    _html_to_text,
    _parse_email_address,
    _parse_time,
    parse_quick_event,
)

# ── _clean_body ─────────────────────────────────────────────────────────────


class TestCleanBody:
    def test_none_returns_empty(self):
        assert _clean_body(None) == ""

    def test_empty_string(self):
        assert _clean_body("") == ""

    def test_strips_whitespace(self):
        assert _clean_body("  hello  ") == "hello"

    def test_replaces_attachment_placeholder(self):
        result = _clean_body("Check this \ufffc out")
        assert "(attachment)" in result
        assert "\ufffc" not in result

    def test_normal_text_unchanged(self):
        assert _clean_body("Hello, world!") == "Hello, world!"


# ── _parse_email_address ────────────────────────────────────────────────────


class TestParseEmailAddress:
    def test_name_and_email(self):
        name, email = _parse_email_address("Alice Smith <alice@example.com>")
        assert name == "Alice Smith"
        assert email == "alice@example.com"

    def test_quoted_name(self):
        name, email = _parse_email_address('"Alice Smith" <alice@example.com>')
        assert name == "Alice Smith"
        assert email == "alice@example.com"

    def test_email_only_angle_brackets(self):
        name, email = _parse_email_address("<alice@example.com>")
        assert name == "alice@example.com"
        assert email == "alice@example.com"

    def test_bare_email(self):
        name, email = _parse_email_address("alice@example.com")
        assert name == "alice@example.com"
        assert email == "alice@example.com"

    def test_whitespace_handling(self):
        name, email = _parse_email_address("  Alice Smith  <alice@example.com>  ")
        assert name == "Alice Smith"
        assert email == "alice@example.com"


# ── _html_to_text ──────────────────────────────────────────────────────────


class TestHtmlToText:
    def test_strips_tags(self):
        assert "hello" in _html_to_text("<p>hello</p>")

    def test_br_to_newline(self):
        result = _html_to_text("line1<br>line2")
        assert "line1\nline2" in result

    def test_strips_style_tags(self):
        result = _html_to_text("<style>body{color:red}</style>visible")
        assert "color:red" not in result
        assert "visible" in result

    def test_strips_script_tags(self):
        result = _html_to_text("<script>alert('x')</script>visible")
        assert "alert" not in result
        assert "visible" in result

    def test_list_items(self):
        result = _html_to_text("<ul><li>one</li><li>two</li></ul>")
        assert "one" in result
        assert "two" in result

    def test_link_extraction(self):
        html = '<a href="https://example.com">Click here</a>'
        result = _html_to_text(html)
        assert "Click here" in result
        assert "https://example.com" in result

    def test_entity_decoding(self):
        assert "&" in _html_to_text("&amp;")
        assert "<" in _html_to_text("&lt;")
        assert ">" in _html_to_text("&gt;")
        assert '"' in _html_to_text("&quot;")
        assert "'" in _html_to_text("&#39;")
        assert " " in _html_to_text("&nbsp;")


# ── _clean_email_body ──────────────────────────────────────────────────────


class TestCleanEmailBody:
    def test_strips_quoted_replies(self):
        body = "Thanks!\n> Previous message\n> More quoting"
        result = _clean_email_body(body)
        assert "Thanks!" in result
        assert "Previous message" not in result

    def test_strips_on_wrote_line(self):
        body = "Sounds good\nOn Mon, Jan 1, 2025 Alice wrote:\nOld stuff"
        result = _clean_email_body(body)
        assert "Sounds good" in result
        assert "Old stuff" not in result

    def test_strips_signature_dashes(self):
        body = "Main content\n--\nSignature here"
        result = _clean_email_body(body)
        assert "Main content" in result
        assert "Signature here" not in result

    def test_strips_triple_dashes(self):
        body = "Main content\n---\nFooter"
        result = _clean_email_body(body)
        assert "Main content" in result
        assert "Footer" not in result

    def test_strips_unsubscribe(self):
        body = "Content here\nunsubscribe from this list"
        result = _clean_email_body(body)
        assert "Content here" in result
        assert "unsubscribe" not in result.lower()

    def test_strips_tracking_urls(self):
        body = "Content\nhttps://click.example.com/track/123"
        result = _clean_email_body(body)
        assert "Content" in result
        assert "track" not in result

    def test_collapses_excessive_newlines(self):
        body = "Line1\n\n\n\n\nLine2"
        result = _clean_email_body(body)
        assert "\n\n\n" not in result
        assert "Line1" in result
        assert "Line2" in result

    def test_normal_body_preserved(self):
        body = "Hey, just wanted to check in.\nLet me know!"
        result = _clean_email_body(body)
        assert "Hey, just wanted to check in." in result
        assert "Let me know!" in result


# ── _parse_time ─────────────────────────────────────────────────────────────


class TestParseTime:
    def test_24h_format(self):
        result = _parse_time("14:30")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_12h_pm(self):
        result = _parse_time("2pm")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 0

    def test_12h_am(self):
        result = _parse_time("9am")
        assert result is not None
        assert result.hour == 9

    def test_12h_with_minutes(self):
        result = _parse_time("2:30pm")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_12pm_is_noon(self):
        result = _parse_time("12pm")
        assert result is not None
        assert result.hour == 12

    def test_12am_is_midnight(self):
        result = _parse_time("12am")
        assert result is not None
        assert result.hour == 0

    def test_invalid_returns_none(self):
        assert _parse_time("not a time") is None
        assert _parse_time("") is None

    def test_case_insensitive(self):
        result = _parse_time("3PM")
        assert result is not None
        assert result.hour == 15

    def test_with_whitespace(self):
        result = _parse_time("  3pm  ")
        assert result is not None
        assert result.hour == 15


# ── parse_quick_event ───────────────────────────────────────────────────────


class TestParseQuickEvent:
    def test_basic_time_range(self):
        result = parse_quick_event("Meeting 2pm-3pm")
        assert result["summary"] == "Meeting"
        assert result["start"].hour == 14
        assert result["end"].hour == 15
        assert result["all_day"] is False

    def test_24h_time_range(self):
        result = parse_quick_event("Standup 09:00-09:30")
        assert result["summary"] == "Standup"
        assert result["start"].hour == 9
        assert result["start"].minute == 0
        assert result["end"].hour == 9
        assert result["end"].minute == 30

    def test_with_location(self):
        result = parse_quick_event("Lunch 12pm-1pm @ Cafe Roma")
        assert result["summary"] == "Lunch"
        assert result["location"] == "Cafe Roma"

    def test_all_day_event(self):
        result = parse_quick_event("all day: Team Offsite")
        assert result["summary"] == "Team Offsite"
        assert result["all_day"] is True

    def test_all_day_with_location(self):
        result = parse_quick_event("all day: Conference @ Convention Center")
        assert result["summary"] == "Conference"
        assert result["location"] == "Convention Center"
        assert result["all_day"] is True

    def test_no_time_falls_back(self):
        result = parse_quick_event("Quick sync")
        assert result["summary"] == "Quick sync"
        # Should default to now → now+1h
        assert result["all_day"] is False
        diff = result["end"] - result["start"]
        assert diff == timedelta(hours=1)

    def test_en_dash_separator(self):
        result = parse_quick_event("Meeting 2pm\u20133pm")
        assert result["summary"] == "Meeting"
        assert result["start"].hour == 14
        assert result["end"].hour == 15

    def test_12h_with_minutes_range(self):
        result = parse_quick_event("Call 2:30pm-3:45pm")
        assert result["start"].hour == 14
        assert result["start"].minute == 30
        assert result["end"].hour == 15
        assert result["end"].minute == 45


# ── _build_event_body ──────────────────────────────────────────────────────


class TestBuildEventBody:
    def test_all_day_event(self):
        start = datetime(2025, 6, 15)
        end = datetime(2025, 6, 16)
        body = _build_event_body("Day Off", start, end, all_day=True)
        assert body["summary"] == "Day Off"
        assert body["start"] == {"date": "2025-06-15"}
        assert body["end"] == {"date": "2025-06-16"}

    def test_timed_event(self):
        start = datetime(2025, 6, 15, 14, 0).astimezone()
        end = datetime(2025, 6, 15, 15, 0).astimezone()
        body = _build_event_body("Meeting", start, end)
        assert body["summary"] == "Meeting"
        assert "dateTime" in body["start"]
        assert "dateTime" in body["end"]

    def test_with_location(self):
        start = datetime(2025, 6, 15)
        end = datetime(2025, 6, 16)
        body = _build_event_body("Event", start, end, location="Room 5", all_day=True)
        assert body["location"] == "Room 5"

    def test_with_description(self):
        start = datetime(2025, 6, 15)
        end = datetime(2025, 6, 16)
        body = _build_event_body("Event", start, end, description="Notes here", all_day=True)
        assert body["description"] == "Notes here"

    def test_no_location_omitted(self):
        start = datetime(2025, 6, 15)
        end = datetime(2025, 6, 16)
        body = _build_event_body("Event", start, end, all_day=True)
        assert "location" not in body


class TestAppleScriptEscaping:
    def test_escape_applescript_handles_special_characters(self):
        escaped = _escape_applescript('Hello "Inbox"\\{team}\nTab\t🙂')

        assert escaped.startswith('"Hello ')
        assert "quote" in escaped
        assert "ASCII character 92" in escaped
        assert "ASCII character 123" in escaped
        assert "ASCII character 125" in escaped
        assert "ASCII character 10" in escaped
        assert "ASCII character 9" in escaped
        assert "🙂" in escaped
        assert "\n" not in escaped

    def test_imessage_group_send_uses_escaped_expressions(self, monkeypatch):
        captured: dict[str, str] = {}

        def fake_run(args: list[str], **kwargs: Any):
            captured["script"] = args[2]

            class _Result:
                returncode = 0

            return _Result()

        monkeypatch.setattr(services.subprocess, "run", fake_run)

        contact = services.Contact(
            id="1",
            name="Group chat",
            source="imessage",
            guid='chat-{team}"primary"',
            is_group=True,
        )
        text = 'Check "quoted" \\ path {today}\nnow'

        assert services.imsg_send(contact, text) is True

        escaped_guid = services._escape_applescript(contact.guid)
        escaped_text = services._escape_applescript(text)
        script = captured["script"]
        assert escaped_guid in script
        assert escaped_text in script
        assert 'send "Check "quoted"' not in script

    def test_note_and_reminder_scripts_use_escaped_expressions(self, monkeypatch):
        captured: list[str] = []

        def fake_run(args: list[str], **kwargs: Any):
            captured.append(args[2])

            class _Result:
                returncode = 0
                stdout = "ok"

            return _Result()

        monkeypatch.setattr(services.subprocess, "run", fake_run)

        title = 'Title "quoted" \\ {set}\nnext'
        list_name = "Errands {today}"
        notes = 'Remember "milk" \\ bread'
        due_date = "April 10, 2026 5:00 PM"

        assert services.note_body(title) == "ok"
        assert services.reminder_complete(title) is True
        assert (
            services.reminder_create(
                title=title,
                list_name=list_name,
                due_date=due_date,
                notes=notes,
            )
            is True
        )

        escaped_title = services._escape_applescript(title)
        escaped_list = services._escape_applescript(list_name)
        escaped_notes = services._escape_applescript(notes)
        escaped_due_date = services._escape_applescript(due_date)

        assert escaped_title in captured[0]
        assert escaped_title in captured[1]
        assert escaped_title in captured[2]
        assert escaped_list in captured[2]
        assert escaped_notes in captured[2]
        assert escaped_due_date in captured[2]


# ── structured logging ─────────────────────────────────────────────────────


class _FailingGmailMessages:
    def send(self, *args: Any, **kwargs: Any) -> _FailingGmailMessages:
        return self

    def execute(self) -> None:
        raise RuntimeError("gmail send exploded")


class _FailingGmailUsers:
    def messages(self) -> _FailingGmailMessages:
        return _FailingGmailMessages()


class _FailingGmailService:
    def users(self) -> _FailingGmailUsers:
        return _FailingGmailUsers()


class _FakeGmailMetadataRequest:
    def __init__(self, service: _FakeBatchGmailService, message: dict[str, Any]):
        self._service = service
        self.message = message

    def execute(self) -> dict[str, Any]:
        self._service.http_call_count += 1
        self._service.standalone_metadata_calls += 1
        return self.message


class _FakeGmailListRequest:
    def __init__(self, service: _FakeBatchGmailService, messages: list[dict[str, str]]):
        self._service = service
        self._messages = messages

    def execute(self) -> dict[str, list[dict[str, str]]]:
        self._service.http_call_count += 1
        return {"messages": self._messages}


class _FakeGmailBatchRequest:
    def __init__(
        self,
        service: _FakeBatchGmailService,
        callback: Callable[[str, dict[str, Any] | None, Exception | None], None],
    ):
        self._service = service
        self._callback = callback
        self._requests: list[tuple[str, _FakeGmailMetadataRequest]] = []

    def add(self, request: _FakeGmailMetadataRequest, request_id: str) -> None:
        self._requests.append((request_id, request))

    def execute(self) -> None:
        self._service.http_call_count += 1
        for request_id, request in self._requests:
            self._callback(request_id, request.message, None)


class _FakeBatchGmailMessages:
    def __init__(self, service: _FakeBatchGmailService):
        self._service = service

    def list(self, **kwargs: Any) -> _FakeGmailListRequest:
        return _FakeGmailListRequest(self._service, self._service.listed_messages)

    def get(self, **kwargs: Any) -> _FakeGmailMetadataRequest:
        message_id = kwargs["id"]
        return _FakeGmailMetadataRequest(self._service, self._service.metadata_by_id[message_id])


class _FakeBatchGmailUsers:
    def __init__(self, service: _FakeBatchGmailService):
        self._service = service

    def messages(self) -> _FakeBatchGmailMessages:
        return _FakeBatchGmailMessages(self._service)


class _FakeBatchGmailService:
    def __init__(
        self, listed_messages: list[dict[str, str]], metadata_by_id: dict[str, dict[str, Any]]
    ):
        self.listed_messages = listed_messages
        self.metadata_by_id = metadata_by_id
        self.http_call_count = 0
        self.batch_request_count = 0
        self.standalone_metadata_calls = 0

    def users(self) -> _FakeBatchGmailUsers:
        return _FakeBatchGmailUsers(self)

    def new_batch_http_request(
        self,
        callback: Callable[[str, dict[str, Any] | None, Exception | None], None],
    ) -> _FakeGmailBatchRequest:
        self.batch_request_count += 1
        return _FakeGmailBatchRequest(self, callback)


class TestStructuredLogging:
    def test_imsg_contacts_logs_exception(self, monkeypatch, tmp_path):
        db_path = tmp_path / "chat.db"
        db_path.write_text("")
        monkeypatch.setattr(services, "IMSG_DB", db_path)

        def boom(*args: Any, **kwargs: Any) -> None:
            raise sqlite3.OperationalError("chat db exploded")

        monkeypatch.setattr(services.sqlite3, "connect", boom)
        sink = StringIO()
        sink_id = logger.add(sink, format="{message}")
        try:
            assert services.imsg_contacts() == []
        finally:
            logger.remove(sink_id)

        log_output = sink.getvalue()
        assert "imsg_contacts" in log_output
        assert "limit=30" in log_output
        assert "chat db exploded" in log_output

    def test_gmail_send_logs_exception(self):
        contact = services.Contact(
            id="msg-1",
            name="Alice",
            source="gmail",
            reply_to="alice@example.com",
            snippet="Hello",
            thread_id="thread-1",
        )
        sink = StringIO()
        sink_id = logger.add(sink, format="{message}")
        try:
            assert services.gmail_send(_FailingGmailService(), contact, "hello there") is False
        finally:
            logger.remove(sink_id)

        log_output = sink.getvalue()
        assert "gmail_send" in log_output
        assert "reply_to='alice@example.com'" in log_output
        assert "gmail send exploded" in log_output

    def test_services_has_no_silent_exception_swallowing(self):
        services_text = Path(services.__file__).read_text()

        assert re.search(r"except.*pass", services_text) is None
        assert re.search(r"except.*return\s+\[\]", services_text) is None
        assert re.search(r"except.*return\s+False", services_text) is None


class TestGmailContactsBatching:
    def test_gmail_contacts_batches_metadata_fetches(self):
        listed_messages = [
            {"id": "msg-1", "threadId": "thread-1"},
            {"id": "msg-2", "threadId": "thread-2"},
            {"id": "msg-3", "threadId": "thread-2"},
            {"id": "msg-4", "threadId": "thread-3"},
        ]
        metadata_by_id = {
            "msg-1": {
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Alice <alice@example.com>"},
                        {"name": "Subject", "value": "Alpha"},
                        {"name": "Message-ID", "value": "<m1@example.com>"},
                    ]
                },
                "labelIds": ["UNREAD", "INBOX"],
                "internalDate": "1715000000000",
            },
            "msg-2": {
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Bob <bob@example.com>"},
                        {"name": "Subject", "value": "Bravo"},
                        {"name": "Message-ID", "value": "<m2@example.com>"},
                    ]
                },
                "labelIds": ["INBOX"],
                "internalDate": "1715000001000",
            },
            "msg-4": {
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Carol <carol@example.com>"},
                        {"name": "Subject", "value": "Charlie"},
                        {"name": "Message-ID", "value": "<m4@example.com>"},
                    ]
                },
                "labelIds": ["UNREAD", "INBOX"],
                "internalDate": "1715000002000",
            },
        }
        service = _FakeBatchGmailService(listed_messages, metadata_by_id)

        contacts = services.gmail_contacts(service, "acct@example.com", limit=4)

        assert [contact.id for contact in contacts] == ["msg-1", "msg-2", "msg-4"]
        assert [contact.name for contact in contacts] == ["Alice", "Bob", "Carol"]
        assert [contact.unread for contact in contacts] == [1, 0, 1]
        assert service.batch_request_count == 1
        assert service.standalone_metadata_calls == 0
        assert service.http_call_count == 2
        assert service.http_call_count < len(contacts)


class TestSqliteConnectionManagement:
    def test_notes_list_reuses_cached_connection(self, monkeypatch, tmp_path):
        db_path = tmp_path / "NoteStore.sqlite"
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE ZICCLOUDSYNCINGOBJECT (
                Z_PK INTEGER PRIMARY KEY,
                ZTITLE1 TEXT,
                ZSNIPPET TEXT,
                ZMODIFICATIONDATE1 REAL,
                ZFOLDER INTEGER,
                ZTITLE2 TEXT,
                ZMARKEDFORDELETION INTEGER
            )
            """
        )
        cur.execute(
            """
            INSERT INTO ZICCLOUDSYNCINGOBJECT
                (Z_PK, ZTITLE1, ZSNIPPET, ZMODIFICATIONDATE1, ZFOLDER, ZTITLE2, ZMARKEDFORDELETION)
            VALUES
                (1, 'Test note', 'Snippet', 60, NULL, NULL, 0)
            """
        )
        conn.commit()
        conn.close()

        services.close_sqlite_connections()
        monkeypatch.setattr(services, "NOTES_DB", db_path)

        connect_calls = 0
        real_connect = services.sqlite3.connect

        def counting_connect(*args: Any, **kwargs: Any):
            nonlocal connect_calls
            connect_calls += 1
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(services.sqlite3, "connect", counting_connect)
        try:
            first = services.notes_list()
            second = services.notes_list()
        finally:
            services.close_sqlite_connections()

        assert [note.title for note in first] == ["Test note"]
        assert [note.title for note in second] == ["Test note"]
        assert connect_calls == 1

    def test_locked_sqlite_returns_empty_and_logs_warning(self, monkeypatch, tmp_path):
        db_path = tmp_path / "NoteStore.sqlite"
        db_path.write_text("")
        monkeypatch.setattr(services, "NOTES_DB", db_path)

        class _LockedCursor:
            def execute(self, *args: Any, **kwargs: Any) -> None:
                raise sqlite3.OperationalError("database is locked")

        class _LockedConnection:
            def cursor(self) -> _LockedCursor:
                return _LockedCursor()

        monkeypatch.setattr(
            services._sqlite_connections, "get_connection", lambda path: _LockedConnection()
        )
        sink = StringIO()
        sink_id = logger.add(sink, format="{message}")
        try:
            assert services.notes_list() == []
        finally:
            logger.remove(sink_id)

        log_output = sink.getvalue()
        assert "notes_list" in log_output
        assert "database is locked" in log_output

    def test_close_sqlite_connections_clears_cache(self, monkeypatch, tmp_path):
        db_path = tmp_path / "NoteStore.sqlite"
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE ZICCLOUDSYNCINGOBJECT (
                Z_PK INTEGER PRIMARY KEY,
                ZTITLE1 TEXT,
                ZSNIPPET TEXT,
                ZMODIFICATIONDATE1 REAL,
                ZFOLDER INTEGER,
                ZTITLE2 TEXT,
                ZMARKEDFORDELETION INTEGER
            )
            """
        )
        conn.commit()
        conn.close()

        services.close_sqlite_connections()
        monkeypatch.setattr(services, "NOTES_DB", db_path)

        services.notes_list()
        assert services._sqlite_connections.cached_connection_count() == 1

        services.close_sqlite_connections()

        assert services._sqlite_connections.cached_connection_count() == 0


class _FakeRefreshedCredentials:
    def __init__(self) -> None:
        self.valid = False
        self.expired = True
        self.refresh_token = "refresh-token"
        self.scopes = list(services.GOOGLE_SCOPES)

    def refresh(self, request: Any) -> None:
        self.valid = True

    def to_json(self) -> str:
        return '{"token": "updated"}'


class TestTokenFileLocking:
    def test_load_creds_refresh_uses_locked_write(self, monkeypatch, tmp_path):
        token_path = tmp_path / "acct.json"
        token_path.write_text("{}")
        fake_creds = _FakeRefreshedCredentials()
        writes: list[tuple[Path, str]] = []

        monkeypatch.setattr(
            services.Credentials,
            "from_authorized_user_file",
            lambda path: fake_creds,
        )

        def fake_write_text_with_lock(path: Path, payload: str) -> None:
            writes.append((path, payload))
            path.write_text(payload)

        monkeypatch.setattr(services, "_write_text_with_lock", fake_write_text_with_lock)

        creds = services._load_creds(token_path)

        assert creds is fake_creds
        assert writes == [(token_path, '{"token": "updated"}')]
        assert token_path.read_text() == '{"token": "updated"}'

    def test_write_text_with_lock_preserves_valid_json_under_concurrent_writes(self, tmp_path):
        token_path = tmp_path / "acct.json"
        payloads = [
            json.dumps({"token": "first", "count": 1}),
            json.dumps({"token": "second", "count": 2}),
        ]
        start = threading.Barrier(3)

        def writer(payload: str) -> None:
            start.wait()
            for _ in range(25):
                services._write_text_with_lock(token_path, payload)

        threads = [threading.Thread(target=writer, args=(payload,)) for payload in payloads]
        for thread in threads:
            thread.start()

        start.wait()

        for thread in threads:
            thread.join()

        assert json.loads(token_path.read_text()) in [
            {"token": "first", "count": 1},
            {"token": "second", "count": 2},
        ]


# ── Contacts favorites ───────────────────────────────────────────────────────


def test_load_favorites_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("services.FAVORITES_FILE", tmp_path / "favorites.json")
    from services import load_favorites

    result = load_favorites()
    assert result == set()


def test_save_and_load_favorites_round_trip(tmp_path, monkeypatch):
    fav_file = tmp_path / "favorites.json"
    monkeypatch.setattr("services.FAVORITES_FILE", fav_file)
    from services import load_favorites, save_favorites

    ids = {"alice@example.com", "bob@example.com"}
    save_favorites(ids)
    loaded = load_favorites()
    assert loaded == ids


def test_save_favorites_creates_parent_dirs(tmp_path, monkeypatch):
    fav_file = tmp_path / "config" / "inbox" / "favorites.json"
    monkeypatch.setattr("services.FAVORITES_FILE", fav_file)
    from services import save_favorites

    save_favorites({"test@example.com"})
    assert fav_file.exists()


def test_load_favorites_handles_corrupt_file(tmp_path, monkeypatch):
    fav_file = tmp_path / "favorites.json"
    fav_file.write_text("NOT VALID JSON")
    monkeypatch.setattr("services.FAVORITES_FILE", fav_file)
    from services import load_favorites

    result = load_favorites()
    assert result == set()
