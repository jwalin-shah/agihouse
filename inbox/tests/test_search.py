"""Tests for search_all() service function and /search endpoint."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

# ── Helpers ──────────────────────────────────────────────────────────────────


APPLE_EPOCH = datetime(2001, 1, 1)


def _apple_secs(dt: datetime) -> float:
    return (dt - APPLE_EPOCH).total_seconds()


# ── _make_snippet ────────────────────────────────────────────────────────────


class TestMakeSnippet:
    def test_match_in_middle(self):
        from services import _make_snippet

        text = "hello world foo bar"
        result = _make_snippet(text, "world")
        assert "world" in result

    def test_empty_text(self):
        from services import _make_snippet

        assert _make_snippet("", "query") == ""

    def test_no_match_returns_prefix(self):
        from services import _make_snippet

        text = "abcdefgh"
        result = _make_snippet(text, "xyz")
        assert result == text[:150]

    def test_long_text_truncated(self):
        from services import _make_snippet

        text = "x" * 300
        result = _make_snippet(text, "x")
        assert len(result) <= 155  # 150 + possible ellipsis chars


# ── _search_imessage ─────────────────────────────────────────────────────────


class TestSearchImessage:
    def test_returns_empty_when_db_missing(self, tmp_path, monkeypatch):
        from services import _search_imessage

        monkeypatch.setattr("services.IMSG_DB", tmp_path / "nonexistent.db")
        assert _search_imessage("hello", 10) == []

    def test_basic_search(self, tmp_path, monkeypatch):
        from services import _search_imessage

        db = tmp_path / "chat.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE chat (rowid INTEGER PRIMARY KEY, guid TEXT, display_name TEXT);
            CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message (
                rowid INTEGER PRIMARY KEY,
                text TEXT,
                is_from_me INTEGER DEFAULT 0,
                is_read INTEGER DEFAULT 1,
                date INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat_message_join (
                chat_id INTEGER,
                message_id INTEGER
            );
        """)
        ts_apple = int((datetime(2026, 4, 1, 12, 0, 0) - datetime(2001, 1, 1)).total_seconds())
        conn.execute("INSERT INTO chat VALUES (1, 'iMessage;+;+15551234567', NULL)")
        conn.execute(
            "INSERT INTO message VALUES (1, 'Hello world test', 0, 1, ?, 0)",
            (ts_apple * 1_000_000_000,),
        )
        conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
        conn.commit()
        conn.close()

        monkeypatch.setattr("services.IMSG_DB", db)
        results = _search_imessage("world", 10)
        assert len(results) == 1
        assert results[0]["source"] == "imessage"
        assert results[0]["id"] == "1"
        assert "world" in results[0]["snippet"].lower()

    def test_no_match_returns_empty(self, tmp_path, monkeypatch):
        from services import _search_imessage

        db = tmp_path / "chat.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE chat (rowid INTEGER PRIMARY KEY, guid TEXT, display_name TEXT);
            CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message (
                rowid INTEGER PRIMARY KEY,
                text TEXT,
                is_from_me INTEGER DEFAULT 0,
                is_read INTEGER DEFAULT 1,
                date INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        """)
        conn.execute("INSERT INTO chat VALUES (1, 'iMessage;+;+15551234567', NULL)")
        conn.execute("INSERT INTO message VALUES (1, 'Hello world', 0, 1, 0, 0)")
        conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
        conn.commit()
        conn.close()

        monkeypatch.setattr("services.IMSG_DB", db)
        assert _search_imessage("zzznomatch", 10) == []


# ── _search_notes ────────────────────────────────────────────────────────────


class TestSearchNotes:
    def test_returns_empty_when_db_missing(self, tmp_path, monkeypatch):
        from services import _search_notes

        monkeypatch.setattr("services.NOTES_DB", tmp_path / "nonexistent.db")
        assert _search_notes("hello", 10) == []

    def test_basic_search(self, tmp_path, monkeypatch):
        from services import _search_notes

        db = tmp_path / "NoteStore.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE ZICCLOUDSYNCINGOBJECT (
                Z_PK INTEGER PRIMARY KEY,
                ZTITLE1 TEXT,
                ZSNIPPET TEXT,
                ZMODIFICATIONDATE1 REAL,
                ZFOLDER INTEGER,
                ZMARKEDFORDELETION INTEGER
            );
        """)
        mod = _apple_secs(datetime(2026, 4, 1))
        conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (1, 'Shopping list', 'milk eggs', ?, NULL, 0)",
            (mod,),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("services.NOTES_DB", db)
        results = _search_notes("shopping", 10)
        assert len(results) == 1
        assert results[0]["source"] == "notes"
        assert results[0]["id"] == "1"
        assert "Shopping" in results[0]["title"]


