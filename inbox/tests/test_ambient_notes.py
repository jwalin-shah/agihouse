"""Tests for ambient_notes.py — Obsidian vault file operations."""

from __future__ import annotations

import datetime


class TestAppendToDaily:
    def test_creates_new_daily_note(self, tmp_vault, freezer_date=None):
        import ambient_notes

        ambient_notes.append_to_daily("Test content")
        daily_dir = tmp_vault / "daily"
        files = list(daily_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert f"# {datetime.date.today()}" in content
        assert "Test content" in content

    def test_appends_to_existing_daily_note(self, tmp_vault):
        import ambient_notes

        ambient_notes.append_to_daily("First entry")
        ambient_notes.append_to_daily("Second entry")
        daily_dir = tmp_vault / "daily"
        files = list(daily_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "First entry" in content
        assert "Second entry" in content

    def test_timestamp_in_entry(self, tmp_vault):
        import ambient_notes

        ambient_notes.append_to_daily("Timestamped")
        daily_dir = tmp_vault / "daily"
        content = list(daily_dir.glob("*.md"))[0].read_text()
        # Should have ## HH:MM format
        assert "## " in content

    def test_creates_directories(self, tmp_vault):
        import ambient_notes

        ambient_notes.append_to_daily("test")
        assert (tmp_vault / "daily").is_dir()
        assert (tmp_vault / "ambient").is_dir()


class TestSaveNote:
    def test_saves_with_summary(self, tmp_vault):
        import ambient_notes

        ambient_notes.save_note("raw text here", "This is the summary")
        content = list((tmp_vault / "daily").glob("*.md"))[0].read_text()
        assert "This is the summary" in content
        assert "raw text here" in content

    def test_saves_with_topics(self, tmp_vault):
        import ambient_notes

        ambient_notes.save_note("raw", "summary", topics="python, testing")
        content = list((tmp_vault / "daily").glob("*.md"))[0].read_text()
        assert "#python" in content
        assert "#testing" in content

    def test_saves_transcript_in_callout(self, tmp_vault):
        import ambient_notes

        ambient_notes.save_note("the raw transcript", None)
        content = list((tmp_vault / "daily").glob("*.md"))[0].read_text()
        assert "> [!note]- Transcript" in content
        assert "the raw transcript" in content

    def test_action_items_as_checkboxes(self, tmp_vault):
        import ambient_notes

        ambient_notes.save_note("raw", "fix bug → deploy; test")
        content = list((tmp_vault / "daily").glob("*.md"))[0].read_text()
        assert "- [ ] deploy" in content
        assert "- [ ] test" in content

    def test_no_summary_still_writes(self, tmp_vault):
        import ambient_notes

        ambient_notes.save_note("just a transcript", None)
        files = list((tmp_vault / "daily").glob("*.md"))
        assert len(files) == 1


class TestListDailyNotes:
    def test_empty_vault(self, tmp_vault):
        import ambient_notes

        assert ambient_notes.list_daily_notes() == []

    def test_lists_notes_sorted(self, tmp_vault):
        import ambient_notes

        daily = tmp_vault / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / "2025-01-01.md").write_text("old")
        (daily / "2025-06-15.md").write_text("new")
        (daily / "2025-03-10.md").write_text("mid")

        notes = ambient_notes.list_daily_notes()
        assert len(notes) == 3
        # reverse sorted — newest first
        assert notes[0]["date"] == "2025-06-15"
        assert notes[-1]["date"] == "2025-01-01"

    def test_respects_limit(self, tmp_vault):
        import ambient_notes

        daily = tmp_vault / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (daily / f"2025-01-0{i + 1}.md").write_text(f"note {i}")

        notes = ambient_notes.list_daily_notes(limit=2)
        assert len(notes) == 2

    def test_returns_date_path_size(self, tmp_vault):
        import ambient_notes

        daily = tmp_vault / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / "2025-04-01.md").write_text("hello world")

        notes = ambient_notes.list_daily_notes()
        assert notes[0]["date"] == "2025-04-01"
        assert "path" in notes[0]
        assert notes[0]["size"] > 0


class TestReadDailyNote:
    def test_reads_existing_note(self, tmp_vault):
        import ambient_notes

        daily = tmp_vault / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / "2025-04-01.md").write_text("# April 1\nContent here")

        result = ambient_notes.read_daily_note("2025-04-01")
        assert result == "# April 1\nContent here"

    def test_returns_none_for_missing(self, tmp_vault):
        import ambient_notes

        (tmp_vault / "daily").mkdir(parents=True, exist_ok=True)
        assert ambient_notes.read_daily_note("1999-01-01") is None
