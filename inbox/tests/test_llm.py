"""Tests for LLM services — autocomplete context building and extraction formatting."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services import Extraction, build_context, extract_summary

# ── build_context ───────────────────────────────────────────────────────────


class TestBuildContext:
    def test_empty_messages(self):
        assert build_context([]) == ""

    def test_formats_messages(self):
        msgs = [
            {"sender": "Alice", "body": "Hey!"},
            {"sender": "Bob", "body": "Hi there"},
        ]
        result = build_context(msgs)
        assert "Alice: Hey!" in result
        assert "Bob: Hi there" in result

    def test_respects_max_messages(self):
        msgs = [{"sender": f"User{i}", "body": f"msg{i}"} for i in range(10)]
        result = build_context(msgs, max_messages=3)
        # Should only include last 3
        assert "User7" in result
        assert "User8" in result
        assert "User9" in result
        assert "User0" not in result

    def test_truncates_long_messages(self):
        msgs = [{"sender": "Alice", "body": "x" * 300}]
        result = build_context(msgs)
        assert "..." in result
        assert len(result) < 300

    def test_missing_fields_use_defaults(self):
        msgs = [{}]
        result = build_context(msgs)
        assert "?:" in result


# ── extract_summary ─────────────────────────────────────────────────────────


class TestExtractSummary:
    @patch("services.generate_json")
    def test_returns_key_points(self, mock_gen):
        mock_gen.return_value = Extraction(
            key_points=["point one", "point two"],
            action_items=[],
            topics=[],
        )
        result = extract_summary("some text")
        assert result is not None
        assert "point one" in result
        assert "point two" in result

    @patch("services.generate_json")
    def test_returns_action_items_with_arrow(self, mock_gen):
        mock_gen.return_value = Extraction(
            key_points=[],
            action_items=["deploy", "test"],
            topics=[],
        )
        result = extract_summary("some text")
        assert result is not None
        assert "\u2192" in result  # → arrow
        assert "deploy" in result

    @patch("services.generate_json")
    def test_returns_none_when_empty(self, mock_gen):
        mock_gen.return_value = Extraction(
            key_points=[],
            action_items=[],
            topics=[],
        )
        result = extract_summary("some text")
        assert result is None

    @patch("services.generate_json")
    def test_combines_points_and_actions(self, mock_gen):
        mock_gen.return_value = Extraction(
            key_points=["idea"],
            action_items=["do thing"],
            topics=["topic"],
        )
        result = extract_summary("some text")
        assert result is not None
        assert "idea" in result
        assert "do thing" in result
        assert "|" in result  # separator


# ── autocomplete ─────────────────────────────────────────────────────────────


class TestAutocomplete:
    @patch("services.llm_complete")
    def test_complete_mode_basic(self, mock_complete):
        """Test completing a partial message."""
        from services import autocomplete

        mock_complete.return_value = " sounds good"
        result = autocomplete(
            draft="That ",
            messages=[
                {"sender": "Alice", "body": "Wanna meet tomorrow?"},
            ],
            mode="complete",
        )
        assert result == "sounds good"
        # Verify the complete function was called with the right prompt
        call_args = mock_complete.call_args
        assert call_args is not None
        prompt = call_args[0][0]
        assert "That " in prompt
        assert "Alice: Wanna meet tomorrow?" in prompt

    @patch("services.llm_complete")
    def test_complete_mode_with_temperature(self, mock_complete):
        """Test that temperature is passed to the LLM."""
        from services import autocomplete

        mock_complete.return_value = "completion"
        autocomplete(
            draft="Hello",
            messages=[],
            temperature=0.8,
            mode="complete",
        )
        # Check temperature was passed
        call_args = mock_complete.call_args
        assert call_args[1]["temperature"] == 0.8

    @patch("services.llm_complete")
    def test_reply_mode_suggests_response(self, mock_complete):
        """Test suggesting a reply to the last message."""
        from services import autocomplete

        mock_complete.return_value = "Absolutely!"
        result = autocomplete(
            draft="",
            messages=[
                {"sender": "Alice", "body": "Want to go get coffee?"},
            ],
            mode="reply",
        )
        assert result == "Absolutely!"
        # Verify the prompt mentions the question
        call_args = mock_complete.call_args
        prompt = call_args[0][0]
        assert "Want to go get coffee?" in prompt

    @patch("services.llm_complete")
    def test_reply_mode_uses_context(self, mock_complete):
        """Test that reply mode includes conversation context."""
        from services import autocomplete

        mock_complete.return_value = "Sure!"
        autocomplete(
            messages=[
                {"sender": "Alice", "body": "How have you been?"},
                {"sender": "Bob", "body": "Good, busy with work"},
                {"sender": "Alice", "body": "Any time for coffee?"},
            ],
            mode="reply",
        )
        call_args = mock_complete.call_args
        prompt = call_args[0][0]
        # Should include previous context but exclude the last message from history
        assert "How have you been?" in prompt
        assert "Good, busy with work" in prompt
        # The last message (which we're replying to) should appear in "received" section
        assert "Any time for coffee?" in prompt

    def test_complete_mode_returns_none_for_short_draft(self):
        """Test that short drafts return None."""
        from services import autocomplete

        result = autocomplete(draft="Hi", messages=[], mode="complete")
        assert result is None

    def test_reply_mode_returns_none_without_messages(self):
        """Test that reply mode returns None if no messages."""
        from services import autocomplete

        result = autocomplete(messages=[], mode="reply")
        assert result is None

    def test_reply_mode_returns_none_with_empty_last_message(self):
        """Test that reply mode returns None if last message is empty."""
        from services import autocomplete

        result = autocomplete(
            messages=[{"sender": "Alice", "body": ""}],
            mode="reply",
        )
        assert result is None

    @patch("services.llm_complete")
    def test_strips_whitespace_from_result(self, mock_complete):
        """Test that results are trimmed."""
        from services import autocomplete

        mock_complete.return_value = "  \n  result  \n  "
        result = autocomplete(draft="Hey", mode="complete")
        assert result == "result"

    @patch("services.llm_complete")
    def test_returns_none_for_empty_completion(self, mock_complete):
        """Test that empty completions return None."""
        from services import autocomplete

        mock_complete.return_value = ""
        result = autocomplete(draft="Hi there", mode="complete")
        assert result is None

    def test_invalid_mode_raises_error(self):
        """Test that invalid mode raises ValueError."""
        from services import autocomplete

        with patch("services.llm_complete"), pytest.raises(ValueError, match="Invalid mode"):
            autocomplete(draft="Hi", mode="invalid")
