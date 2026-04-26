"""
Scheduler persistence layer — SQLite-backed storage for message scheduling,
follow-up reminders, and task↔message links. Pattern mirrors memory_store.py.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
SCHEDULER_DB = BASE_DIR / ".inbox_scheduler.sqlite3"


@dataclass
class ScheduledMessage:
    id: int | None = None
    source: str = ""  # "gmail" | "imessage"
    conv_id: str = ""
    text: str = ""
    send_at: str = ""  # ISO datetime
    status: str = "pending"  # "pending" | "sent" | "cancelled" | "failed"
    account: str = ""  # Gmail account
    created_at: str = ""
    sent_at: str | None = None
    error: str | None = None


@dataclass
class FollowupReminder:
    id: int | None = None
    source: str = ""  # "gmail" | "imessage"
    conv_id: str = ""
    thread_id: str = ""  # Gmail thread_id
    remind_after: str = ""  # ISO datetime
    reminder_title: str = ""
    reminder_list: str = "Reminders"
    status: str = "active"  # "active" | "fired" | "cancelled" | "replied"
    created_at: str = ""
    fired_at: str | None = None


@dataclass
class TaskMessageLink:
    id: int | None = None
    task_id: str = ""  # Google Task id OR reminder_id
    task_source: str = ""  # "google_tasks" | "reminders"
    message_id: str = ""  # Gmail msg_id OR iMessage conv_id
    message_source: str = ""  # "gmail" | "imessage"
    thread_id: str = ""
    account: str = ""
    created_at: str = ""


class SchedulerStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or SCHEDULER_DB
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    send_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    account TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS followup_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL DEFAULT '',
                    remind_after TEXT NOT NULL,
                    reminder_title TEXT NOT NULL,
                    reminder_list TEXT NOT NULL DEFAULT 'Reminders',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    fired_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_message_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    task_source TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    message_source TEXT NOT NULL,
                    thread_id TEXT NOT NULL DEFAULT '',
                    account TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    # ── Scheduled Messages ──────────────────────────────────────────────

    def schedule_message(
        self,
        source: str,
        conv_id: str,
        text: str,
        send_at: str,
        account: str = "",
    ) -> dict[str, Any]:
        """Schedule a message to be sent at a future time."""
        with self._lock:  # noqa: SIM117
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                created_at = datetime.now().isoformat()
                conn.execute(
                    """
                    INSERT INTO scheduled_messages (source, conv_id, text, send_at, status, account, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source, conv_id, text, send_at, "pending", account, created_at),
                )
                conn.commit()
                msg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                return {
                    "id": msg_id,
                    "source": source,
                    "conv_id": conv_id,
                    "text": text,
                    "send_at": send_at,
                    "status": "pending",
                    "account": account,
                    "created_at": created_at,
                }

    def cancel_scheduled(self, msg_id: int) -> bool:
        """Cancel a scheduled message."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                "UPDATE scheduled_messages SET status = 'cancelled' WHERE id = ?", (msg_id,)
            )
            conn.commit()
            return True

    def list_scheduled(self, status: str = "pending") -> list[dict[str, Any]]:
        """List scheduled messages by status."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_messages WHERE status = ? ORDER BY send_at ASC", (status,)
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "source": r[1],
                    "conv_id": r[2],
                    "text": r[3],
                    "send_at": r[4],
                    "status": r[5],
                    "account": r[6],
                    "created_at": r[7],
                    "sent_at": r[8],
                    "error": r[9],
                }
                for r in rows
            ]

    def get_due_messages(self) -> list[dict[str, Any]]:
        """Get messages that are due to send (send_at <= now)."""
        now = datetime.now().isoformat()
        with self._lock:  # noqa: SIM117
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT * FROM scheduled_messages WHERE status = 'pending' AND send_at <= ? ORDER BY send_at ASC",
                    (now,),
                ).fetchall()
                return [
                    {
                        "id": r[0],
                        "source": r[1],
                        "conv_id": r[2],
                        "text": r[3],
                        "send_at": r[4],
                        "status": r[5],
                        "account": r[6],
                        "created_at": r[7],
                        "sent_at": r[8],
                        "error": r[9],
                    }
                    for r in rows
                ]

    def mark_sent(self, msg_id: int) -> bool:
        """Mark a scheduled message as sent."""
        sent_at = datetime.now().isoformat()
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                "UPDATE scheduled_messages SET status = 'sent', sent_at = ? WHERE id = ?",
                (sent_at, msg_id),
            )
            conn.commit()
            return True

    def mark_failed(self, msg_id: int, error: str) -> bool:
        """Mark a scheduled message as failed."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                "UPDATE scheduled_messages SET status = 'failed', error = ? WHERE id = ?",
                (error, msg_id),
            )
            conn.commit()
            return True

    # ── Follow-up Reminders ────────────────────────────────────────────

    def create_followup(
        self,
        source: str,
        conv_id: str,
        thread_id: str,
        remind_after: str,
        reminder_title: str,
        reminder_list: str = "Reminders",
    ) -> dict[str, Any]:
        """Create a follow-up reminder."""
        with self._lock:  # noqa: SIM117
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                created_at = datetime.now().isoformat()
                conn.execute(
                    """
                    INSERT INTO followup_reminders
                    (source, conv_id, thread_id, remind_after, reminder_title, reminder_list, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source,
                        conv_id,
                        thread_id,
                        remind_after,
                        reminder_title,
                        reminder_list,
                        "active",
                        created_at,
                    ),
                )
                conn.commit()
                fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                return {
                    "id": fid,
                    "source": source,
                    "conv_id": conv_id,
                    "thread_id": thread_id,
                    "remind_after": remind_after,
                    "reminder_title": reminder_title,
                    "reminder_list": reminder_list,
                    "status": "active",
                    "created_at": created_at,
                }

    def cancel_followup(self, fid: int) -> bool:
        """Cancel a follow-up reminder."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("UPDATE followup_reminders SET status = 'cancelled' WHERE id = ?", (fid,))
            conn.commit()
            return True

    def list_followups(self, status: str = "active") -> list[dict[str, Any]]:
        """List follow-up reminders by status."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT * FROM followup_reminders WHERE status = ? ORDER BY remind_after ASC",
                (status,),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "source": r[1],
                    "conv_id": r[2],
                    "thread_id": r[3],
                    "remind_after": r[4],
                    "reminder_title": r[5],
                    "reminder_list": r[6],
                    "status": r[7],
                    "created_at": r[8],
                    "fired_at": r[9],
                }
                for r in rows
            ]

    def get_due_followups(self) -> list[dict[str, Any]]:
        """Get follow-up reminders that are due to fire."""
        now = datetime.now().isoformat()
        with self._lock:  # noqa: SIM117
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT * FROM followup_reminders WHERE status = 'active' AND remind_after <= ? ORDER BY remind_after ASC",
                    (now,),
                ).fetchall()
                return [
                    {
                        "id": r[0],
                        "source": r[1],
                        "conv_id": r[2],
                        "thread_id": r[3],
                        "remind_after": r[4],
                        "reminder_title": r[5],
                        "reminder_list": r[6],
                        "status": r[7],
                        "created_at": r[8],
                        "fired_at": r[9],
                    }
                    for r in rows
                ]

    def mark_followup_fired(self, fid: int) -> bool:
        """Mark a follow-up reminder as fired."""
        fired_at = datetime.now().isoformat()
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                "UPDATE followup_reminders SET status = 'fired', fired_at = ? WHERE id = ?",
                (fired_at, fid),
            )
            conn.commit()
            return True

    def mark_followup_replied(self, fid: int) -> bool:
        """Mark a follow-up reminder as replied."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("UPDATE followup_reminders SET status = 'replied' WHERE id = ?", (fid,))
            conn.commit()
            return True

    # ── Task↔Message Links ────────────────────────────────────────────

    def link_task(
        self,
        task_id: str,
        task_source: str,
        message_id: str,
        message_source: str,
        thread_id: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        """Create a link between a task and a message."""
        with self._lock:  # noqa: SIM117
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                created_at = datetime.now().isoformat()
                conn.execute(
                    """
                    INSERT INTO task_message_links
                    (task_id, task_source, message_id, message_source, thread_id, account, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        task_source,
                        message_id,
                        message_source,
                        thread_id,
                        account,
                        created_at,
                    ),
                )
                conn.commit()
                link_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                return {
                    "id": link_id,
                    "task_id": task_id,
                    "task_source": task_source,
                    "message_id": message_id,
                    "message_source": message_source,
                    "thread_id": thread_id,
                    "account": account,
                    "created_at": created_at,
                }

    def unlink_task(self, link_id: int) -> bool:
        """Delete a task↔message link."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("DELETE FROM task_message_links WHERE id = ?", (link_id,))
            conn.commit()
            return True

    def links_for_message(self, message_id: str, message_source: str) -> list[dict[str, Any]]:
        """Get all task links for a message."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT * FROM task_message_links WHERE message_id = ? AND message_source = ?",
                (message_id, message_source),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "task_id": r[1],
                    "task_source": r[2],
                    "message_id": r[3],
                    "message_source": r[4],
                    "thread_id": r[5],
                    "account": r[6],
                    "created_at": r[7],
                }
                for r in rows
            ]

    def links_for_task(self, task_id: str, task_source: str) -> list[dict[str, Any]]:
        """Get all message links for a task."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT * FROM task_message_links WHERE task_id = ? AND task_source = ?",
                (task_id, task_source),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "task_id": r[1],
                    "task_source": r[2],
                    "message_id": r[3],
                    "message_source": r[4],
                    "thread_id": r[5],
                    "account": r[6],
                    "created_at": r[7],
                }
                for r in rows
            ]
