"""Action registry — what the agent can actually do.

Live actions touch the world. Dry-run actions log + propose to the lens but
never fire externally. The split keeps demos safe: the only live verb is
sending iMessage to allow-listed handles. Everything else is auditable.

All actions append to actions.jsonl alongside audit.log so the trust story
is one append-only file: every proposal, every fire, every suppression.
"""

from __future__ import annotations

import json
import os
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

ACTIONS_LOG = Path(__file__).parent / "actions.jsonl"

# iMessage handles allowed to receive *real* messages from the agent.
# Anything not in this set gets dry-run'd. Add via env or by editing here.
LIVE_IMESSAGE_HANDLES: set[str] = set()  # e.g. {"+15551234567"}


def _log(record: dict[str, Any]) -> None:
    record["ts"] = time.time()
    with ACTIONS_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _emoji_for(action: str) -> str:
    # Most emoji render fine on G2 firmware. The studio mic 🎙 (U+1F399) does
    # NOT render — kept ASCII for the transcript echo only (see audio_pipeline).
    return {
        "send_imessage": "✉️",
        "create_reminder": "⏰",
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
    fired = handle in LIVE_IMESSAGE_HANDLES
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
            "text": text, "reason": "not in LIVE_IMESSAGE_HANDLES",
        }
    _log(result)
    _propose_to_lens("send_imessage", summary, result["fired"])
    return result


def create_reminder(title: str, due: str | None = None) -> dict[str, Any]:
    """Create a real macOS Reminder via AppleScript. Falls back to local JSON."""
    fired_real = False
    if _inbox_reminder_create is not None:
        try:
            fired_real = bool(_inbox_reminder_create(title=title, due_date=due or ""))
        except Exception as e:
            print(f"[actions] reminder_create failed: {e!r}")
    # Mirror to local JSON for the demo audit trail regardless.
    path = Path(__file__).parent / "reminders.json"
    items = json.loads(path.read_text()) if path.exists() else []
    rec = {"title": title, "due": due, "created": time.time(), "real": fired_real}
    items.append(rec)
    path.write_text(json.dumps(items, indent=2))
    summary = f"{title}" + (f" by {due}" if due else "")
    result = {"action": "create_reminder", "fired": True, **rec}
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
    """Read calendar.json sandbox. Optional date/keyword filter."""
    path = Path(__file__).parent / "calendar.json"
    items = json.loads(path.read_text()) if path.exists() else []
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
    """Read memories.jsonl. Optional substring filter on subject or fact."""
    path = Path(__file__).parent / "memories.jsonl"
    if not path.exists():
        result = {"action": "list_memories", "fired": True, "count": 0, "items": []}
        _log(result)
        _propose_to_lens("remember_fact", "(no memories yet)", True)
        return result
    items: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    if query:
        q = query.lower()
        items = [i for i in items
                 if q in (i.get("subject") or "").lower()
                 or q in (i.get("fact") or "").lower()]
    items = items[-10:]
    summary = " · ".join(f"{i.get('subject', '?')}: {i.get('fact', '')[:40]}"
                         for i in items[:3]) or "(no memories)"
    result = {"action": "list_memories", "fired": True, "count": len(items),
              "items": items}
    _log(result)
    _propose_to_lens("remember_fact", summary, True)
    return result


def remember_fact(subject: str, fact: str) -> dict[str, Any]:
    """Append a durable observation to memories.jsonl, keyed by person."""
    path = Path(__file__).parent / "memories.jsonl"
    rec = {"subject": subject.strip(), "fact": fact.strip(), "created": time.time()}
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    summary = f"{subject}: {fact[:50]}"
    result = {"action": "remember_fact", "fired": True, **rec}
    _log(result)
    _propose_to_lens("remember_fact", summary, True)
    return result


def memories_for(subject: str, limit: int = 5) -> list[dict[str, Any]]:
    """Read memories filtered to a subject (case-insensitive substring)."""
    path = Path(__file__).parent / "memories.jsonl"
    if not path.exists():
        return []
    needle = subject.lower().strip()
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if needle in (rec.get("subject") or "").lower():
            out.append(rec)
    return out[-limit:]


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
        cal_path = Path(__file__).parent / "calendar.json"
        if cal_path.exists():
            cal = json.loads(cal_path.read_text())[:6]
    except Exception:
        pass
    if _inbox_reminders_list is not None:
        try:
            rem_titles = [getattr(r, "title", str(r))
                          for r in _inbox_reminders_list(show_completed=False, limit=8)]
        except Exception:
            pass
    mem_path = Path(__file__).parent / "memories.jsonl"
    mems: list[str] = []
    if mem_path.exists():
        for line in mem_path.read_text().splitlines()[-20:]:
            try:
                m = json.loads(line)
                mems.append(f"{m.get('subject', '?')}: {m.get('fact', '')}")
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
    path = Path(__file__).parent / "notes.json"
    items = json.loads(path.read_text()) if path.exists() else []
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
    path = Path(__file__).parent / "calendar.json"
    items = json.loads(path.read_text()) if path.exists() else []
    rec = {"title": title, "when": when, "created": time.time()}
    items.append(rec)
    path.write_text(json.dumps(items, indent=2))
    summary = f"{title} @ {when}"
    result = {"action": "add_calendar_event", "fired": True, **rec}
    _log(result)
    _propose_to_lens("add_calendar_event", summary, True)
    return result


def add_note(title: str, body: str = "") -> dict[str, Any]:
    path = Path(__file__).parent / "notes.json"
    items = json.loads(path.read_text()) if path.exists() else []
    rec = {"title": title, "body": body, "created": time.time()}
    items.append(rec)
    path.write_text(json.dumps(items, indent=2))
    summary = title
    result = {"action": "add_note", "fired": True, **rec}
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
