"""Tests for Apple Reminders connector."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import services
from services import (
    reminder_by_id,
    reminder_complete,
    reminder_create,
    reminders_list,
    reminders_lists,
)


class TestRemindersLists:
    def test_returns_lists_with_counts(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            lists = reminders_lists()
        names = {item["name"] for item in lists}
        assert "Daily" in names
        assert "Work" in names
        # Daily has 1 incomplete, Work has 1 incomplete
        daily = next(item for item in lists if item["name"] == "Daily")
        assert daily["incomplete_count"] == 1  # "Done task" is completed

    def test_empty_dir_returns_empty(self, tmp_path):
        with patch("services.REMINDERS_DIR", tmp_path / "nonexistent"):
            assert reminders_lists() == []


class TestRemindersList:
    def test_lists_incomplete_by_default(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            items = reminders_list()
        titles = [r.title for r in items]
        assert "Buy groceries" in titles
        assert "Ship feature" in titles
        assert "Done task" not in titles

    def test_show_completed(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            items = reminders_list(show_completed=True)
        titles = [r.title for r in items]
        assert "Done task" in titles

    def test_filter_by_list(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            items = reminders_list(list_name="Work")
        assert len(items) == 1
        assert items[0].title == "Ship feature"
        assert items[0].flagged is True
        assert items[0].priority == 1

    def test_due_dates_parsed(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            items = reminders_list()
        for r in items:
            assert r.due_date is not None
            assert r.creation_date is not None

    def test_limit(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            items = reminders_list(limit=1)
        assert len(items) == 1

    def test_ordered_by_due_date(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            items = reminders_list()
        # "Ship feature" is due sooner (3h) than "Buy groceries" (1d)
        assert items[0].title == "Ship feature"
        assert items[1].title == "Buy groceries"


class TestReminderComplete:
    def test_calls_osascript(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_complete("Buy groceries")
        assert result is True
        args = mock_run.call_args
        assert args[0][0][0] == "osascript"
        assert "Buy groceries" in args[0][0][2]

    def test_returns_false_on_failure(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="fail")
            result = reminder_complete("Nonexistent")
        assert result is False

    def test_escapes_quotes_in_title(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            reminder_complete('Buy "fancy" cheese')
        script = mock_run.call_args[0][0][2]
        assert services._escape_applescript('Buy "fancy" cheese') in script


class TestReminderCreate:
    def test_creates_via_osascript(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_create("New task", list_name="Daily")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "New task" in script
        assert "Daily" in script

    def test_with_notes(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            reminder_create("Task", notes="Some details")
        script = mock_run.call_args[0][0][2]
        assert "Some details" in script


class TestReminderEdit:
    def test_edits_title_via_osascript(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_edit("Buy groceries", title="Buy organic groceries")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "Buy organic groceries" in script
        assert "Buy groceries" in script
        # Verify _escape_applescript is used for title
        assert services._escape_applescript("Buy organic groceries") in script

    def test_edits_due_date_via_osascript(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_edit("Buy groceries", due_date="4/15/2026")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "due date" in script.lower()

    def test_edits_notes_via_osascript(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_edit("Buy groceries", notes="Get almond milk")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "body" in script.lower() or "note" in script.lower()
        assert services._escape_applescript("Get almond milk") in script

    def test_returns_false_on_failure(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="fail")
            result = reminder_edit("Nonexistent", title="New title")
        assert result is False

    def test_escapes_special_chars_in_title(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            reminder_edit("Old task", title='Buy "fancy" stuff\\here')
        script = mock_run.call_args[0][0][2]
        assert services._escape_applescript('Buy "fancy" stuff\\here') in script

    def test_no_fields_to_edit_still_runs(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            # Editing with only the identifier, no new fields — should still find the reminder
            result = reminder_edit("Buy groceries")
        assert result is True


class TestReminderDelete:
    def test_deletes_via_osascript(self):
        from services import reminder_delete

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_delete("Buy groceries")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "delete" in script.lower()
        assert "Buy groceries" in script

    def test_returns_false_on_failure(self):
        from services import reminder_delete

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="fail")
            result = reminder_delete("Nonexistent")
        assert result is False

    def test_escapes_special_chars_in_title(self):
        from services import reminder_delete

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            reminder_delete('Buy "important" stuff')
        script = mock_run.call_args[0][0][2]
        assert services._escape_applescript('Buy "important" stuff') in script


class TestReminderById:
    def test_finds_reminder_by_id(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            reminder = reminder_by_id("1")
        assert reminder is not None
        assert reminder.id == "1"
        assert reminder.title == "Buy groceries"
        assert reminder.list_name == "Daily"
        assert reminder.completed is False

    def test_finds_reminder_in_work_list(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            reminder = reminder_by_id("3")
        assert reminder is not None
        assert reminder.id == "3"
        assert reminder.title == "Ship feature"
        assert reminder.list_name == "Work"
        assert reminder.flagged is True

    def test_returns_none_for_nonexistent_id(self, tmp_reminders_db):
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            reminder = reminder_by_id("999")
        assert reminder is None

    def test_returns_none_for_empty_dir(self, tmp_path):
        with patch("services.REMINDERS_DIR", tmp_path / "nonexistent"):
            reminder = reminder_by_id("1")
        assert reminder is None

    def test_no_limit_needed(self, tmp_reminders_db):
        """reminder_by_id queries SQLite directly, no limit cap."""
        with patch("services.REMINDERS_DIR", tmp_reminders_db):
            # This would fail with the old limit=500 approach for ID 3
            # if there were 500+ reminders, but direct lookup always works
            reminder = reminder_by_id("3")
        assert reminder is not None
        assert reminder.title == "Ship feature"


class TestApplescriptFindReminder:
    def test_without_list_name(self):
        clause = services._applescript_find_reminder("Buy groceries")
        assert "Buy groceries" in clause
        assert "container" not in clause
        assert "completed is false" in clause

    def test_with_list_name(self):
        clause = services._applescript_find_reminder("Buy groceries", list_name="Daily")
        assert "Buy groceries" in clause
        assert "Daily" in clause
        assert "container" in clause
        assert "completed is false" in clause

    def test_escapes_title(self):
        clause = services._applescript_find_reminder('Buy "fancy" stuff')
        assert services._escape_applescript('Buy "fancy" stuff') in clause

    def test_escapes_list_name(self):
        clause = services._applescript_find_reminder("Task", list_name='My "Special" List')
        assert services._escape_applescript('My "Special" List') in clause


class TestReminderCompleteWithListName:
    def test_passes_list_name_to_applescript(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_complete("Buy groceries", list_name="Daily")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "Daily" in script
        assert "container" in script

    def test_without_list_name_uses_title_only(self):
        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_complete("Buy groceries")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "container" not in script


class TestReminderEditWithListName:
    def test_passes_list_name_to_applescript(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_edit("Buy groceries", title="Buy organic", list_name="Daily")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "Daily" in script
        assert "container" in script
        assert "Buy organic" in script

    def test_without_list_name_uses_title_only(self):
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_edit("Buy groceries", title="Buy organic")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "container" not in script


class TestReminderDeleteWithListName:
    def test_passes_list_name_to_applescript(self):
        from services import reminder_delete

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_delete("Buy groceries", list_name="Daily")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "Daily" in script
        assert "container" in script

    def test_without_list_name_uses_title_only(self):
        from services import reminder_delete

        with patch("services.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_delete("Buy groceries")
        assert result is True
        script = mock_run.call_args[0][0][2]
        assert "container" not in script


class TestApplescriptRetryLogic:
    """Test retry logic for AppleScript reminder mutations."""

    def test_reminder_complete_succeeds_first_try(self):
        """reminder_complete returns True on first successful attempt."""
        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.return_value = MagicMock(returncode=0, stdout="ok")
            result = reminder_complete("Buy groceries")
        assert result is True
        assert mock_run.call_count == 1

    def test_reminder_complete_retries_on_failure(self):
        """reminder_complete retries up to APPLESCRIPT_RETRIES times on failure."""
        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            # First attempt fails, second succeeds
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="fail"),
                MagicMock(returncode=0, stdout="ok"),
            ]
            result = reminder_complete("Buy groceries")
        assert result is True
        assert mock_run.call_count == 2

    def test_reminder_complete_returns_false_after_all_retries(self):
        """reminder_complete returns False after all retries exhausted."""
        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.return_value = MagicMock(returncode=1, stdout="fail")
            result = reminder_complete("Nonexistent")
        assert result is False
        # Should be called APPLESCRIPT_RETRIES + 1 times
        assert mock_run.call_count == services.APPLESCRIPT_RETRIES + 1

    def test_reminder_create_retries_on_failure(self):
        """reminder_create retries on first failure."""
        from services import reminder_create

        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="fail"),
                MagicMock(returncode=0, stdout="ok"),
            ]
            result = reminder_create("New task")
        assert result is True
        assert mock_run.call_count == 2

    def test_reminder_edit_retries_on_failure(self):
        """reminder_edit retries on first failure."""
        from services import reminder_edit

        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="fail"),
                MagicMock(returncode=0, stdout="ok"),
            ]
            result = reminder_edit("Buy groceries", title="Buy organic")
        assert result is True
        assert mock_run.call_count == 2

    def test_reminder_delete_retries_on_failure(self):
        """reminder_delete retries on first failure."""
        from services import reminder_delete

        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="fail"),
                MagicMock(returncode=0, stdout="ok"),
            ]
            result = reminder_delete("Buy groceries")
        assert result is True
        assert mock_run.call_count == 2

    def test_applescript_retry_delays_between_attempts(self):
        """Retry logic sleeps between attempts."""
        with (
            patch("services.subprocess.run") as mock_run,
            patch("services.time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="fail"),
                MagicMock(returncode=0, stdout="ok"),
            ]
            result = reminder_complete("Buy groceries")
        assert result is True
        mock_sleep.assert_called_once_with(services.APPLESCRIPT_RETRY_DELAY)

    def test_applescript_retry_on_exception(self):
        """Retry logic handles exceptions (e.g., timeout) and retries."""
        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.side_effect = [
                subprocess.TimeoutExpired("osascript", 10),
                MagicMock(returncode=0, stdout="ok"),
            ]
            result = reminder_complete("Buy groceries")
        assert result is True
        assert mock_run.call_count == 2

    def test_applescript_retry_exhausted_on_exceptions(self):
        """Retry logic returns False after all retries on repeated exceptions."""
        with patch("services.subprocess.run") as mock_run, patch("services.time.sleep"):
            mock_run.side_effect = subprocess.TimeoutExpired("osascript", 10)
            result = reminder_complete("Buy groceries")
        assert result is False
        assert mock_run.call_count == services.APPLESCRIPT_RETRIES + 1
