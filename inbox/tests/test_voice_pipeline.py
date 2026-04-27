"""Tests for the voice-pipeline milestone.

Covers: ambient core, extraction notes, dictation, voice config, autostart.
All ML/audio deps are mocked — runs in CI without MLX or sounddevice.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ── Voice Config ─────────────────────────────────────────────────────────────


class TestVoiceConfig:
    def test_load_defaults_when_no_file(self, tmp_path, monkeypatch):
        from services import _VOICE_CONFIG_DEFAULTS, load_voice_config

        monkeypatch.setattr("services.VOICE_CONFIG_PATH", tmp_path / "voice.json")
        cfg = load_voice_config()
        assert cfg["ambient_autostart"] == _VOICE_CONFIG_DEFAULTS["ambient_autostart"]
        assert "dictation_hotkey" in cfg
        assert "vault_dir" in cfg

    def test_save_and_reload(self, tmp_path, monkeypatch):
        config_path = tmp_path / "voice.json"
        monkeypatch.setattr("services.VOICE_CONFIG_PATH", config_path)

        from services import load_voice_config, save_voice_config

        save_voice_config({"ambient_autostart": False, "dictation_hotkey": "ctrl+d"})
        loaded = load_voice_config()
        assert loaded["ambient_autostart"] is False
        assert loaded["dictation_hotkey"] == "ctrl+d"

    def test_save_merges_missing_defaults(self, tmp_path, monkeypatch):
        config_path = tmp_path / "voice.json"
        monkeypatch.setattr("services.VOICE_CONFIG_PATH", config_path)

        from services import load_voice_config, save_voice_config

        # Only save one key
        save_voice_config({"ambient_autostart": False})
        loaded = load_voice_config()
        # Other defaults should still be present
        assert "dictation_hotkey" in loaded
        assert "vault_dir" in loaded

    def test_save_creates_parent_dirs(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "voice.json"
        monkeypatch.setattr("services.VOICE_CONFIG_PATH", nested)

        from services import save_voice_config

        save_voice_config({})
        assert nested.exists()

    def test_load_corrupt_file_returns_defaults(self, tmp_path, monkeypatch):
        config_path = tmp_path / "voice.json"
        config_path.write_text("not valid json {{")
        monkeypatch.setattr("services.VOICE_CONFIG_PATH", config_path)

        from services import load_voice_config

        cfg = load_voice_config()
        assert "ambient_autostart" in cfg


# ── route_voice_command ───────────────────────────────────────────────────────


class TestRouteVoiceCommand:
    def test_no_handlers_returns_false(self):
        from services import _voice_command_handlers, route_voice_command

        old = list(_voice_command_handlers)
        _voice_command_handlers.clear()
        try:
            assert route_voice_command("hello") is False
        finally:
            _voice_command_handlers.extend(old)

    def test_handler_called_and_returns_true(self):
        from services import _voice_command_handlers, route_voice_command

        called_with = []

        def handler(text: str) -> bool:
            called_with.append(text)
            return True

        _voice_command_handlers.append(handler)
        try:
            result = route_voice_command("open inbox")
            assert result is True
            assert called_with == ["open inbox"]
        finally:
            _voice_command_handlers.remove(handler)

    def test_handler_exception_is_swallowed(self):
        from services import _voice_command_handlers, route_voice_command

        def bad_handler(text: str) -> bool:
            raise RuntimeError("boom")

        _voice_command_handlers.append(bad_handler)
        try:
            assert route_voice_command("test") is False
        finally:
            _voice_command_handlers.remove(bad_handler)


# ── AmbientService transcript ─────────────────────────────────────────────────


class TestAmbientTranscript:
    def test_transcript_empty_initially(self):
        from services import AmbientService

        svc = AmbientService(on_note=lambda r, s: None)
        assert svc.get_transcript() == []

    def test_transcript_appends_segments(self):
        from services import AmbientService

        svc = AmbientService(on_note=lambda r, s: None)
        svc._transcript = ["hello world", "second segment"]
        result = svc.get_transcript()
        assert result == ["hello world", "second segment"]

    def test_transcript_respects_max_segments(self):
        from services import AmbientService

        svc = AmbientService(on_note=lambda r, s: None)
        svc._transcript = [f"segment {i}" for i in range(100)]
        result = svc.get_transcript(max_segments=5)
        assert len(result) == 5
        assert result[-1] == "segment 99"

    def test_transcript_capped_at_maxlen(self):
        from services import TRANSCRIPT_MAXLEN, AmbientService

        svc = AmbientService(on_note=lambda r, s: None)
        # Simulate overflow
        svc._transcript = [f"x{i}" for i in range(TRANSCRIPT_MAXLEN + 50)]
        # Trim as _capture_loop would
        svc._transcript = svc._transcript[-TRANSCRIPT_MAXLEN:]
        assert len(svc.get_transcript(max_segments=9999)) == TRANSCRIPT_MAXLEN


# ── ambient_available ─────────────────────────────────────────────────────────


class TestAmbientAvailable:
    def test_available_when_both_present(self):
        from services import ambient_available

        with (
            patch("services.sounddevice_available", return_value=True),
            patch("services.mlx_whisper_available", return_value=True),
        ):
            ok, reason = ambient_available()
        assert ok is True
        assert reason == ""

    def test_unavailable_when_sounddevice_missing(self):
        from services import ambient_available

        with (
            patch("services.sounddevice_available", return_value=False),
            patch("services.mlx_whisper_available", return_value=True),
        ):
            ok, reason = ambient_available()
        assert ok is False
        assert "sounddevice" in reason

    def test_unavailable_when_mlx_whisper_missing(self):
        """In ASR_PROVIDER=mlx mode, missing mlx_whisper means unavailable."""
        from services import ambient_available

        with (
            patch("services.sounddevice_available", return_value=True),
            patch("services.mlx_whisper_available", return_value=False),
            patch("services.ASR_PROVIDER", "mlx"),
        ):
            ok, reason = ambient_available()
        assert ok is False
        assert "mlx_whisper" in reason

    def test_available_in_cloud_mode_without_mlx(self):
        """ASR_PROVIDER=cloud + OpenRouter key set → available even w/o mlx."""
        from services import ambient_available

        with (
            patch("services.sounddevice_available", return_value=True),
            patch("services.mlx_whisper_available", return_value=False),
            patch("services.ASR_PROVIDER", "cloud"),
            patch("services.cloud_asr_available", return_value=True),
        ):
            ok, reason = ambient_available()
        assert ok is True
        assert reason == ""

    def test_unavailable_when_neither_backend_works(self):
        """Cloud unconfigured AND mlx missing → unavailable."""
        from services import ambient_available

        with (
            patch("services.sounddevice_available", return_value=True),
            patch("services.mlx_whisper_available", return_value=False),
            patch("services.ASR_PROVIDER", "cloud"),
            patch("services.cloud_asr_available", return_value=False),
        ):
            ok, reason = ambient_available()
        assert ok is False
        assert "OPENROUTER_API_KEY" in reason


# ── Server endpoint tests ─────────────────────────────────────────────────────


@pytest.fixture()
def server_client():
    import os

    with (
        patch.dict(os.environ, {"INBOX_SERVER_TOKEN": ""}, clear=False),
        patch("inbox_server.init_contacts", return_value=0),
        patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
        patch("inbox_server.load_voice_config", return_value={"ambient_autostart": False}),
    ):
        from inbox_server import app, state
        from services import AmbientService, DictationService

        state.gmail_services = {}
        state.cal_services = {}
        state.drive_services = {}
        state.ambient = AmbientService(on_note=lambda r, s: None)
        state.dictation = DictationService()
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            yield c, state


class TestAmbientStatusEndpoint:
    def test_includes_available_field(self, server_client):
        c, state = server_client
        with (
            patch(
                "inbox_server.ambient_available", return_value=(False, "sounddevice not installed")
            ),
        ):
            resp = c.get("/ambient/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert data["available"] is False
        assert "sounddevice" in data["reason"]

    def test_ambient_not_running_by_default(self, server_client):
        c, state = server_client
        with patch("inbox_server.ambient_available", return_value=(True, "")):
            resp = c.get("/ambient/status")
        assert resp.status_code == 200
        assert resp.json()["ambient"] is False


class TestAmbientTranscriptEndpoint:
    def test_returns_empty_segments_initially(self, server_client):
        c, state = server_client
        resp = c.get("/ambient/transcript")
        assert resp.status_code == 200
        data = resp.json()
        assert "segments" in data
        assert data["segments"] == []
        assert data["count"] == 0

    def test_returns_segments_from_service(self, server_client):
        c, state = server_client
        state.ambient._transcript = ["hello", "world"]
        resp = c.get("/ambient/transcript")
        assert resp.status_code == 200
        data = resp.json()
        assert data["segments"] == ["hello", "world"]
        assert data["count"] == 2

    def test_limit_param(self, server_client):
        c, state = server_client
        state.ambient._transcript = [f"s{i}" for i in range(20)]
        resp = c.get("/ambient/transcript?limit=5")
        assert resp.status_code == 200
        assert resp.json()["count"] == 5


class TestDictationStatusEndpoint:
    def test_dictation_status_not_running(self, server_client):
        c, state = server_client
        resp = c.get("/dictation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "available" in data
        assert data["running"] is False

    def test_dictation_start_unavailable(self, server_client):
        c, state = server_client
        state.dictation._available = False
        resp = c.post("/dictation/start")
        assert resp.status_code == 400


class TestVoiceConfigEndpoints:
    def test_get_voice_config(self, server_client):
        c, _ = server_client
        fake_cfg = {"ambient_autostart": True, "dictation_hotkey": "f5", "vault_dir": "/tmp/vault"}
        with patch("inbox_server.load_voice_config", return_value=fake_cfg):
            resp = c.get("/voice/config")
        assert resp.status_code == 200
        assert resp.json()["dictation_hotkey"] == "f5"

    def test_put_voice_config(self, server_client, tmp_path):
        c, _ = server_client
        fake_cfg = {"ambient_autostart": True, "dictation_hotkey": "f5", "vault_dir": "/tmp/vault"}
        with (
            patch("inbox_server.load_voice_config", return_value=fake_cfg),
            patch("inbox_server.save_voice_config") as mock_save,
        ):
            resp = c.put("/voice/config", json={"dictation_hotkey": "ctrl+d"})
        assert resp.status_code == 200
        assert resp.json()["dictation_hotkey"] == "ctrl+d"
        mock_save.assert_called_once()

    def test_put_voice_config_partial_update(self, server_client):
        c, _ = server_client
        fake_cfg = {"ambient_autostart": True, "dictation_hotkey": "f5", "vault_dir": "/tmp/v"}
        with (
            patch("inbox_server.load_voice_config", return_value=fake_cfg),
            patch("inbox_server.save_voice_config"),
        ):
            resp = c.put("/voice/config", json={"ambient_autostart": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambient_autostart"] is False
        # Other fields preserved
        assert data["dictation_hotkey"] == "f5"


class TestAmbientNotesSearch:
    def test_notes_q_filter(self, server_client):
        c, _ = server_client
        notes = [
            {"date": "2026-04-10", "path": "/a", "size": 1},
            {"date": "2026-03-01", "path": "/b", "size": 2},
        ]
        with patch("inbox_server.ambient_notes.list_daily_notes", return_value=notes):
            resp = c.get("/ambient/notes?q=2026-04")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["date"] == "2026-04-10"


# ── Ambient autostart on server boot ─────────────────────────────────────────


class TestAmbientAutostart:
    def test_autostart_enabled_starts_ambient(self):
        import os

        from inbox_server import app, state

        started = []

        def mock_start():
            started.append(True)
            state.ambient._running = True

        with (
            patch.dict(os.environ, {"INBOX_DISABLE_AMBIENT": ""}, clear=False),
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
            patch("inbox_server.load_voice_config", return_value={"ambient_autostart": True}),
            patch("inbox_server.ambient_available", return_value=(True, "")),
            patch.object(state.ambient, "start", side_effect=mock_start),
        ):
            state.ambient._running = False
            from fastapi.testclient import TestClient

            with TestClient(app):
                assert state.ambient.is_running is True
            state.ambient._running = False
        assert started

    def test_autostart_skipped_when_unavailable(self):
        from inbox_server import app, state

        with (
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
            patch("inbox_server.load_voice_config", return_value={"ambient_autostart": True}),
            patch(
                "inbox_server.ambient_available", return_value=(False, "sounddevice not installed")
            ),
        ):
            state.ambient._running = False
            from fastapi.testclient import TestClient

            with TestClient(app):
                assert state.ambient.is_running is False

    def test_autostart_disabled_by_config(self):
        from inbox_server import app, state

        with (
            patch("inbox_server.init_contacts", return_value=0),
            patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {})),
            patch("inbox_server.load_voice_config", return_value={"ambient_autostart": False}),
            patch("inbox_server.ambient_available", return_value=(True, "")),
        ):
            state.ambient._running = False
            from fastapi.testclient import TestClient

            with TestClient(app):
                assert state.ambient.is_running is False
