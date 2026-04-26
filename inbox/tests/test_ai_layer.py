"""Tests for AI layer features: briefing, triage, summarization, action extraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── Feature 1: LLM Infrastructure ───────────────────────────────────────────


class TestLLMInfrastructure:
    def test_mlx_large_model_env_default(self):
        import services

        assert services.MLX_LARGE_MODEL != ""
        # Should reference a 3-4B MLX model by default
        assert (
            "mlx" in services.MLX_LARGE_MODEL.lower() or "qwen" in services.MLX_LARGE_MODEL.lower()
        )

    def test_llm_large_is_loaded_false_initially(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "_llm_large_model", None)
        assert services.llm_large_is_loaded() is False

    def test_llm_large_is_loaded_true_when_set(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "_llm_large_model", MagicMock())
        assert services.llm_large_is_loaded() is True

    def test_llm_large_is_loading_false_initially(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "_llm_large_loading", False)
        assert services.llm_large_is_loading() is False

    def test_ensure_large_llm_returns_false_when_mlx_unavailable(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "_llm_large_model", None)
        import sys

        # mlx_lm is already mocked by conftest, but make .load raise ImportError
        mlx_mock = MagicMock()
        mlx_mock.load.side_effect = ImportError("no mlx_lm")
        monkeypatch.setitem(sys.modules, "mlx_lm", mlx_mock)
        monkeypatch.setattr(services, "_llm_large_loading", False)

        result = services._ensure_large_llm_loaded()
        # Should return False (model unavailable), not raise
        assert result is False

    def test_llm_large_complete_returns_none_when_unavailable(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "_llm_large_model", None)
        monkeypatch.setattr(services, "_llm_large_loading", False)
        import sys

        mlx_mock = MagicMock()
        mlx_mock.load.side_effect = RuntimeError("no model")
        monkeypatch.setitem(sys.modules, "mlx_lm", mlx_mock)

        result = services.llm_large_complete("test prompt")
        assert result is None

    def test_llm_large_complete_calls_generate_when_loaded(self, monkeypatch):
        import services

        mock_model = MagicMock()
        mock_tok = MagicMock()
        monkeypatch.setattr(services, "_llm_large_model", mock_model)
        monkeypatch.setattr(services, "_llm_large_tokenizer", mock_tok)

        import sys

        mlx_mock = sys.modules["mlx_lm"]
        mlx_mock.generate.return_value = "test output"
        mlx_mock.sample_utils.make_sampler.return_value = MagicMock()
        # Patch the module reference
        with patch.object(services, "llm_large_complete", wraps=services.llm_large_complete):
            # Manually call with mock already set
            mlx_mock.generate.return_value = "output text"
            result = services.llm_large_complete("prompt", max_tokens=50)
            assert result == "output text"


# ── Feature 2: Autocomplete (already exists, verify endpoint shape) ──────────


class TestAutocomplete:
    def test_autocomplete_complete_mode(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "_llm_model", MagicMock())
        monkeypatch.setattr(services, "_llm_tokenizer", MagicMock())

        import sys

        mlx_mock = sys.modules["mlx_lm"]
        mlx_mock.generate.return_value = "world"  # type: ignore[attr-defined]
        sample_utils_mock = MagicMock()
        sample_utils_mock.make_sampler.return_value = MagicMock()
        sys.modules["mlx_lm.sample_utils"] = sample_utils_mock

        with patch.object(services, "llm_complete", return_value="world"):
            result = services.autocomplete(draft="hello ", messages=[], mode="complete")
        assert result is not None

    def test_autocomplete_short_draft_returns_none(self):
        import services

        result = services.autocomplete(draft="hi", messages=[], mode="complete")
        assert result is None

    def test_autocomplete_invalid_mode_raises(self):
        import services

        with pytest.raises(ValueError, match="Invalid mode"):
            services.autocomplete(draft="hello", mode="invalid")

    def test_autocomplete_reply_mode_no_messages_returns_none(self):
        import services

        result = services.autocomplete(draft="", messages=None, mode="reply")
        assert result is None


# ── Feature 3: AI Briefing ───────────────────────────────────────────────────


class TestAIBriefing:
    def test_briefing_returns_correct_structure(self):
        import services

        events = [
            {"summary": "Standup", "start": "2026-04-10T09:00:00", "end": "2026-04-10T09:30:00"}
        ]
        reminders = [{"title": "Review PR", "completed": False}]
        conversations = [
            {"source": "imessage", "unread": 2},
            {"source": "gmail", "unread": 5},
        ]
        github_notifs = [{"unread": True}, {"unread": False}]
        github_prs = [{"id": 1}]

        with patch.object(services, "llm_large_complete", return_value=None):
            result = services.ai_briefing(
                events, reminders, conversations, github_notifs, github_prs
            )

        assert "events" in result
        assert "pending_reminders" in result
        assert "unread_counts" in result
        assert result["unread_counts"]["imessage"] == 2
        assert result["unread_counts"]["gmail"] == 5
        assert result["unread_counts"]["github_notifications"] == 1
        assert result["unread_counts"]["github_prs"] == 1

    def test_briefing_filters_completed_reminders(self):
        import services

        reminders = [
            {"title": "Done", "completed": True},
            {"title": "Todo", "completed": False},
        ]
        with patch.object(services, "llm_large_complete", return_value=None):
            result = services.ai_briefing([], reminders, [], [], [])
        assert len(result["pending_reminders"]) == 1
        assert result["pending_reminders"][0]["title"] == "Todo"

    def test_briefing_includes_summary_when_large_model_returns(self):
        import services

        with patch.object(services, "llm_large_complete", return_value="Great day ahead!"):
            result = services.ai_briefing([], [], [], [], [])
        assert result["summary"] == "Great day ahead!"

    def test_briefing_summary_none_when_large_model_unavailable(self):
        import services

        with patch.object(services, "llm_large_complete", return_value=None):
            result = services.ai_briefing([], [], [], [], [])
        assert result["summary"] is None


# ── Feature 4: AI Triage ─────────────────────────────────────────────────────


class TestAITriage:
    def test_triage_empty_conversations(self):
        import services

        result = services.ai_triage([])
        assert result == {}

    def test_triage_defaults_to_normal_without_large_model(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "get_large_outlines_model", lambda: None)
        conversations = [
            {"id": "c1", "source": "imessage", "name": "Alice", "snippet": "hi", "unread": 1},
            {"id": "c2", "source": "gmail", "name": "Bob", "snippet": "hello", "unread": 0},
        ]
        result = services.ai_triage(conversations)
        assert result == {"c1": "normal", "c2": "normal"}

    def test_triage_uses_choice_gen_with_large_model(self, monkeypatch):
        import services

        mock_model = MagicMock()
        monkeypatch.setattr(services, "get_large_outlines_model", lambda: mock_model)

        import sys

        outlines_mock = sys.modules["outlines"]
        choice_gen = MagicMock()
        choice_gen.return_value = "urgent"
        outlines_mock.generate.choice.return_value = choice_gen

        conversations = [
            {
                "id": "c1",
                "source": "gmail",
                "name": "Boss",
                "snippet": "URGENT fix now",
                "unread": 3,
            }
        ]
        result = services.ai_triage(conversations)
        assert "c1" in result
        assert result["c1"] in ("urgent", "normal", "low")

    def test_triage_handles_exception_gracefully(self, monkeypatch):
        import services

        mock_model = MagicMock()
        monkeypatch.setattr(services, "get_large_outlines_model", lambda: mock_model)

        import sys

        outlines_mock = sys.modules["outlines"]
        outlines_mock.generate.choice.side_effect = RuntimeError("outlines broken")

        conversations = [{"id": "c1", "source": "gmail", "name": "X", "snippet": "y", "unread": 0}]
        result = services.ai_triage(conversations)
        assert result == {"c1": "normal"}

    def test_triage_skips_missing_id(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "get_large_outlines_model", lambda: None)
        conversations = [{"source": "imessage", "name": "Alice"}]  # no id
        result = services.ai_triage(conversations)
        # Empty id key filtered out
        assert "" not in result or result.get("", "normal") == "normal"


# ── Feature 5: AI Summarization ──────────────────────────────────────────────


class TestAISummarization:
    def test_summarize_skips_short_threads(self):
        import services

        msgs = [{"sender": "Alice", "body": "hi"}] * 3
        result = services.ai_summarize("t1", msgs)
        assert result["skipped"] is True
        assert result["summary"] is None

    def test_summarize_returns_structure_for_long_threads(self):
        import services

        msgs = [{"sender": f"User{i}", "body": f"message {i} body text here"} for i in range(6)]

        with patch.object(
            services,
            "llm_large_complete",
            return_value=(
                "Summary of thread.\n"
                "Key points:\n"
                "- Point one\n"
                "- Point two\n"
                "Action items:\n"
                "- Do something\n"
                "Decisions:\n"
                "- Decided on X\n"
            ),
        ):
            result = services.ai_summarize("t1", msgs)

        assert result["skipped"] is False
        assert "summary" in result
        assert "key_points" in result
        assert "action_items" in result
        assert "decisions" in result

    def test_summarize_returns_empty_when_large_model_unavailable(self):
        import services

        msgs = [{"sender": f"U{i}", "body": f"body {i}"} for i in range(6)]
        with patch.object(services, "llm_large_complete", return_value=None):
            result = services.ai_summarize("t1", msgs)
        assert result["skipped"] is False
        assert result["summary"] is None
        assert result["action_items"] == []


# ── Feature 6: AI Action Extraction ──────────────────────────────────────────


class TestAIActionExtraction:
    def test_extract_short_text_returns_empty(self):
        import services

        result = services.ai_extract_actions("hi")
        assert result == {"actions": []}

    def test_extract_returns_actions_structure(self):
        import services

        # Mock outlines to return a valid action list
        mock_result = MagicMock()
        action = MagicMock()
        action.text = "Schedule meeting"
        action.deadline = "tomorrow"
        action.type = "meeting"
        mock_result.actions = [action]

        with patch.object(services, "generate_json_large", return_value=mock_result):
            result = services.ai_extract_actions(
                "Please schedule a meeting with Alice tomorrow about the Q2 roadmap review."
            )

        assert "actions" in result
        assert len(result["actions"]) == 1
        assert result["actions"][0]["text"] == "Schedule meeting"
        assert result["actions"][0]["type"] == "meeting"

    def test_extract_falls_back_to_small_model(self):
        import services

        mock_result = MagicMock()
        action = MagicMock()
        action.text = "Follow up"
        action.deadline = None
        action.type = "follow-up"
        mock_result.actions = [action]

        with (
            patch.object(services, "generate_json_large", return_value=None),
            patch.object(services, "generate_json", return_value=mock_result),
        ):
            result = services.ai_extract_actions(
                "Please follow up with the team about the deployment schedule next week."
            )

        assert "actions" in result
        assert len(result["actions"]) == 1

    def test_extract_handles_exception_gracefully(self):
        import services

        with patch.object(services, "generate_json_large", side_effect=RuntimeError("broken")):
            result = services.ai_extract_actions(
                "This is a long message body with action items to extract from the text."
            )
        assert result == {"actions": []}

    def test_extract_normalizes_invalid_type(self):
        import services

        mock_result = MagicMock()
        action = MagicMock()
        action.text = "Do something"
        action.deadline = None
        action.type = "invalid_type"
        mock_result.actions = [action]

        with patch.object(services, "generate_json_large", return_value=mock_result):
            result = services.ai_extract_actions(
                "Some long message body text that has actionable content in it here."
            )
        assert result["actions"][0]["type"] == "task"
