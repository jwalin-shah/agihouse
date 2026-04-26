"""Tests for audio services — whisper config and dictation text cleaning."""

from __future__ import annotations

from unittest.mock import patch

from services import CHUNK_SECS, SAMPLE_RATE, SILENCE_RMS_THRESHOLD, _clean_line

# ── _clean_line ─────────────────────────────────────────────────────────────


class TestCleanLine:
    def test_strips_ansi_codes(self):
        result = _clean_line("\x1b[32mhello world\x1b[0m")
        assert result == "hello world"

    def test_strips_timestamp_markers(self):
        result = _clean_line("[00:01:23.456 --> 00:01:25.789] Hello")
        assert result == "Hello"

    def test_strips_both(self):
        result = _clean_line("\x1b[1m[00:00:00.000 --> 00:00:01.000] test text\x1b[0m")
        assert result == "test text"

    def test_empty_line(self):
        assert _clean_line("") == ""

    def test_normal_text_unchanged(self):
        assert _clean_line("Just regular text") == "Just regular text"

    def test_whitespace_stripped(self):
        assert _clean_line("  hello  ") == "hello"


# ── whisper constants ───────────────────────────────────────────────────────


class TestWhisperConfig:
    def test_sample_rate_standard(self):
        assert SAMPLE_RATE == 16000

    def test_chunk_secs_positive(self):
        assert CHUNK_SECS > 0

    def test_silence_threshold_positive(self):
        assert SILENCE_RMS_THRESHOLD > 0


# ── whisper_stream_available ────────────────────────────────────────────────


class TestWhisperStreamAvailable:
    @patch("services.Path.exists", return_value=True)
    def test_available_when_both_exist(self, mock_exists):
        from services import whisper_stream_available

        assert whisper_stream_available() is True

    @patch("services.Path.exists", return_value=False)
    def test_not_available_when_missing(self, mock_exists):
        from services import whisper_stream_available

        assert whisper_stream_available() is False


# ── AmbientService ──────────────────────────────────────────────────────────


class TestAmbientService:
    def test_initial_state(self):
        from services import AmbientService

        svc = AmbientService(on_note=lambda raw, summary: None)
        assert svc.is_running is False

    def test_process_buffer_skips_short_text(self):
        from services import AmbientService

        notes_received: list[str] = []
        svc = AmbientService(on_note=lambda raw, summary: notes_received.append(raw))
        svc._buffer = ["hi"]  # too few words
        svc._process_buffer()
        assert notes_received == []

    def test_process_buffer_fires_callback(self):
        from services import AmbientService

        notes_received: list[str] = []

        def on_note(raw, summary):
            notes_received.append(raw)

        svc = AmbientService(on_note=on_note)
        # Add enough words to pass MIN_CHUNK_WORDS threshold
        svc._buffer = ["word " * 15]
        with patch("services.extract_summary", return_value=None):
            svc._process_buffer()
        assert len(notes_received) == 1

    def test_process_buffer_clears_buffer(self):
        from services import AmbientService

        svc = AmbientService(on_note=lambda raw, summary: None)
        svc._buffer = ["word " * 15]
        with patch("services.extract_summary", return_value=None):
            svc._process_buffer()
        assert svc._buffer == []


# ── DictationService ────────────────────────────────────────────────────────


class TestDictationService:
    def test_initial_state(self):
        from services import DictationService

        svc = DictationService()
        assert svc.is_running is False

    def test_stop_when_not_running(self):
        from services import DictationService

        svc = DictationService()
        svc.stop()  # should not raise
        assert svc.is_running is False
