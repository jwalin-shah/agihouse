"""Action registry — what the agent can actually do.

Live actions touch the world. Dry-run actions log + propose to the lens but
never fire externally. The split keeps demos safe: the only live verb is
sending iMessage to allow-listed handles. Everything else is auditable.

All actions append to actions_log table alongside audit.log so the trust story
is one append-only file: every proposal, every fire, every suppression.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from imessage_send import send as _imsg_send
from output import notify

# Inbox is on sys.path via trigger_server bootstrap. Real macOS Reminders +
# iMessage live there — no OAuth, just AppleScript / local sqlite.
try:
    from services import (  # type: ignore[import-not-found]
        reminders_list as _inbox_reminders_list,
        reminder_create as _inbox_reminder_create,
    )
except Exception as _e:  # pragma: no cover
    _inbox_reminders_list = None
    _inbox_reminder_create = None

STATE_DB = Path(__file__).parent / "state.db"
DEMO_LIVE_IMESSAGE_HANDLES = {"+15551234567", "+15551234568"}

def _live_imessage_handles() -> set[str]:
    raw = os.environ.get("AGIHOUSE_LIVE_IMESSAGE_HANDLES", "")
    return DEMO_LIVE_IMESSAGE_HANDLES | {h.strip() for h in raw.split(",") if h.strip()}


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(STATE_DB, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            action TEXT,
            record TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            due TEXT,
            created REAL,
            real INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            when_time TEXT,
            created REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            body TEXT,
            created REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            fact TEXT,
            created REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_imessages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handle TEXT,
            text TEXT,
            send_at REAL,
            created REAL,
            status TEXT,
            result TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relationship_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            relation TEXT,
            object TEXT,
            confidence REAL,
            evidence TEXT,
            source_action TEXT,
            source_id TEXT,
            created REAL,
            updated REAL
        )
    """)
    return conn


def _log(record: dict[str, Any]) -> None:
    record["ts"] = time.time()
    with get_db() as conn:
        conn.execute("INSERT INTO actions_log (ts, action, record) VALUES (?, ?, ?)",
                     (record["ts"], record.get("action", ""), json.dumps(record)))


def recent_actions(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, ts, action, record FROM actions_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        try:
            record = json.loads(row["record"])
        except json.JSONDecodeError:
            record = {"raw": row["record"]}
        out.append({"id": row["id"], "ts": row["ts"], "action": row["action"], "record": record})
    return out


def remember_edge(
    subject: str,
    relation: str,
    object_: str,
    *,
    confidence: float = 0.6,
    evidence: str = "",
    source_action: str = "",
    source_id: str = "",
) -> dict[str, Any]:
    subject = subject.strip()
    relation = relation.strip()
    object_ = object_.strip()
    now = time.time()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, confidence FROM relationship_edges
            WHERE lower(subject) = lower(?) AND relation = ? AND lower(object) = lower(?)
            ORDER BY id DESC LIMIT 1
            """,
            (subject, relation, object_),
        ).fetchone()
        if row:
            edge_id = int(row["id"])
            new_conf = min(1.0, max(float(row["confidence"] or 0), confidence) + 0.05)
            conn.execute(
                """
                UPDATE relationship_edges
                SET confidence = ?, evidence = ?, source_action = ?, source_id = ?, updated = ?
                WHERE id = ?
                """,
                (new_conf, evidence, source_action, source_id, now, edge_id),
            )
        else:
            edge_id = int(conn.execute(
                """
                INSERT INTO relationship_edges
                    (subject, relation, object, confidence, evidence, source_action, source_id, created, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (subject, relation, object_, confidence, evidence, source_action, source_id, now, now),
            ).lastrowid)
    record = {
        "action": "remember_edge",
        "fired": True,
        "edge_id": edge_id,
        "subject": subject,
        "relation": relation,
        "object": object_,
        "confidence": confidence,
        "evidence": evidence,
        "source_action": source_action,
        "source_id": source_id,
    }
    _log(record)
    return record


