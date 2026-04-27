"""Background watcher: polls macOS Messages chat.db directly for new
inbound iMessages and pushes each one to notify() → glasses HUD.

Self-contained: reads ~/Library/Messages/chat.db via sqlite3. The Python
binary running this needs Full Disk Access (granted to your usual Python /
uv binary if your inbox project's iMessage features are working).

We prime with the current max(message.rowid) so we never dump history on
boot — only NEW inbound messages after start are surfaced.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

from output import notify

IMSG_DB = Path.home() / "Library/Messages/chat.db"
_POLL_SECONDS = 3.0
_started = False
_lock = threading.Lock()


def _connect_ro() -> sqlite3.Connection:
    # immutable=1 read-only mode dodges lock contention with Messages.app
    uri = f"file:{IMSG_DB}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def _max_rowid(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(rowid), 0) FROM message")
    return int(cur.fetchone()[0] or 0)


def _new_inbound_messages(conn: sqlite3.Connection, after_rowid: int) -> list[tuple[int, str, str]]:
    """Return list of (rowid, sender_handle, text) for new INBOUND messages."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.rowid, h.id, m.text
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.rowid
        WHERE m.rowid > ?
          AND m.is_from_me = 0
          AND m.text IS NOT NULL
          AND length(m.text) > 0
        ORDER BY m.rowid ASC
        LIMIT 20
        """,
        (after_rowid,),
    )
    return [(int(r[0]), str(r[1] or "Unknown"), str(r[2] or "")) for r in cur.fetchall()]


def _resolve_name(handle: str) -> str:
    """Best-effort name lookup against contacts.json. Falls back to handle."""
    try:
        import json
        contacts = json.loads((Path(__file__).parent / "contacts.json").read_text())
    except Exception:
        return handle
    needle = handle.strip().lower()
    for c in contacts:
        phone = str(c.get("phone") or "").strip().lower()
        email = str(c.get("email") or "").strip().lower()
        if needle and (needle == phone or needle == email):
            return str(c.get("name") or handle)
    return handle


def _loop() -> None:
    last_rowid = 0
    primed = False
    while True:
        try:
            with _connect_ro() as conn:
                if not primed:
                    last_rowid = _max_rowid(conn)
                    primed = True
                    print(f"[imsg-watcher] primed at rowid={last_rowid}", file=sys.stderr)
                else:
                    rows = _new_inbound_messages(conn, last_rowid)
                    for rid, handle, text in rows:
                        name = _resolve_name(handle)
                        snippet = text.strip().replace("\n", " ")
                        if len(snippet) > 140:
                            snippet = snippet[:137] + "..."
                        notify(f"📩 {name}: {snippet}", speak=False)
                        last_rowid = max(last_rowid, rid)
        except sqlite3.OperationalError as e:
            print(f"[imsg-watcher] sqlite locked/missing: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[imsg-watcher] loop error: {e!r}", file=sys.stderr)
        time.sleep(_POLL_SECONDS)


def start_watcher() -> bool:
    """Spawn the background poller. Idempotent."""
    global _started
    with _lock:
        if _started:
            return False
        if not IMSG_DB.exists():
            print(f"[imsg-watcher] {IMSG_DB} not found — skipping", file=sys.stderr)
            return False
        t = threading.Thread(target=_loop, name="imsg-watcher", daemon=True)
        t.start()
        _started = True
        print("[imsg-watcher] started", file=sys.stderr)
        return True


if __name__ == "__main__":
    start_watcher()
    while True:
        time.sleep(60)
