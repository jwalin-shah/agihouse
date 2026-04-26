from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DEFAULT_MEMORY_DB = BASE_DIR / ".inbox_memory.sqlite3"


@dataclass
class MemoryEntry:
    id: int
    memory_type: str
    subject: str
    content: str
    source: str
    confidence: float
    status: str
    created_at: str
    updated_at: str
    expires_at: str | None = None
    metadata: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_MEMORY_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_lookup
                ON memory_entries(memory_type, subject, status, created_at DESC)
                """
            )

    def save_entry(
        self,
        memory_type: str,
        subject: str,
        content: str,
        source: str = "manual",
        confidence: float = 0.8,
        status: str = "active",
        expires_at: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        now = _utcnow()
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_entries (
                    memory_type,
                    subject,
                    content,
                    source,
                    confidence,
                    status,
                    created_at,
                    updated_at,
                    expires_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_type,
                    subject,
                    content,
                    source,
                    confidence,
                    status,
                    now,
                    now,
                    expires_at,
                    metadata_json,
                ),
            )
            entry_id = int(cursor.lastrowid)
        return self.get_entry(entry_id)

    def get_entry(self, entry_id: int) -> dict[str, object]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM memory_entries
                WHERE id = ?
                """,
                (entry_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Memory entry {entry_id} not found")
        return self._row_to_entry(row).to_dict()

    def query_entries(
        self,
        query: str = "",
        memory_type: str = "",
        subject: str = "",
        status: str = "",
        limit: int = 10,
    ) -> list[dict[str, object]]:
        predicates = ["(expires_at IS NULL OR expires_at > ?)"]
        params: list[object] = [_utcnow()]

        if memory_type:
            predicates.append("memory_type = ?")
            params.append(memory_type)
        if subject:
            predicates.append("subject = ?")
            params.append(subject)
        if status:
            predicates.append("status = ?")
            params.append(status)
        if query:
            like = f"%{query}%"
            predicates.append("(subject LIKE ? OR content LIKE ?)")
            params.extend([like, like])

        where_clause = " AND ".join(predicates) if predicates else "1=1"
        params.append(limit)
        # NOTE: where_clause is constructed from fixed predicate strings (not user input),
        # so concatenation here is safe and not a SQL injection vector.
        sql = (
            "SELECT * FROM memory_entries WHERE "
            + where_clause  # nosec: B608
            + " ORDER BY updated_at DESC, id DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(row).to_dict() for row in rows]

    def list_open_commitments(self, limit: int = 25) -> list[dict[str, object]]:
        return self.query_entries(memory_type="commitment", status="open", limit=limit)

    def update_entry(self, entry_id: int, **kwargs) -> dict[str, object]:
        allowed_fields = {"subject", "content", "confidence", "status", "expires_at", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return self.get_entry(entry_id)

        set_clause = ", ".join([f"{k} = ?" for k in updates])
        set_clause += ", updated_at = ?"
        values = list(updates.values()) + [_utcnow(), entry_id]

        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE memory_entries SET {set_clause} WHERE id = ?",  # nosec: B608
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Memory entry {entry_id} not found")
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
            return cursor.rowcount > 0

    def close_commitment(self, entry_id: int) -> dict[str, object]:
        return self.update_entry(entry_id, status="closed")

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=int(row["id"]),
            memory_type=str(row["memory_type"]),
            subject=str(row["subject"]),
            content=str(row["content"]),
            source=str(row["source"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            expires_at=str(row["expires_at"]) if row["expires_at"] else None,
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
