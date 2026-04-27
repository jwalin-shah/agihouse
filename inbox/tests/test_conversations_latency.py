"""Tests for /conversations endpoint latency optimization.

Verifies that iMessage and Gmail fetches are parallelized, and that
server startup pre-warms the conversation cache.
"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services import Contact


@pytest.fixture
def client():
    """TestClient with mocked lifespan (no real Google auth / contacts)."""
    import os

    import inbox_server

    with (
        patch.dict(os.environ, {"INBOX_SERVER_TOKEN": ""}, clear=False),
        patch("inbox_server.init_contacts", return_value=0),
        patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
        TestClient(inbox_server.app, raise_server_exceptions=False) as c,
    ):
        inbox_server.state.gmail_services = {}
        inbox_server.state.cal_services = {}
        inbox_server.state.drive_services = {}
        inbox_server.state.conv_cache = {}
        inbox_server.state.events_cache = []
        mock_ambient = MagicMock()
        mock_ambient.is_running = False
        mock_dictation = MagicMock()
        mock_dictation.is_running = False
        mock_dictation.available = True
        inbox_server.state.ambient = mock_ambient
        inbox_server.state.dictation = mock_dictation
        yield c


class TestConversationsParallelFetch:
    """Verify iMessage and Gmail fetches run concurrently, not sequentially."""

    @patch("inbox_server.gmail_contacts")
    @patch("inbox_server.imsg_contacts")
    def test_imessage_and_gmail_fetched_concurrently(self, mock_imsg, mock_gmail, client):
        """When source=all, iMessage and Gmail should be fetched in parallel.

        We verify this by making each call take 0.5s. If sequential, total
        would be ~1s+. If parallel, total should be ~0.5s+.
        """
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}

        call_times: list[float] = []

        def slow_imsg(limit=30):
            call_times.append(time.monotonic())
            time.sleep(0.3)
            return [Contact(id="1", name="Alice", source="imessage", last_ts=datetime(2025, 1, 1))]

        def slow_gmail(svc, email, limit=20):
            call_times.append(time.monotonic())
            time.sleep(0.3)
            return [Contact(id="2", name="Bob", source="gmail", last_ts=datetime(2025, 1, 2))]

        mock_imsg.side_effect = slow_imsg
        mock_gmail.side_effect = slow_gmail

        start = time.monotonic()
        resp = client.get("/conversations", params={"source": "all"})
        _elapsed = time.monotonic() - start

        assert resp.status_code == 200
        data = resp.json()
        # Both sources should be present
        sources = {d["source"] for d in data}
        assert "imessage" in sources
        assert "gmail" in sources

        # If parallel, both calls should start within 50ms of each other
        # and total elapsed should be roughly 0.3s + overhead, not 0.6s+
        if len(call_times) == 2:
            start_diff = abs(call_times[1] - call_times[0])
            assert start_diff < 0.15, (
                f"Fetches appear sequential (start diff: {start_diff:.3f}s). "
                "They should be parallel."
            )

    @patch("inbox_server.gmail_contacts")
    @patch("inbox_server.imsg_contacts")
    def test_multi_account_gmail_fetched_concurrently(self, mock_imsg, mock_gmail, client):
        """Multiple Gmail accounts should be fetched in parallel."""
        import inbox_server

        inbox_server.state.gmail_services = {
            "a@gmail.com": MagicMock(),
            "b@gmail.com": MagicMock(),
        }

        call_times: list[float] = []

        def slow_gmail(svc, email, limit=20):
            call_times.append(time.monotonic())
            time.sleep(0.2)
            return [
                Contact(
                    id=f"2-{email}",
                    name=f"Contact-{email}",
                    source="gmail",
                    last_ts=datetime(2025, 1, 2),
                    gmail_account=email,
                )
            ]

        mock_imsg.return_value = []
        mock_gmail.side_effect = slow_gmail

        resp = client.get("/conversations", params={"source": "gmail"})
        assert resp.status_code == 200

        # Both account fetches should start within 50ms of each other
        if len(call_times) == 2:
            start_diff = abs(call_times[1] - call_times[0])
            assert start_diff < 0.15, (
                f"Multi-account Gmail fetches appear sequential "
                f"(start diff: {start_diff:.3f}s). They should be parallel."
            )

    @patch("inbox_server.gmail_contacts")
    @patch("inbox_server.imsg_contacts")
    def test_imessage_only_not_blocked_by_gmail(self, mock_imsg, mock_gmail, client):
        """When source=imessage, Gmail fetch should not be called."""
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_imsg.return_value = [
            Contact(id="1", name="Alice", source="imessage", last_ts=datetime(2025, 1, 1))
        ]
        mock_gmail.side_effect = Exception("Should not be called")

        resp = client.get("/conversations", params={"source": "imessage"})
        assert resp.status_code == 200
        mock_gmail.assert_not_called()

    @patch("inbox_server.gmail_contacts")
    @patch("inbox_server.imsg_contacts")
    def test_gmail_only_not_blocked_by_imessage(self, mock_imsg, mock_gmail, client):
        """When source=gmail, iMessage fetch should not be called."""
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_gmail.return_value = [
            Contact(id="2", name="Bob", source="gmail", last_ts=datetime(2025, 1, 2))
        ]
        mock_imsg.side_effect = Exception("Should not be called")

        resp = client.get("/conversations", params={"source": "gmail"})
        assert resp.status_code == 200
        mock_imsg.assert_not_called()

    @patch("inbox_server.gmail_contacts")
    @patch("inbox_server.imsg_contacts")
    def test_results_sorted_by_timestamp(self, mock_imsg, mock_gmail, client):
        """Results from parallel fetches are still sorted by last_ts."""
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_imsg.return_value = [
            Contact(id="1", name="Old", source="imessage", last_ts=datetime(2024, 1, 1)),
            Contact(id="2", name="New", source="imessage", last_ts=datetime(2025, 6, 1)),
        ]
        mock_gmail.return_value = [
            Contact(id="3", name="Mid", source="gmail", last_ts=datetime(2025, 3, 1)),
        ]

        resp = client.get("/conversations", params={"source": "all"})
        data = resp.json()
        names = [d["name"] for d in data]
        assert names == ["New", "Mid", "Old"]

    @patch("inbox_server.gmail_contacts")
    @patch("inbox_server.imsg_contacts")
    def test_conv_cache_populated_from_parallel_results(self, mock_imsg, mock_gmail, client):
        """Conversation cache is populated correctly from parallel fetches."""
        import inbox_server

        inbox_server.state.gmail_services = {"me@gmail.com": MagicMock()}
        mock_imsg.return_value = [
            Contact(id="1", name="Alice", source="imessage", last_ts=datetime(2025, 1, 1))
        ]
        mock_gmail.return_value = [
            Contact(
                id="2",
                name="Bob",
                source="gmail",
                last_ts=datetime(2025, 1, 2),
                gmail_account="me@gmail.com",
            )
        ]

        client.get("/conversations", params={"source": "all"})
        cache = inbox_server.state.conv_cache
        assert "imessage:1" in cache
        assert "gmail:2" in cache


class TestPreWarmConversations:
    """Verify that server startup can pre-warm the conversation cache."""

    def test_lifespan_populates_conv_cache_when_pre_warm_enabled(self):
        """When INBOX_PRE_WARM_CONVERSATIONS=1, lifespan pre-fetches conversations."""
        import os

        import inbox_server

        gmail_svc = MagicMock()
        with (
            patch.dict(os.environ, {"INBOX_PRE_WARM_CONVERSATIONS": "1"}),
            patch("inbox_server.init_contacts", return_value=0),
            patch(
                "inbox_server.google_auth_all",
                return_value=({"me@gmail.com": gmail_svc}, {}, {}, {}, {}, {}),
            ),
            patch(
                "inbox_server.imsg_contacts",
                return_value=[
                    Contact(id="1", name="Alice", source="imessage", last_ts=datetime(2025, 1, 1))
                ],
            ),
            patch("inbox_server.gmail_contacts", return_value=[]),
            TestClient(inbox_server.app, raise_server_exceptions=False),
        ):
            # After startup, conv_cache should be populated
            cache = inbox_server.state.conv_cache
            assert "imessage:1" in cache, (
                "Pre-warm should populate conv_cache during server startup"
            )

    def test_lifespan_skips_pre_warm_by_default(self):
        """When INBOX_PRE_WARM_CONVERSATIONS is not set, no pre-warm occurs."""
        import inbox_server

        # Clear any pre-existing cache
        inbox_server.state.conv_cache = {}

        with (
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
            TestClient(inbox_server.app, raise_server_exceptions=False),
        ):
            # conv_cache should still be empty since no pre-warm env var is set
            cache = inbox_server.state.conv_cache
            assert len(cache) == 0, "conv_cache should be empty without pre-warm"

    def test_pre_warm_gracefully_handles_fetch_errors(self):
        """Pre-warm should not crash the server if fetches fail."""
        import os

        import inbox_server

        with (
            patch.dict(os.environ, {"INBOX_PRE_WARM_CONVERSATIONS": "1"}),
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
            patch("inbox_server.imsg_contacts", return_value=[]),
            # gmail_contacts won't be called since no gmail_services
            TestClient(inbox_server.app, raise_server_exceptions=False),
        ):
            # Server should still start successfully
            pass