def list_memory_edges(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, subject, relation, object, confidence, evidence, source_action, source_id, created, updated
            FROM relationship_edges
            ORDER BY updated DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def learn_from_proposal_feedback(
    *,
    proposal_id: str,
    action: str,
    payload: dict[str, Any],
    outcome: str,
    transcript: str = "",
) -> list[dict[str, Any]]:
    confidence = 0.78 if outcome == "confirmed" else 0.35
    relation = "confirmed_target_for" if outcome == "confirmed" else "rejected_target_for"
    evidence = transcript or json.dumps(payload, ensure_ascii=False)
    learned: list[dict[str, Any]] = []

    handle = payload.get("handle") or payload.get("to")
    if action in {"send_imessage", "schedule_imessage"} and handle:
        learned.append(remember_edge(
            "wearer",
            relation,
            str(handle),
            confidence=confidence,
            evidence=evidence[:240],
            source_action=action,
            source_id=proposal_id,
        ))
        if "demo" in str(payload.get("text", "")).lower():
            learned.append(remember_edge(
                "demo_link",
                relation,
                str(handle),
                confidence=confidence,
                evidence=evidence[:240],
                source_action=action,
                source_id=proposal_id,
            ))

    if action == "add_calendar_event" and payload.get("title"):
        learned.append(remember_edge(
            "wearer",
            relation,
            str(payload["title"]),
            confidence=confidence,
            evidence=evidence[:240],
            source_action=action,
            source_id=proposal_id,
        ))

    return learned


def _emoji_for(action: str) -> str:
    # Most emoji render fine on G2 firmware. The studio mic 🎙 (U+1F399) does
    # NOT render — kept ASCII for the transcript echo only (see audio_pipeline).
    return {
        "send_imessage": "✉️",
        "schedule_imessage": "🕒",
        "create_reminder": "🔔",
        "add_calendar_event": "📅",
        "add_note": "📝",
        "send_email": "📧",
        "list_reminders": "📋",
        "list_calendar": "🗓",
        "list_notes": "🗒",
        "remember_fact": "🧠",
        "answer_question": "💬",
    }.get(action, "•")


def _propose_to_lens(action: str, summary: str, fired: bool) -> None:
    prefix = _emoji_for(action)
    tag = "" if fired else " (dry)"
    notify(f"{prefix}{tag} {summary}"[:200])


# --- Live-or-dry actions ---------------------------------------------------

def send_imessage(handle: str, text: str) -> dict[str, Any]:
    """Send iMessage. Live only for allow-listed handles, else dry-run."""
    live_handles = _live_imessage_handles()
    fired = handle in live_handles
    summary = f"to {handle}: {text[:60]}"
    if fired:
        ok, msg = _imsg_send(handle, text)
        result = {
            "action": "send_imessage", "fired": ok, "handle": handle,
            "text": text, "error": None if ok else msg,
        }
    else:
        result = {
            "action": "send_imessage", "fired": False, "handle": handle,
            "text": text, "reason": "not in AGIHOUSE_LIVE_IMESSAGE_HANDLES",
        }
    _log(result)
    _propose_to_lens("send_imessage", summary, result["fired"])
    return result


def _parse_send_at(send_at: str | float | int) -> float:
    if isinstance(send_at, (float, int)):
        return float(send_at)
    raw = str(send_at).strip()
    try:
        return float(raw)
    except ValueError:
        pass
    from datetime import datetime

    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError as exc:
        raise ValueError(f"send_at must be epoch seconds or ISO datetime: {send_at!r}") from exc


def schedule_imessage(handle: str, text: str, send_at: str | float | int) -> dict[str, Any]:
    """Store a future iMessage job. Actual send is handled by due-job runner."""
    send_ts = _parse_send_at(send_at)
    created = time.time()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO scheduled_imessages (handle, text, send_at, created, status, result)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (handle, text, send_ts, created, "scheduled", ""),
        )
        job_id = int(cur.lastrowid)
    result = {
        "action": "schedule_imessage",
        "fired": True,
        "job_id": job_id,
        "handle": handle,
        "text": text,
        "send_at": send_ts,
        "status": "scheduled",
    }
    _log(result)
    _propose_to_lens("schedule_imessage", f"to {handle} at {time.strftime('%b %d %I:%M %p', time.localtime(send_ts))}", True)
    return result


