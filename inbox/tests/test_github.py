"""Tests for GitHub connector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from services import (
    _github_headers,
    github_mark_all_read,
    github_mark_read,
    github_notifications,
    github_pulls,
)

SAMPLE_NOTIFICATION = {
    "id": "123",
    "subject": {
        "title": "Fix auth bug",
        "type": "PullRequest",
        "url": "https://api.github.com/repos/owner/repo/pulls/42",
    },
    "repository": {"full_name": "owner/repo"},
    "reason": "review_requested",
    "unread": True,
    "updated_at": "2026-04-09T10:00:00Z",
}

SAMPLE_SEARCH_ITEM = {
    "id": 456,
    "number": 42,
    "title": "Fix auth bug",
    "repository_url": "https://api.github.com/repos/owner/repo",
    "user": {"login": "alice"},
    "state": "open",
    "html_url": "https://github.com/owner/repo/pull/42",
    "updated_at": "2026-04-09T10:00:00Z",
}


class TestGitHubHeaders:
    def test_returns_headers_with_token(self):
        with patch("services._github_token", return_value="ghp_test"):
            headers = _github_headers()
        assert headers["Authorization"] == "Bearer ghp_test"
        assert "X-GitHub-Api-Version" in headers

    def test_returns_empty_without_token(self):
        with patch("services._github_token", return_value=None):
            assert _github_headers() == {}


class TestGitHubNotifications:
    def test_parses_notifications(self, mock_github_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [SAMPLE_NOTIFICATION]
        mock_resp.raise_for_status = MagicMock()

        with patch("services.httpx.get", return_value=mock_resp):
            notifs = github_notifications()

        assert len(notifs) == 1
        n = notifs[0]
        assert n.id == "123"
        assert n.title == "Fix auth bug"
        assert n.repo == "owner/repo"
        assert n.type == "PullRequest"
        assert n.reason == "review_requested"
        assert n.unread is True
        assert "github.com" in n.url
        assert "/pull/42" in n.url

    def test_returns_empty_without_token(self):
        with patch("services._github_token", return_value=None):
            assert github_notifications() == []

    def test_handles_api_error(self, mock_github_token):
        with patch("services.httpx.get", side_effect=httpx.HTTPError("fail")):
            assert github_notifications() == []


class TestGitHubMarkRead:
    def test_marks_single_read(self, mock_github_token):
        mock_resp = MagicMock(status_code=205)
        with patch("services.httpx.patch", return_value=mock_resp):
            assert github_mark_read("123") is True

    def test_returns_false_without_token(self):
        with patch("services._github_token", return_value=None):
            assert github_mark_read("123") is False


class TestGitHubMarkAllRead:
    def test_marks_all_read(self, mock_github_token):
        mock_resp = MagicMock(status_code=202)
        with patch("services.httpx.put", return_value=mock_resp):
            assert github_mark_all_read() is True


class TestGitHubPulls:
    def test_parses_search_results(self, mock_github_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [SAMPLE_SEARCH_ITEM]}
        mock_resp.raise_for_status = MagicMock()

        with patch("services.httpx.get", return_value=mock_resp):
            pulls = github_pulls()

        assert len(pulls) == 1
        p = pulls[0]
        assert p["number"] == 42
        assert p["title"] == "Fix auth bug"
        assert p["repo"] == "owner/repo"

    def test_filters_by_repo(self, mock_github_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("services.httpx.get", return_value=mock_resp) as mock_get:
            github_pulls(repo="owner/repo")

        call_params = mock_get.call_args[1]["params"]
        assert "repo:owner/repo" in call_params["q"]
