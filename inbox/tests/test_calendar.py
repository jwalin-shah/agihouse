"""Tests for the calendar upgrade features — date range API, attendees, TUI helpers."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Server endpoint tests ───────────────────────────────────────────────────


@pytest.fixture()
def server_client():
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


class TestCalendarDateRangeEndpoint:
    def test_single_date_param(self, server_client):
        c, state = server_client
        from services import CalendarEvent

        mock_events = [
            CalendarEvent(
                summary="Standup",
                start=datetime(2026, 4, 10, 9, 0),
                end=datetime(2026, 4, 10, 9, 30),
                event_id="e1",
                calendar_id="primary",
            )
        ]
        with patch("inbox_server.calendar_events", return_value=mock_events):
            resp = c.get("/calendar/events", params={"date": "2026-04-10"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["summary"] == "Standup"

    def test_date_range_params(self, server_client):
        c, state = server_client
        from services import CalendarEvent

        mock_events = [
            CalendarEvent(
                summary="Mon meeting",
                start=datetime(2026, 4, 6, 10, 0),
                end=datetime(2026, 4, 6, 11, 0),
                event_id="e1",
                calendar_id="primary",
            ),
            CalendarEvent(
                summary="Wed lunch",
                start=datetime(2026, 4, 8, 12, 0),
                end=datetime(2026, 4, 8, 13, 0),
                event_id="e2",
                calendar_id="primary",
            ),
        ]
        with patch("inbox_server.calendar_events", return_value=mock_events) as mock_fn:
            resp = c.get(
                "/calendar/events",
                params={"start": "2026-04-06", "end": "2026-04-12"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Verify start_date and end_date kwargs were passed
        call_kwargs = mock_fn.call_args[1]
        assert call_kwargs["start_date"] == datetime(2026, 4, 6)
        assert call_kwargs["end_date"] == datetime(2026, 4, 12)

    def test_no_params_defaults_to_today(self, server_client):
        c, state = server_client
        with patch("inbox_server.calendar_events", return_value=[]) as mock_fn:
            resp = c.get("/calendar/events")
        assert resp.status_code == 200
        # When no params, date=None is passed (not start_date/end_date)
        args, kwargs = mock_fn.call_args
        assert args[1] is None  # date param is None

    def test_attendees_returned(self, server_client):
        c, state = server_client
        from services import CalendarEvent

        mock_events = [
            CalendarEvent(
                summary="Team sync",
                start=datetime(2026, 4, 10, 14, 0),
                end=datetime(2026, 4, 10, 15, 0),
                event_id="e1",
                calendar_id="primary",
                attendees=[
                    {
                        "name": "Alice",
                        "email": "alice@example.com",
                        "responseStatus": "accepted",
                    },
                    {
                        "name": "Bob",
                        "email": "bob@example.com",
                        "responseStatus": "tentative",
                    },
                ],
            )
        ]
        with patch("inbox_server.calendar_events", return_value=mock_events):
            resp = c.get("/calendar/events", params={"date": "2026-04-10"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data[0]["attendees"]) == 2
        assert data[0]["attendees"][0]["name"] == "Alice"
        assert data[0]["attendees"][1]["responseStatus"] == "tentative"


# ── Client tests ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_client():
    """InboxClient with a mocked httpx.Client underneath."""
    from inbox_client import InboxClient

    c = InboxClient.__new__(InboxClient)
    c._client = MagicMock()
    return c


def _mock_response(data, status_code=200):
    import httpx

    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


class TestClientCalendarRange:
    def test_calendar_events_with_date(self, mock_client):
        mock_client._client.get.return_value = _mock_response([{"summary": "Standup"}])
        result = mock_client.calendar_events(date="2026-04-10")
        assert result[0]["summary"] == "Standup"
        call_args = mock_client._client.get.call_args
        assert call_args[1]["params"]["date"] == "2026-04-10"

    def test_calendar_events_with_range(self, mock_client):
        mock_client._client.get.return_value = _mock_response([{"summary": "Event"}])
        result = mock_client.calendar_events(start_date="2026-04-06", end_date="2026-04-12")
        assert result[0]["summary"] == "Event"
        call_args = mock_client._client.get.call_args
        params = call_args[1]["params"]
        assert params["start"] == "2026-04-06"
        assert params["end"] == "2026-04-12"
        assert "date" not in params

    def test_calendar_events_range_convenience(self, mock_client):
        mock_client._client.get.return_value = _mock_response([])
        mock_client.calendar_events_range("2026-04-06", "2026-04-12")
        call_args = mock_client._client.get.call_args
        params = call_args[1]["params"]
        assert params["start"] == "2026-04-06"
        assert params["end"] == "2026-04-12"

    def test_calendar_events_no_params(self, mock_client):
        mock_client._client.get.return_value = _mock_response([])
        mock_client.calendar_events()
        call_args = mock_client._client.get.call_args
        params = call_args[1]["params"]
        assert params == {}


# ── TUI helper tests (no UI needed) ────────────────────────────────────────


class TestParseUserDate:
    """Test the InboxApp._parse_user_date static-like method."""

    def _make_app(self):
        """Minimal InboxApp instance for calling parse methods."""
        from inbox import InboxApp

        app = InboxApp.__new__(InboxApp)
        return app

    def test_iso_format(self):
        app = self._make_app()
        assert app._parse_user_date("2026-05-01") == date(2026, 5, 1)

    def test_month_day_long(self):
        app = self._make_app()
        result = app._parse_user_date("May 1")
        assert result is not None
        assert result.month == 5
        assert result.day == 1

    def test_month_day_short(self):
        app = self._make_app()
        result = app._parse_user_date("Apr 15")
        assert result is not None
        assert result.month == 4
        assert result.day == 15

    def test_month_day_year(self):
        app = self._make_app()
        result = app._parse_user_date("May 1, 2027")
        assert result == date(2027, 5, 1)

    def test_slash_format(self):
        app = self._make_app()
        result = app._parse_user_date("12/25/2026")
        assert result == date(2026, 12, 25)

    def test_invalid_returns_none(self):
        app = self._make_app()
        assert app._parse_user_date("not a date") is None
        assert app._parse_user_date("") is None

    def test_whitespace_stripped(self):
        app = self._make_app()
        assert app._parse_user_date("  2026-05-01  ") == date(2026, 5, 1)


class TestCalendarDateLabel:
    def _make_app(self):
        from inbox import InboxApp

        app = InboxApp.__new__(InboxApp)
        app._calendar_date = date(2026, 4, 14)
        return app

    def test_non_today_format(self):
        app = self._make_app()
        app._calendar_date = date(2026, 4, 13)
        label = app._calendar_date_label()
        assert "Mon" in label
        assert "Apr 13" in label
        assert "Today" not in label

    def test_today_format(self):
        app = self._make_app()
        app._calendar_date = date.today()
        label = app._calendar_date_label()
        assert "Today" in label


class TestCalendarViewMode:
    def _make_app(self):
        from inbox import InboxApp

        app = InboxApp.__new__(InboxApp)
        app._calendar_view_mode = "day"
        app._calendar_date = date(2026, 4, 10)
        return app

    def test_fetch_calendar_day_view(self):
        app = self._make_app()
        app._calendar_view_mode = "day"
        app.client = MagicMock()
        app.client.calendar_events.return_value = []
        result = app._fetch_calendar_for_view()
        app.client.calendar_events.assert_called_once_with(date="2026-04-10")
        assert result == []

    def test_fetch_calendar_week_view(self):
        app = self._make_app()
        app._calendar_view_mode = "week"
        # April 10, 2026 is Friday; Monday is April 6
        app._calendar_date = date(2026, 4, 10)
        app.client = MagicMock()
        app.client.calendar_events_range.return_value = []
        result = app._fetch_calendar_for_view()
        app.client.calendar_events_range.assert_called_once_with("2026-04-06", "2026-04-12")
        assert result == []

    def test_fetch_calendar_agenda_view(self):
        app = self._make_app()
        app._calendar_view_mode = "agenda"
        app.client = MagicMock()
        app.client.calendar_events_range.return_value = []
        result = app._fetch_calendar_for_view()
        app.client.calendar_events_range.assert_called_once_with("2026-04-10", "2026-04-23")
        assert result == []


# ── Services layer tests ───────────────────────────────────────────────────


class TestCalendarEventsDateRange:
    def test_single_date(self):
        """calendar_events with just a date queries one day."""
        from services import calendar_events

        mock_svc = MagicMock()
        mock_svc.calendarList().list().execute.return_value = {"items": [{"id": "primary"}]}
        mock_svc.events().list().execute.return_value = {"items": []}

        result = calendar_events({"test@example.com": mock_svc}, date=datetime(2026, 4, 10))
        assert result == []

    def test_date_range(self):
        """calendar_events with start_date and end_date queries the range."""
        from services import calendar_events

        mock_svc = MagicMock()
        mock_svc.calendarList().list().execute.return_value = {"items": [{"id": "primary"}]}
        mock_svc.events().list().execute.return_value = {
            "items": [
                {
                    "summary": "Multi-day meeting",
                    "start": {"dateTime": "2026-04-07T10:00:00-07:00"},
                    "end": {"dateTime": "2026-04-07T11:00:00-07:00"},
                    "id": "e1",
                }
            ]
        }

        result = calendar_events(
            {"test@example.com": mock_svc},
            start_date=datetime(2026, 4, 6),
            end_date=datetime(2026, 4, 12),
        )
        assert len(result) == 1
        assert result[0].summary == "Multi-day meeting"

    def test_attendees_extracted(self):
        """calendar_events extracts attendee data from API response."""
        from services import calendar_events

        mock_svc = MagicMock()
        mock_svc.calendarList().list().execute.return_value = {"items": [{"id": "primary"}]}
        mock_svc.events().list().execute.return_value = {
            "items": [
                {
                    "summary": "Sync",
                    "start": {"dateTime": "2026-04-10T14:00:00-07:00"},
                    "end": {"dateTime": "2026-04-10T15:00:00-07:00"},
                    "id": "e1",
                    "attendees": [
                        {
                            "displayName": "Alice",
                            "email": "alice@co.com",
                            "responseStatus": "accepted",
                        },
                        {
                            "email": "bob@co.com",
                            "responseStatus": "needsAction",
                        },
                    ],
                }
            ]
        }

        result = calendar_events(
            {"test@example.com": mock_svc},
            date=datetime(2026, 4, 10),
        )
        assert len(result) == 1
        assert len(result[0].attendees) == 2
        assert result[0].attendees[0]["name"] == "Alice"
        assert result[0].attendees[0]["email"] == "alice@co.com"
        assert result[0].attendees[0]["responseStatus"] == "accepted"
        # Second attendee has no displayName
        assert result[0].attendees[1]["name"] == ""
        assert result[0].attendees[1]["email"] == "bob@co.com"

    def test_no_attendees_returns_empty_list(self):
        """Events without attendees get an empty attendees list."""
        from services import calendar_events

        mock_svc = MagicMock()
        mock_svc.calendarList().list().execute.return_value = {"items": [{"id": "primary"}]}
        mock_svc.events().list().execute.return_value = {
            "items": [
                {
                    "summary": "Solo event",
                    "start": {"dateTime": "2026-04-10T14:00:00-07:00"},
                    "end": {"dateTime": "2026-04-10T15:00:00-07:00"},
                    "id": "e1",
                }
            ]
        }

        result = calendar_events(
            {"test@example.com": mock_svc},
            date=datetime(2026, 4, 10),
        )
        assert result[0].attendees == []
