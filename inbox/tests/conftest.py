"""Shared fixtures for inbox tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Stub heavy dependencies that aren't installed in test envs ──────────────


def _stub_module(name: str):
    """Install a MagicMock as a module if it's not already importable."""
    if name not in sys.modules:
        sys.modules[name] = MagicMock()


# These are hardware/ML deps that may not be present in CI
for mod in [
    "mlx_lm",
    "mlx_lm.sample_utils",
    "mlx_whisper",
    "sounddevice",
    "outlines",
    "outlines.models",
    "outlines.generate",
    "Quartz",
]:
    _stub_module(mod)


@pytest.fixture
def tmp_vault(tmp_path, monkeypatch):
    """Redirect ambient_notes paths to a temp directory."""
    import ambient_notes

    daily = tmp_path / "daily"
    ambient = tmp_path / "ambient"
    monkeypatch.setattr(ambient_notes, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(ambient_notes, "DAILY_DIR", daily)
    monkeypatch.setattr(ambient_notes, "AMBIENT_DIR", ambient)
    return tmp_path


@pytest.fixture
def mock_drive_service():
    """Mock Google Drive API service with chainable method calls."""
    svc = MagicMock()
    # Make the chain svc.files().list(...).execute() work
    # Each call returns a new mock so chaining works naturally
    return svc


@pytest.fixture
def mock_github_token(monkeypatch):
    """Patch _github_token to return a fake token."""
    from unittest.mock import patch

    with patch("services._github_token", return_value="ghp_test_token"):
        yield


@pytest.fixture
def tmp_reminders_db(tmp_path):
    """Create a temporary Reminders-style SQLite database for testing."""
    import sqlite3
    from datetime import datetime, timedelta

    APPLE_EPOCH = datetime(2001, 1, 1)

    db_path = tmp_path / "Data-test.sqlite"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Create the tables that services.py expects
    cur.executescript("""
        CREATE TABLE ZREMCDBASELIST (
            Z_PK INTEGER PRIMARY KEY,
            ZNAME TEXT,
            ZMARKEDFORDELETION INTEGER
        );
        CREATE TABLE ZREMCDREMINDER (
            Z_PK INTEGER PRIMARY KEY,
            ZTITLE TEXT,
            ZCOMPLETED INTEGER DEFAULT 0,
            ZFLAGGED INTEGER DEFAULT 0,
            ZPRIORITY INTEGER DEFAULT 0,
            ZDUEDATE REAL,
            ZNOTES TEXT,
            ZCREATIONDATE REAL,
            ZLIST INTEGER,
            ZMARKEDFORDELETION INTEGER,
            FOREIGN KEY (ZLIST) REFERENCES ZREMCDBASELIST(Z_PK)
        );
    """)

    # Insert test lists
    cur.execute("INSERT INTO ZREMCDBASELIST (Z_PK, ZNAME) VALUES (1, 'Daily')")
    cur.execute("INSERT INTO ZREMCDBASELIST (Z_PK, ZNAME) VALUES (2, 'Work')")

    # Timestamps relative to Apple epoch
    now = datetime.now()
    created_secs = (now - timedelta(days=7) - APPLE_EPOCH).total_seconds()
    due_1d = (now + timedelta(days=1) - APPLE_EPOCH).total_seconds()
    due_3h = (now + timedelta(hours=3) - APPLE_EPOCH).total_seconds()

    # Insert test reminders
    # Incomplete reminder in Daily
    cur.execute(
        "INSERT INTO ZREMCDREMINDER (Z_PK, ZTITLE, ZCOMPLETED, ZFLAGGED, ZPRIORITY, ZDUEDATE, ZNOTES, ZCREATIONDATE, ZLIST) "
        "VALUES (1, 'Buy groceries', 0, 0, 0, ?, 'Milk, eggs', ?, 1)",
        (due_1d, created_secs),
    )
    # Completed reminder in Daily
    cur.execute(
        "INSERT INTO ZREMCDREMINDER (Z_PK, ZTITLE, ZCOMPLETED, ZFLAGGED, ZPRIORITY, ZDUEDATE, ZNOTES, ZCREATIONDATE, ZLIST) "
        "VALUES (2, 'Done task', 1, 0, 0, ?, '', ?, 1)",
        (due_1d, created_secs),
    )
    # Flagged, high-priority reminder in Work
    cur.execute(
        "INSERT INTO ZREMCDREMINDER (Z_PK, ZTITLE, ZCOMPLETED, ZFLAGGED, ZPRIORITY, ZDUEDATE, ZNOTES, ZCREATIONDATE, ZLIST) "
        "VALUES (3, 'Ship feature', 0, 1, 1, ?, 'Q2 deadline', ?, 2)",
        (due_3h, created_secs),
    )

    conn.commit()
    conn.close()

    return tmp_path
