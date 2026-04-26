"""Tests for the desktop notifications feature."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# ── Config load/save roundtrip ────────────────────────────────────────────────


def test_load_notification_config_creates_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import load_notification_config

    cfg = load_notification_config()
    assert cfg["enabled"] is True
    assert cfg["sources"]["imessage"] is True
    assert cfg["sources"]["gmail"] is True
    assert cfg["sources"]["calendar"] is True
    assert cfg["sources"]["github"] is True
    assert cfg["quiet_hours"]["enabled"] is False
    assert config_path.exists()


def test_load_notification_config_roundtrip(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import load_notification_config, save_notification_config

    # Save a modified config
    cfg = load_notification_config()
    cfg["enabled"] = False
    cfg["sources"]["github"] = False
    cfg["quiet_hours"]["enabled"] = True
    cfg["quiet_hours"]["start"] = "21:00"
    save_notification_config(cfg)

    # Reload and verify
    cfg2 = load_notification_config()
    assert cfg2["enabled"] is False
    assert cfg2["sources"]["github"] is False
    assert cfg2["quiet_hours"]["enabled"] is True
    assert cfg2["quiet_hours"]["start"] == "21:00"


def test_save_notification_config_creates_parent_dirs(tmp_path, monkeypatch):
    config_path = tmp_path / "deep" / "nested" / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import save_notification_config

    ok = save_notification_config({"enabled": True, "sources": {}, "quiet_hours": {}})
    assert ok is True
    assert config_path.exists()


def test_load_notification_config_fills_missing_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    # Write partial config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"enabled": False}))
    from services import load_notification_config

    cfg = load_notification_config()
    assert cfg["enabled"] is False
    # Missing keys filled from defaults
    assert "sources" in cfg
    assert "quiet_hours" in cfg


# ── Quiet hours logic ─────────────────────────────────────────────────────────


def test_in_quiet_hours_disabled_returns_false():
    from services import _in_quiet_hours

    assert _in_quiet_hours({"enabled": False, "start": "00:00", "end": "23:59"}) is False


def test_in_quiet_hours_within_window():
    from services import _in_quiet_hours

    with patch("services.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 10, 23, 30)
        # Overnight window 22:00 – 08:00, current time 23:30 → in window
        result = _in_quiet_hours({"enabled": True, "start": "22:00", "end": "08:00"})
    assert result is True


def test_in_quiet_hours_outside_window():
    from services import _in_quiet_hours

    with patch("services.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 10, 14, 0)
        # Overnight window 22:00 – 08:00, current time 14:00 → outside
        result = _in_quiet_hours({"enabled": True, "start": "22:00", "end": "08:00"})
    assert result is False


def test_in_quiet_hours_same_day_range():
    from services import _in_quiet_hours

    with patch("services.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 10, 10, 0)
        # Daytime window 09:00 – 17:00, current 10:00 → in window
        result = _in_quiet_hours({"enabled": True, "start": "09:00", "end": "17:00"})
    assert result is True


def test_in_quiet_hours_early_morning_in_overnight_window():
    from services import _in_quiet_hours

    with patch("services.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 10, 7, 30)
        # Overnight 22:00 – 08:00, current 07:30 → in window
        result = _in_quiet_hours({"enabled": True, "start": "22:00", "end": "08:00"})
    assert result is True


# ── Source filtering ──────────────────────────────────────────────────────────


def test_send_notification_respects_source_disabled(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import save_notification_config

    cfg = {
        "enabled": True,
        "sources": {"imessage": False, "gmail": True, "calendar": True, "github": True},
        "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00"},
    }
    save_notification_config(cfg)

    from services import send_notification

    with patch("subprocess.run") as mock_run:
        result = send_notification("Test", "body", "imessage")
    assert result is False
    mock_run.assert_not_called()


def test_send_notification_respects_global_disabled(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import save_notification_config

    cfg = {
        "enabled": False,
        "sources": {"imessage": True, "gmail": True, "calendar": True, "github": True},
        "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00"},
    }
    save_notification_config(cfg)

    from services import send_notification

    with patch("subprocess.run") as mock_run:
        result = send_notification("Test", "body", "imessage")
    assert result is False
    mock_run.assert_not_called()


def test_send_notification_respects_quiet_hours(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import save_notification_config

    cfg = {
        "enabled": True,
        "sources": {"imessage": True, "gmail": True, "calendar": True, "github": True},
        "quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"},
    }
    save_notification_config(cfg)

    from services import send_notification

    with (
        patch("services.datetime") as mock_dt,
        patch("subprocess.run") as mock_run,
    ):
        mock_dt.now.return_value = datetime(2026, 4, 10, 23, 0)
        result = send_notification("Test", "body", "imessage")
    assert result is False
    mock_run.assert_not_called()


def test_send_notification_fires_osascript_when_enabled(tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import save_notification_config

    cfg = {
        "enabled": True,
        "sources": {"imessage": True, "gmail": True, "calendar": True, "github": True},
        "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00"},
    }
    save_notification_config(cfg)

    from services import send_notification

    # Ensure pyobjc path fails so we fall through to osascript
    with (
        patch.dict("sys.modules", {"objc": None, "UserNotifications": None, "Foundation": None}),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = send_notification("Hello", "World", "gmail")
    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "osascript" in cmd
    assert "Hello" in " ".join(cmd)


# ── Server endpoints ──────────────────────────────────────────────────────────


@pytest.fixture()
def server_client():
    import os

    with (
        patch.dict(os.environ, {"INBOX_SERVER_TOKEN": ""}, clear=False),
        patch("inbox_server.init_contacts", return_value=0),
        patch("inbox_server.google_auth_all", return_value=({}, {}, {}, {}, {}, {})),
    ):
        from fastapi.testclient import TestClient

        from inbox_server import app, state

        state.gmail_services = {}
        state.cal_services = {}
        state.drive_services = {}
        state.sheets_services = {}
        with TestClient(app) as c:
            yield c


def test_get_notification_config_endpoint(server_client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", tmp_path / "notifications.json")
    resp = server_client.get("/notifications/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "sources" in data
    assert "quiet_hours" in data


def test_put_notification_config_endpoint(server_client, tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    cfg = {
        "enabled": False,
        "sources": {"imessage": True, "gmail": False, "calendar": True, "github": True},
        "quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"},
    }
    resp = server_client.put("/notifications/config", json=cfg)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = json.loads(config_path.read_text())
    assert saved["enabled"] is False
    assert saved["sources"]["gmail"] is False


def test_post_notification_test_endpoint(server_client, tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    # Enable notifications
    from services import save_notification_config

    save_notification_config(
        {
            "enabled": True,
            "sources": {"imessage": True, "gmail": True, "calendar": True, "github": True},
            "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00"},
        }
    )
    with (
        patch.dict("sys.modules", {"objc": None, "UserNotifications": None, "Foundation": None}),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        resp = server_client.post("/notifications/test", json={"title": "Test", "body": "Hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


def test_post_notification_test_endpoint_disabled(server_client, tmp_path, monkeypatch):
    config_path = tmp_path / "notifications.json"
    monkeypatch.setattr("services.NOTIFICATION_CONFIG_PATH", config_path)
    from services import save_notification_config

    save_notification_config(
        {
            "enabled": False,
            "sources": {"imessage": True, "gmail": True, "calendar": True, "github": True},
            "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00"},
        }
    )
    with patch("subprocess.run") as mock_run:
        resp = server_client.post("/notifications/test", json={"title": "Test", "body": "Hello"})
    assert resp.status_code == 200
    assert resp.json()["sent"] is False
    mock_run.assert_not_called()