# ── _search_reminders ────────────────────────────────────────────────────────


class TestSearchReminders:
    def test_basic_search(self, tmp_path, monkeypatch):
        from services import _search_reminders

        db = tmp_path / "Data-test.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE ZREMCDBASELIST (
                Z_PK INTEGER PRIMARY KEY, ZNAME TEXT, ZMARKEDFORDELETION INTEGER
            );
            CREATE TABLE ZREMCDREMINDER (
                Z_PK INTEGER PRIMARY KEY,
                ZTITLE TEXT,
                ZCOMPLETED INTEGER DEFAULT 0,
                ZFLAGGED INTEGER DEFAULT 0,
                ZPRIORITY INTEGER DEFAULT 0,
                ZDUEDATE REAL,
                ZNOTES TEXT,
                ZCREATIONDATE REAL,
                ZLIST INTEGER,
                ZMARKEDFORDELETION INTEGER
            );
        """)
        conn.execute("INSERT INTO ZREMCDBASELIST VALUES (1, 'Work', 0)")
        created = _apple_secs(datetime(2026, 4, 1))
        conn.execute(
            "INSERT INTO ZREMCDREMINDER VALUES (1, 'Ship feature', 0, 0, 0, NULL, 'deadline', ?, 1, 0)",
            (created,),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("services.REMINDERS_DIR", tmp_path)
        results = _search_reminders("feature", 10)
        assert len(results) == 1
        assert results[0]["source"] == "reminders"
        assert results[0]["id"] == "1"

    def test_no_match(self, tmp_path, monkeypatch):
        from services import _search_reminders

        monkeypatch.setattr("services.REMINDERS_DIR", tmp_path)
        assert _search_reminders("zzznomatch", 10) == []


# ── _search_gmail ────────────────────────────────────────────────────────────


class TestSearchGmail:
    def test_basic_search(self):
        from services import _search_gmail

        svc = MagicMock()
        svc.users().messages().list().execute.return_value = {
            "messages": [{"id": "msg1", "threadId": "thread1"}]
        }
        svc.users().messages().get().execute.return_value = {
            "id": "msg1",
            "threadId": "thread1",
            "internalDate": "1680000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Meeting notes"},
                    {"name": "From", "value": "Alice <alice@example.com>"},
                ]
            },
        }
        # Patch batch fetch to return simple dict
        with patch("services._fetch_gmail_metadata_batch") as mock_batch:
            mock_batch.return_value = {
                "msg1": {
                    "id": "msg1",
                    "threadId": "thread1",
                    "internalDate": "1680000000000",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Meeting notes"},
                            {"name": "From", "value": "Alice <alice@example.com>"},
                        ]
                    },
                }
            }
            results = _search_gmail({"alice@example.com": svc}, "meeting", 10)

        assert len(results) == 1
        assert results[0]["source"] == "gmail"
        assert "Meeting" in results[0]["title"]

    def test_empty_services(self):
        from services import _search_gmail

        assert _search_gmail({}, "query", 10) == []


# ── _search_calendar ─────────────────────────────────────────────────────────


class TestSearchCalendar:
    def test_basic_search(self):
        from services import _search_calendar

        svc = MagicMock()
        svc.calendarList().list().execute.return_value = {"items": [{"id": "primary"}]}
        svc.events().list().execute.return_value = {
            "items": [
                {
                    "id": "evt1",
                    "summary": "Team standup",
                    "description": "Daily standup call",
                    "start": {"dateTime": "2026-04-10T10:00:00"},
                    "end": {"dateTime": "2026-04-10T10:30:00"},
                }
            ]
        }
        results = _search_calendar({"me@example.com": svc}, "standup", 10)
        assert len(results) == 1
        assert results[0]["source"] == "calendar"
        assert "standup" in results[0]["title"].lower()

    def test_empty_services(self):
        from services import _search_calendar

        assert _search_calendar({}, "query", 10) == []


# ── search_all ────────────────────────────────────────────────────────────────


class TestSearchAll:
    def test_empty_query_returns_empty(self):
        from services import search_all

        result = search_all("", ["all"])
        assert result["total"] == 0
        assert result["results"] == []

    def test_whitespace_query_returns_empty(self):
        from services import search_all

        result = search_all("   ", ["all"])
        assert result["total"] == 0

    def test_limit_enforced(self, tmp_path, monkeypatch):
        from services import search_all

        # Mock all source searches to return many results
        big = [
            {
                "source": "notes",
                "id": str(i),
                "title": f"Note {i}",
                "snippet": "test",
                "timestamp": datetime(2026, 1, i % 28 + 1).isoformat(),
                "metadata": {},
            }
            for i in range(100)
        ]

        with (
            patch("services._search_imessage", return_value=[]),
            patch("services._search_gmail", return_value=[]),
            patch("services._search_notes", return_value=big),
            patch("services._search_reminders", return_value=[]),
            patch("services._search_calendar", return_value=[]),
        ):
            result = search_all("test", ["all"], limit=10)

        assert result["total"] == 10
        assert len(result["results"]) == 10

    def test_results_sorted_by_timestamp_desc(self):
        from services import search_all

        older = {
            "source": "notes",
            "id": "1",
            "title": "Old",
            "snippet": "test",
            "timestamp": "2026-01-01T00:00:00",
            "metadata": {},
        }
        newer = {
            "source": "reminders",
            "id": "2",
            "title": "New",
            "snippet": "test",
            "timestamp": "2026-04-01T00:00:00",
            "metadata": {},
        }

        with (
            patch("services._search_imessage", return_value=[older]),
            patch("services._search_gmail", return_value=[newer]),
            patch("services._search_notes", return_value=[]),
            patch("services._search_reminders", return_value=[]),
            patch("services._search_calendar", return_value=[]),
        ):
            result = search_all("test", ["all"], limit=50)

        assert result["results"][0]["id"] == "2"
        assert result["results"][1]["id"] == "1"

    def test_source_filter_only_imessage(self):
        from services import search_all

        with (
            patch(
                "services._search_imessage",
                return_value=[
                    {
                        "source": "imessage",
                        "id": "1",
                        "title": "x",
                        "snippet": "x",
                        "timestamp": "2026-04-01T00:00:00",
                        "metadata": {},
                    }
                ],
            ) as mock_imsg,
            patch("services._search_gmail", return_value=[]) as mock_gmail,
            patch("services._search_notes", return_value=[]) as mock_notes,
            patch("services._search_reminders", return_value=[]) as mock_rem,
            patch("services._search_calendar", return_value=[]) as mock_cal,
        ):
            result = search_all("test", ["imessage"], limit=50)

        mock_imsg.assert_called_once()
        mock_gmail.assert_not_called()
        mock_notes.assert_not_called()
        mock_rem.assert_not_called()
        mock_cal.assert_not_called()
        assert result["total"] == 1

    def test_result_shape(self):
        from services import search_all

        item = {
            "source": "notes",
            "id": "5",
            "title": "My note",
            "snippet": "some text",
            "timestamp": "2026-04-01T12:00:00",
            "metadata": {"folder": "Work"},
        }
        with (
            patch("services._search_imessage", return_value=[]),
            patch("services._search_gmail", return_value=[]),
            patch("services._search_notes", return_value=[item]),
            patch("services._search_reminders", return_value=[]),
            patch("services._search_calendar", return_value=[]),
        ):
            result = search_all("note", ["notes"], limit=10)

        assert result["query"] == "note"
        r = result["results"][0]
        assert r["source"] == "notes"
        assert r["id"] == "5"
        assert r["title"] == "My note"
        assert r["snippet"] == "some text"
        assert r["timestamp"] == "2026-04-01T12:00:00"
        assert r["metadata"] == {"folder": "Work"}
