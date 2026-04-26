from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
DEFAULT_INDEX_DB = BASE_DIR / ".inbox_index.sqlite3"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _json_loads(value: str | None) -> object:
    return json.loads(value or "{}")


def _coalesce_str(value: object | None) -> str:
    return "" if value is None else str(value)


@dataclass
class IndexedItem:
    source: str
    account: str
    external_id: str
    thread_id: str
    kind: str
    created_at: str
    updated_at: str
    ingested_at: str
    sender: str
    recipients_json: str
    subject: str
    snippet: str
    body_text: str
    body_hash: str
    labels_json: str
    raw_pointer: str
    is_deleted: int = 0
    is_read: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MessageIndexStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_INDEX_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS sync_state (
                    source TEXT NOT NULL,
                    account TEXT NOT NULL,
                    checkpoint_type TEXT NOT NULL,
                    checkpoint_value TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT NOT NULL DEFAULT '',
                    last_full_sync_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_run_started_at TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source, account)
                );

                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    account TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    sender TEXT NOT NULL DEFAULT '',
                    recipients_json TEXT NOT NULL DEFAULT '[]',
                    subject TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL DEFAULT '',
                    body_text TEXT NOT NULL DEFAULT '',
                    body_hash TEXT NOT NULL DEFAULT '',
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    raw_pointer TEXT NOT NULL DEFAULT '',
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (source, account, external_id)
                );

                CREATE INDEX IF NOT EXISTS idx_items_thread
                    ON items(source, account, thread_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_items_created
                    ON items(source, account, created_at DESC);

                CREATE TABLE IF NOT EXISTS threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    account TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    latest_item_at TEXT NOT NULL,
                    latest_external_id TEXT NOT NULL,
                    latest_sender TEXT NOT NULL DEFAULT '',
                    latest_subject TEXT NOT NULL DEFAULT '',
                    latest_snippet TEXT NOT NULL DEFAULT '',
                    participant_fingerprint TEXT NOT NULL DEFAULT '',
                    participants_json TEXT NOT NULL DEFAULT '[]',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    unread_count INTEGER NOT NULL DEFAULT 0,
                    human_score REAL NOT NULL DEFAULT 0,
                    noise_class TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    urgency TEXT NOT NULL DEFAULT '',
                    actionability TEXT NOT NULL DEFAULT '',
                    needs_reply INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    open_loop TEXT NOT NULL DEFAULT '',
                    last_summary_version TEXT NOT NULL DEFAULT 'v1',
                    updated_at TEXT NOT NULL,
                    UNIQUE (source, account, thread_id)
                );

                CREATE INDEX IF NOT EXISTS idx_threads_latest
                    ON threads(source, account, latest_item_at DESC);

                CREATE INDEX IF NOT EXISTS idx_threads_actionability
                    ON threads(actionability, needs_reply, urgency, latest_item_at DESC);

                CREATE TABLE IF NOT EXISTS sender_stats (
                    email TEXT PRIMARY KEY,
                    thread_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sync_state)").fetchall()}
        if "status" not in columns:
            conn.execute("ALTER TABLE sync_state ADD COLUMN status TEXT NOT NULL DEFAULT 'idle'")
        if "last_run_started_at" not in columns:
            conn.execute(
                "ALTER TABLE sync_state ADD COLUMN last_run_started_at TEXT NOT NULL DEFAULT ''"
            )
        if "metadata_json" not in columns:
            conn.execute(
                "ALTER TABLE sync_state ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
            )

    def upsert_item(self, item: IndexedItem) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO items (
                    source, account, external_id, thread_id, kind, created_at, updated_at,
                    ingested_at, sender, recipients_json, subject, snippet, body_text,
                    body_hash, labels_json, raw_pointer, is_deleted, is_read
                )
                VALUES (
                    :source, :account, :external_id, :thread_id, :kind, :created_at, :updated_at,
                    :ingested_at, :sender, :recipients_json, :subject, :snippet, :body_text,
                    :body_hash, :labels_json, :raw_pointer, :is_deleted, :is_read
                )
                ON CONFLICT(source, account, external_id) DO UPDATE SET
                    thread_id=excluded.thread_id,
                    kind=excluded.kind,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    ingested_at=excluded.ingested_at,
                    sender=excluded.sender,
                    recipients_json=excluded.recipients_json,
                    subject=excluded.subject,
                    snippet=excluded.snippet,
                    body_text=excluded.body_text,
                    body_hash=excluded.body_hash,
                    labels_json=excluded.labels_json,
                    raw_pointer=excluded.raw_pointer,
                    is_deleted=excluded.is_deleted,
                    is_read=excluded.is_read
                """,
                item.to_dict(),
            )

    def set_sync_state(
        self,
        *,
        source: str,
        account: str,
        checkpoint_type: str,
        checkpoint_value: str,
        last_error: str = "",
        full_sync: bool = False,
        status: str = "idle",
        last_run_started_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _utcnow()
        metadata_json = _json_dumps(metadata or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    source, account, checkpoint_type, checkpoint_value, last_success_at,
                    last_full_sync_at, status, last_run_started_at, metadata_json, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, account) DO UPDATE SET
                    checkpoint_type=excluded.checkpoint_type,
                    checkpoint_value=excluded.checkpoint_value,
                    last_success_at=excluded.last_success_at,
                    last_full_sync_at=CASE
                        WHEN excluded.last_full_sync_at != '' THEN excluded.last_full_sync_at
                        ELSE sync_state.last_full_sync_at
                    END,
                    status=excluded.status,
                    last_run_started_at=CASE
                        WHEN excluded.last_run_started_at != '' THEN excluded.last_run_started_at
                        ELSE sync_state.last_run_started_at
                    END,
                    metadata_json=excluded.metadata_json,
                    last_error=excluded.last_error
                """,
                (
                    source,
                    account,
                    checkpoint_type,
                    checkpoint_value,
                    now,
                    now if full_sync else "",
                    status,
                    last_run_started_at,
                    metadata_json,
                    last_error,
                ),
            )

    def mark_sync_started(
        self,
        *,
        source: str,
        account: str,
        checkpoint_type: str,
        checkpoint_value: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.set_sync_state(
            source=source,
            account=account,
            checkpoint_type=checkpoint_type,
            checkpoint_value=checkpoint_value,
            status="running",
            last_run_started_at=_utcnow(),
            metadata=metadata,
        )

    def update_sync_progress(
        self,
        *,
        source: str,
        account: str,
        checkpoint_type: str,
        checkpoint_value: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.set_sync_state(
            source=source,
            account=account,
            checkpoint_type=checkpoint_type,
            checkpoint_value=checkpoint_value,
            status="running",
            metadata=metadata,
        )

    def record_sync_error(self, *, source: str, account: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    source, account, checkpoint_type, checkpoint_value, last_success_at,
                    last_full_sync_at, status, last_run_started_at, metadata_json, last_error
                )
                VALUES (?, ?, '', '', '', '', 'error', '', '{}', ?)
                ON CONFLICT(source, account) DO UPDATE SET
                    status='error',
                    last_error=excluded.last_error
                """,
                (source, account, error),
            )

    def get_sync_state(self, source: str, account: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE source = ? AND account = ?",
                (source, account),
            ).fetchone()
        return self._sync_row_to_dict(row) if row else None

    def list_sync_states(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sync_state ORDER BY source, account").fetchall()
        return [self._sync_row_to_dict(row) for row in rows]

    def index_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        return {"items": int(items), "threads": int(threads)}

    def _sync_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = _json_loads(str(data.get("metadata_json") or "{}"))
        return data

    def rebuild_threads(self, *, source: str | None = None, account: str | None = None) -> int:
        predicates: list[str] = []
        params: list[object] = []
        if source:
            predicates.append("source = ?")
            params.append(source)
        if account:
            predicates.append("account = ?")
            params.append(account)
        where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""

        with self._connect() as conn:
            _q = f"SELECT * FROM items {where_clause} ORDER BY source, account, thread_id, created_at, id"  # nosec B608
            rows = conn.execute(_q, params).fetchall()

            grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
            for row in rows:
                key = (str(row["source"]), str(row["account"]), str(row["thread_id"]))
                grouped.setdefault(key, []).append(row)

            now = _utcnow()
            for (row_source, row_account, thread_id), items in grouped.items():
                latest = items[-1]
                participants = sorted(
                    {
                        sender
                        for sender in [str(item["sender"]) for item in items]
                        if sender and sender != "Me"
                    }
                )
                unread_count = sum(int(item["is_read"] == 0) for item in items)
                human_score = _human_score(
                    latest_sender=str(latest["sender"]),
                    latest_subject=str(latest["subject"]),
                    latest_body=str(latest["body_text"]),
                )
                noise_class = _noise_class(
                    latest_sender=str(latest["sender"]),
                    subject=str(latest["subject"]),
                    body=str(latest["body_text"]),
                )
                topic = _topic(subject=str(latest["subject"]), body=str(latest["body_text"]))
                urgency = _urgency(subject=str(latest["subject"]), body=str(latest["body_text"]))
                actionability = _actionability(
                    human_score=human_score,
                    noise_class=noise_class,
                    urgency=urgency,
                    topic=topic,
                )
                needs_reply = int(
                    actionability in {"reply", "review"} and str(latest["sender"]) != "Me"
                )
                open_loop = _open_loop(topic=topic, actionability=actionability, latest=latest)
                summary = _summary(latest=latest, topic=topic, actionability=actionability)
                conn.execute(
                    """
                    INSERT INTO threads (
                        source, account, thread_id, latest_item_at, latest_external_id,
                        latest_sender, latest_subject, latest_snippet, participant_fingerprint,
                        participants_json, message_count, unread_count, human_score, noise_class,
                        topic, urgency, actionability, needs_reply, summary, open_loop,
                        last_summary_version, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, account, thread_id) DO UPDATE SET
                        latest_item_at=excluded.latest_item_at,
                        latest_external_id=excluded.latest_external_id,
                        latest_sender=excluded.latest_sender,
                        latest_subject=excluded.latest_subject,
                        latest_snippet=excluded.latest_snippet,
                        participant_fingerprint=excluded.participant_fingerprint,
                        participants_json=excluded.participants_json,
                        message_count=excluded.message_count,
                        unread_count=excluded.unread_count,
                        human_score=excluded.human_score,
                        noise_class=excluded.noise_class,
                        topic=excluded.topic,
                        urgency=excluded.urgency,
                        actionability=excluded.actionability,
                        needs_reply=excluded.needs_reply,
                        summary=excluded.summary,
                        open_loop=excluded.open_loop,
                        last_summary_version=excluded.last_summary_version,
                        updated_at=excluded.updated_at
                    """,
                    (
                        row_source,
                        row_account,
                        thread_id,
                        str(latest["created_at"]),
                        str(latest["external_id"]),
                        str(latest["sender"]),
                        str(latest["subject"]),
                        str(latest["snippet"]),
                        "|".join(participants),
                        _json_dumps(participants),
                        len(items),
                        unread_count,
                        human_score,
                        noise_class,
                        topic,
                        urgency,
                        actionability,
                        needs_reply,
                        summary,
                        open_loop,
                        "v1",
                        now,
                    ),
                )

            if grouped:
                keys = list(grouped.keys())
                deletion_scope_predicates: list[str] = []
                deletion_scope_params: list[object] = []
                if source:
                    deletion_scope_predicates.append("source = ?")
                    deletion_scope_params.append(source)
                if account:
                    deletion_scope_predicates.append("account = ?")
                    deletion_scope_params.append(account)
                scope_clause = (
                    " AND ".join(deletion_scope_predicates) if deletion_scope_predicates else "1=1"
                )
                keep_clause = " OR ".join(
                    "(source = ? AND account = ? AND thread_id = ?)" for _ in keys
                )
                keep_params: list[object] = [value for key in keys for value in key]
                conn.execute(
                    f"DELETE FROM threads WHERE ({scope_clause}) AND NOT ({keep_clause})",  # nosec: B608
                    deletion_scope_params + keep_params,
                )
            elif source and account:
                conn.execute(
                    "DELETE FROM threads WHERE source = ? AND account = ?", (source, account)
                )
            elif source:
                conn.execute("DELETE FROM threads WHERE source = ?", (source,))
            elif account:
                conn.execute("DELETE FROM threads WHERE account = ?", (account,))
            else:
                conn.execute("DELETE FROM threads")

        return len(grouped)

    def list_threads(
        self,
        *,
        limit: int = 25,
        actionable_only: bool = False,
        newest_only: bool = False,
        actions: tuple[str, ...] | None = None,
        needs_reply: bool | None = None,
        has_open_loop: bool | None = None,
        sort_mode: str = "priority",
    ) -> list[dict[str, object]]:
        predicates: list[str] = []
        params: list[object] = []
        if actionable_only:
            predicates.append("actionability IN ('reply', 'review', 'track')")
        if newest_only:
            predicates.append("latest_item_at >= datetime('now', '-7 day')")
        if actions:
            predicates.append(f"actionability IN ({','.join('?' for _ in actions)})")
            params.extend(actions)
        if needs_reply is not None:
            predicates.append("needs_reply = ?")
            params.append(1 if needs_reply else 0)
        if has_open_loop is not None:
            predicates.append("(open_loop != '') = ?")
            params.append(1 if has_open_loop else 0)
        where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        if sort_mode == "recent":
            order_clause = "latest_item_at DESC"
        else:
            order_clause = """
                CASE urgency
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                needs_reply DESC,
                latest_item_at DESC
            """
        params.append(limit)
        with self._connect() as conn:
            _q = f"SELECT * FROM threads {where_clause} ORDER BY {order_clause} LIMIT ?"  # nosec B608
            rows = conn.execute(_q, params).fetchall()
        return [self._thread_row_to_dict(row) for row in rows]

    def _thread_row_to_dict(self, row: sqlite3.Row) -> dict[str, object]:
        keys = list(row.keys())
        return {key: (_json_loads(row[key]) if key.endswith("_json") else row[key]) for key in keys}


def _human_score(*, latest_sender: str, latest_subject: str, latest_body: str) -> float:
    sender = latest_sender.lower()
    haystack = f"{latest_subject}\n{latest_body}".lower()
    score = 0.2
    if latest_sender and latest_sender != "Me":
        score += 0.3
    if "noreply" not in sender and "no-reply" not in sender:
        score += 0.2
    if "unsubscribe" not in haystack and "verification code" not in haystack:
        score += 0.2
    if not sender.isdigit():
        score += 0.1
    return min(score, 1.0)


def _noise_class(*, latest_sender: str, subject: str, body: str) -> str:
    haystack = f"{subject}\n{body}".lower()
    sender = latest_sender.lower()
    if "verification code" in haystack or "otp" in haystack:
        return "otp"
    if "unsubscribe" in haystack or "job alert" in haystack:
        return "newsletter"
    if "appointment" in haystack or "your appt" in haystack:
        return "appointment"
    if "survey" in haystack or "thank you for your most recent visit" in haystack:
        return "survey"
    if "receipt" in haystack or "order" in haystack:
        return "receipt"
    if "login" in haystack or "security alert" in haystack:
        return "security-alert"
    if "noreply" in sender or "no-reply" in sender:
        return "automated"
    return ""


def _topic(*, subject: str, body: str) -> str:
    haystack = f"{subject}\n{body}".lower()
    if any(token in haystack for token in ("interview", "recruit", "opportunity", "consulting")):
        return "opportunity"
    if any(token in haystack for token in ("appointment", "billing", "quest", "cvs", "health")):
        return "health-admin"
    if any(token in haystack for token in ("apartment", "tour", "lease", "housing")):
        return "housing"
    if any(token in haystack for token in ("login", "security", "verification")):
        return "security"
    return "general"


def _urgency(*, subject: str, body: str) -> str:
    haystack = f"{subject}\n{body}".lower()
    if any(
        token in haystack for token in ("action required", "urgent", "today", "verify", "security")
    ):
        return "high"
    if any(token in haystack for token in ("appointment", "reply", "follow up", "opportunity")):
        return "medium"
    return "low"


def _sender_freq_score(reply_count: int, thread_count: int) -> float:
    if thread_count == 0:
        return 0.0
    reply_rate = reply_count / thread_count
    volume_boost = min(math.log1p(reply_count) / math.log1p(10), 1.0)
    return round(reply_rate * 0.7 + volume_boost * 0.3, 3)


def _actionability(
    *, human_score: float, noise_class: str, urgency: str, topic: str, sender_freq: float = 0.0
) -> str:
    if noise_class in {"otp", "receipt", "survey"}:
        return "ignore"
    if topic in {"security", "health-admin"} and urgency in {"high", "medium"}:
        return "track"
    if human_score >= 0.7 or sender_freq >= 0.5:
        return "reply"
    if noise_class in {"newsletter", "automated"}:
        return "archive"
    if topic == "opportunity":
        return "review"
    return "track"


def _open_loop(*, topic: str, actionability: str, latest: sqlite3.Row) -> str:
    if actionability == "reply":
        return f"Reply to {latest['sender'] or 'sender'}"
    if topic == "health-admin":
        return "Track appointment or billing follow-up"
    if topic == "security":
        return "Confirm whether activity was expected"
    if actionability == "review":
        return "Review opportunity details"
    return ""


def _summary(*, latest: sqlite3.Row, topic: str, actionability: str) -> str:
    title = _coalesce_str(latest["subject"]) or _coalesce_str(latest["snippet"])
    sender = _coalesce_str(latest["sender"]) or "Unknown sender"
    if title:
        return f"{sender}: {title} [{topic}/{actionability}]"
    return f"{sender} [{topic}/{actionability}]"