def list_scheduled_imessages(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, handle, text, send_at, created, status, result
            FROM scheduled_imessages
            ORDER BY send_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def send_due_imessages(now: float | None = None, limit: int = 10) -> list[dict[str, Any]]:
    now_ts = time.time() if now is None else float(now)
    sent: list[dict[str, Any]] = []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, handle, text, send_at
            FROM scheduled_imessages
            WHERE status = 'scheduled' AND send_at <= ?
            ORDER BY send_at ASC
            LIMIT ?
            """,
            (now_ts, limit),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE scheduled_imessages SET status = ? WHERE id = ? AND status = ?",
                ("sending", row["id"], "scheduled"),
            )

    for row in rows:
        send_result = send_imessage(row["handle"], row["text"])
        status = "sent" if send_result.get("fired") else "failed"
        record = {
            "action": "scheduled_imessage_due",
            "job_id": row["id"],
            "handle": row["handle"],
            "send_at": row["send_at"],
            "status": status,
            "send_result": send_result,
        }
        with get_db() as conn:
            conn.execute(
                "UPDATE scheduled_imessages SET status = ?, result = ? WHERE id = ?",
                (status, json.dumps(send_result), row["id"]),
            )
        _log(record)
        sent.append(record)
    return sent


def create_reminder(title: str, due: str | None = None) -> dict[str, Any]:
    """Create a real macOS Reminder via AppleScript. Falls back to local DB."""
    fired_real = False
    if _inbox_reminder_create is not None:
        try:
            fired_real = bool(_inbox_reminder_create(title=title, due_date=due or ""))
        except Exception as e:
            print(f"[actions] reminder_create failed: {e!r}")
    # Mirror to local DB for the demo audit trail regardless.
    created = time.time()
    with get_db() as conn:
        conn.execute("INSERT INTO reminders (title, due, created, real) VALUES (?, ?, ?, ?)",
                     (title, due, created, int(fired_real)))
    summary = f"{title}" + (f" by {due}" if due else "")
    result = {"action": "create_reminder", "fired": True, "title": title, "due": due, "created": created, "real": fired_real}
    _log(result)
    _propose_to_lens("create_reminder", summary, True)
    return result


def list_reminders(query: str | None = None) -> dict[str, Any]:
    """Read real macOS Reminders. Optional substring filter."""
    if _inbox_reminders_list is None:
        result = {"action": "list_reminders", "fired": False, "reason": "inbox unavailable"}
        _log(result)
        _propose_to_lens("list_reminders", "(reminders unavailable)", False)
        return result
    try:
        rems = _inbox_reminders_list(show_completed=False, limit=20)
    except Exception as e:
        result = {"action": "list_reminders", "fired": False, "reason": repr(e)}
        _log(result)
        _propose_to_lens("list_reminders", f"(error: {e})", False)
        return result
    titles = [getattr(r, "title", str(r)) for r in rems]
    if query:
        q = query.lower()
        titles = [t for t in titles if q in t.lower()]
    summary = " · ".join(titles[:5]) or "(no reminders)"
    result = {"action": "list_reminders", "fired": True, "count": len(titles),
              "titles": titles[:10]}
    _log(result)
    _propose_to_lens("list_reminders", summary, True)
    return result


def list_calendar(when: str | None = None) -> dict[str, Any]:
    """Read calendar DB sandbox. Optional date/keyword filter."""
    with get_db() as conn:
        rows = conn.execute("SELECT title, when_time FROM calendar ORDER BY created DESC").fetchall()
    items = [{"title": r["title"], "when": r["when_time"]} for r in rows]
    if when:
        q = when.lower()
        items = [it for it in items if q in (it.get("when") or "").lower()
                 or q in (it.get("title") or "").lower()]
    summary = " · ".join(f"{it['title']} {it['when']}" for it in items[:3]) or "(no events)"
    result = {"action": "list_calendar", "fired": True, "count": len(items),
              "events": items[:5]}
    _log(result)
    _propose_to_lens("list_calendar", summary, True)
    return result


def list_memories(query: str | None = None) -> dict[str, Any]:
    """Read memories table. Optional substring filter on subject or fact."""
    with get_db() as conn:
        rows = conn.execute("SELECT subject, fact FROM memories ORDER BY created DESC LIMIT 20").fetchall()
    if not rows:
        result = {"action": "list_memories", "fired": True, "count": 0, "items": []}
        _log(result)
        _propose_to_lens("remember_fact", "(no memories yet)", True)
        return result
    items = [{"subject": r["subject"], "fact": r["fact"]} for r in rows]
    if query:
        q = query.lower()
        items = [i for i in items
                 if q in (i.get("subject") or "").lower()
                 or q in (i.get("fact") or "").lower()]
    items = items[:10]
    summary = " · ".join(f"{i.get('subject', '?')}: {i.get('fact', '')[:40]}"
                         for i in items[:3]) or "(no memories)"
    result = {"action": "list_memories", "fired": True, "count": len(items),
              "items": items}
    _log(result)
    _propose_to_lens("remember_fact", summary, True)
    return result


def remember_fact(subject: str, fact: str) -> dict[str, Any]:
    """Append a durable observation to memories table, keyed by person."""
    subject = subject.strip()
    fact = fact.strip()
    created = time.time()
    with get_db() as conn:
        conn.execute("INSERT INTO memories (subject, fact, created) VALUES (?, ?, ?)",
                     (subject, fact, created))
    summary = f"{subject}: {fact[:50]}"
    result = {"action": "remember_fact", "fired": True, "subject": subject, "fact": fact, "created": created}
    _log(result)
    _propose_to_lens("remember_fact", summary, True)
    return result


def memories_for(subject: str, limit: int = 5) -> list[dict[str, Any]]:
    """Read memories filtered to a subject (case-insensitive substring)."""
    needle = f"%{subject.strip()}%"
    with get_db() as conn:
        rows = conn.execute("SELECT subject, fact, created FROM memories WHERE subject LIKE ? ORDER BY created ASC LIMIT ?",
                            (needle, limit)).fetchall()
    return [{"subject": r["subject"], "fact": r["fact"], "created": r["created"]} for r in rows]


def answer_question(question: str) -> dict[str, Any]:
    """Synthesize a one-line answer using calendar/reminders/memories as context."""
    import httpx as _httpx
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        result = {"action": "answer_question", "fired": False, "reason": "no key"}
        _log(result)
        _propose_to_lens("answer_question", "(no key)", False)
        return result
    cal = []
    rem_titles: list[str] = []
    try:
        with get_db() as conn:
            crows = conn.execute("SELECT title, when_time FROM calendar ORDER BY created DESC LIMIT 6").fetchall()
            cal = [{"title": r["title"], "when": r["when_time"]} for r in crows]
    except Exception:
        pass
    if _inbox_reminders_list is not None:
        try:
            rem_titles = [getattr(r, "title", str(r))
                          for r in _inbox_reminders_list(show_completed=False, limit=8)]
        except Exception:
            pass
    mems = []
    try:
        with get_db() as conn:
            mrows = conn.execute("SELECT subject, fact FROM memories ORDER BY created DESC LIMIT 20").fetchall()
            mems = [f"{r['subject']}: {r['fact']}" for r in mrows]
    except Exception:
        pass
    context = (
        f"calendar={cal}\n"
        f"reminders={rem_titles}\n"
        f"memories={mems}"
    )
    sys_prompt = (
        "You are an ambient assistant whispering ONE calm sentence (under 25 words) "
        "into a heads-up display. Use only facts from the provided context. If the "
        "context doesn't answer the question, say so briefly. No greetings, no emoji."
    )
    try:
        r = _httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "llama-3.1-8b-instant",
                "max_tokens": 80,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
                ],
            },
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"groq {r.status_code}: {r.text[:200]}")
        answer = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        result = {"action": "answer_question", "fired": False, "reason": repr(e)}
        _log(result)
        _propose_to_lens("answer_question", "(error)", False)
        return result
    result = {"action": "answer_question", "fired": True, "question": question, "answer": answer}
    _log(result)
    notify(f"💬 {answer[:200]}")
    return result


def list_notes(query: str | None = None) -> dict[str, Any]:
    with get_db() as conn:
        rows = conn.execute("SELECT title, body FROM notes ORDER BY created DESC").fetchall()
    items = [{"title": r["title"], "body": r["body"]} for r in rows]
    if query:
        q = query.lower()
        items = [it for it in items if q in (it.get("title") or "").lower()
                 or q in (it.get("body") or "").lower()]
    titles = [it.get("title", "?") for it in items]
    summary = " · ".join(titles[:5]) or "(no notes)"
    result = {"action": "list_notes", "fired": True, "count": len(titles),
              "titles": titles[:10]}
    _log(result)
    _propose_to_lens("list_notes", summary, True)
    return result


def add_calendar_event(title: str, when: str) -> dict[str, Any]:
    created = time.time()
    with get_db() as conn:
        conn.execute("INSERT INTO calendar (title, when_time, created) VALUES (?, ?, ?)",
                     (title, when, created))
    summary = f"{title} @ {when}"
    result = {"action": "add_calendar_event", "fired": True, "title": title, "when": when, "created": created}
    _log(result)
    _propose_to_lens("add_calendar_event", summary, True)
    return result


def add_note(title: str, body: str = "") -> dict[str, Any]:
    created = time.time()
    with get_db() as conn:
        conn.execute("INSERT INTO notes (title, body, created) VALUES (?, ?, ?)",
                     (title, body, created))
    summary = title
    result = {"action": "add_note", "fired": True, "title": title, "body": body, "created": created}
    _log(result)
    _propose_to_lens("add_note", summary, True)
    return result


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Always dry-run — never actually sends."""
    result = {
        "action": "send_email", "fired": False, "to": to,
        "subject": subject, "body": body, "reason": "email always dry-run",
    }
    _log(result)
    _propose_to_lens("send_email", f"to {to}: {subject}", False)
    return result


def lookup_contact(name: str) -> dict[str, Any] | None:
    """Read-only resolution against contacts.json seed."""
    path = Path(__file__).parent / "contacts.json"
    if not path.exists():
        return None
    needle = name.lower().strip()
    for c in json.loads(path.read_text()):
        if needle in c.get("name", "").lower():
            return c
        for alias in c.get("aliases", []):
            if needle in alias.lower():
                return c
    return None


# --- Dispatcher ------------------------------------------------------------

DISPATCH = {
    "send_imessage": send_imessage,
    "schedule_imessage": schedule_imessage,
    "create_reminder": create_reminder,
    "add_calendar_event": add_calendar_event,
    "add_note": add_note,
    "send_email": send_email,
    "list_reminders": list_reminders,
    "list_calendar": list_calendar,
    "list_notes": list_notes,
    "remember_fact": remember_fact,
    "answer_question": answer_question,
    "list_memories": list_memories,
}


def dispatch(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    fn = DISPATCH.get(action)
    if fn is None:
        result = {"action": action, "fired": False, "reason": "unknown action"}
        _log(result)
        return result
    try:
        return fn(**payload)
    except TypeError as e:
        result = {"action": action, "fired": False, "reason": f"bad payload: {e}"}
        _log(result)
        return result
