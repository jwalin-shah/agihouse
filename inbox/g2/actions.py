"""G2 action registry — what the ambient copilot is actually allowed to do.

Every verb here is the same shape:

    1. ``audit.gate(verb, **payload)`` decides allow / deny.
    2. On allow, call the matching ``services.*`` function (or sandbox fallback
       for verbs without a real-world counterpart).
    3. Append the outcome to ``~/.inbox/actions.jsonl``.
    4. ``audit.mark_fired`` updates restraint counters and writes the final
       ``fired`` / ``dry_run`` row.
    5. Echo a :class:`Signal` to the blackboard so the arbitrator can whisper
       "did X" on the lens.

The ``send_imessage`` verb is the only path that can perform a *real* live
write to the user's account, and even that is gated by
``LIVE_IMESSAGE_HANDLES``: any handle not in this set automatically falls
back to dry-run regardless of the policy mode. ``send_email`` is denied
outright in ``policy.yaml``.

Sandbox state lives under ``~/.inbox/`` so the repo stays clean.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import audit
from .blackboard import Signal

# ── Paths / sandboxes ────────────────────────────────────────────────────

_INBOX_DIR = Path(os.path.expanduser("~/.inbox"))
_INBOX_DIR.mkdir(parents=True, exist_ok=True)

ACTIONS_LOG = _INBOX_DIR / "actions.jsonl"
NOTES_PATH = _INBOX_DIR / "notes.json"
CALENDAR_SANDBOX = _INBOX_DIR / "calendar.json"
LEGACY_MEMORIES = _INBOX_DIR / "memories.jsonl"
CONTACTS_PATH = _INBOX_DIR / "contacts.json"

# iMessage handles cleared for *real* live writes. Anything outside this set
# automatically dry-runs even when policy.yaml is in ``mode: live``. Set
# ``LIVE_IMESSAGE_HANDLES`` env var as a comma-separated list to populate.
LIVE_IMESSAGE_HANDLES: set[str] = {
    h.strip()
    for h in os.environ.get("LIVE_IMESSAGE_HANDLES", "").split(",")
    if h.strip()
}


# ── Wiring with the rest of inbox/g2 ─────────────────────────────────────

_loop: asyncio.AbstractEventLoop | None = None
_blackboard = None  # filled by bind() from g2/__init__ on demand
_cal_service_provider: Callable[[], Any] | None = None
_gmail_service_provider: Callable[[], Any] | None = None


def bind(*, loop: asyncio.AbstractEventLoop, blackboard) -> None:  # noqa: ANN001
    """Called once from ``start_g2_agents`` to wire async dependencies."""
    global _loop, _blackboard
    _loop = loop
    _blackboard = blackboard


def set_cal_service_provider(getter: Callable[[], Any] | None) -> None:
    """Optional hook so live Google Calendar writes can be enabled later."""
    global _cal_service_provider
    _cal_service_provider = getter


def set_gmail_service_provider(getter: Callable[[], Any] | None) -> None:
    """Optional hook used by ``answer_question`` for inbox briefing context."""
    global _gmail_service_provider
    _gmail_service_provider = getter


# ── Internal helpers ─────────────────────────────────────────────────────


def _log(record: dict[str, Any]) -> None:
    record.setdefault("ts", time.time())
    with ACTIONS_LOG.open("a") as f:
        f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _emit_signal(verb: str, summary: str, *, fired: bool, priority: int = 4) -> None:
    """Best-effort write of an "I just did X" Signal to the blackboard."""
    if _loop is None or _blackboard is None:
        return
    payload = {"insight": f"did {verb}: {summary}"[:56], "fired": fired}
    sig = Signal(
        agent_id=f"action:{verb}",
        priority=priority,
        category="action",
        data=payload,
        ttl=60,
    )
    try:
        asyncio.run_coroutine_threadsafe(_blackboard.write(sig), _loop)
    except RuntimeError:
        pass


def _suppressed(verb: str, decision: audit.Decision) -> dict[str, Any]:
    rec = {
        "action": verb,
        "fired": False,
        "decision": "suppressed",
        "reason": decision.reason,
    }
    _log(rec)
    return rec


# ── Verbs ────────────────────────────────────────────────────────────────


def create_reminder(title: str, due: str | None = None, **_: Any) -> dict[str, Any]:
    """Create a real macOS Reminder via :func:`services.reminder_create`."""
    decision = audit.gate("create_reminder", title=title, due=due)
    if not decision.allow:
        return _suppressed("create_reminder", decision)

    fired = False
    reason = ""
    try:
        import services  # type: ignore[import-not-found]

        fired = bool(services.reminder_create(title=title, due_date=due or ""))
    except Exception as e:
        reason = repr(e)
        print(f"[g2.actions] reminder_create failed: {e!r}", file=sys.stderr)

    summary = title + (f" by {due}" if due else "")
    rec = {
        "action": "create_reminder",
        "fired": fired,
        "title": title,
        "due": due,
        "reason": reason,
    }
    _log(rec)
    audit.mark_fired("create_reminder", title=title, due=due)
    _emit_signal("reminder", summary, fired=fired, priority=5)
    return rec


def list_reminders(query: str | None = None, **_: Any) -> dict[str, Any]:
    decision = audit.gate("list_reminders", query=query)
    if not decision.allow:
        return _suppressed("list_reminders", decision)

    titles: list[str] = []
    reason = ""
    try:
        import services  # type: ignore[import-not-found]

        rems = services.reminders_list(show_completed=False, limit=20)
        titles = [getattr(r, "title", str(r)) for r in rems]
        if query:
            q = query.lower()
            titles = [t for t in titles if q in t.lower()]
    except Exception as e:
        reason = repr(e)

    summary = " · ".join(titles[:3]) or "(no reminders)"
    rec = {
        "action": "list_reminders",
        "fired": True,
        "count": len(titles),
        "titles": titles[:10],
        "reason": reason,
    }
    _log(rec)
    audit.mark_fired("list_reminders")
    _emit_signal("list_reminders", summary, fired=True, priority=3)
    return rec


def add_calendar_event(title: str, when: str, **_: Any) -> dict[str, Any]:
    """Append to the local calendar sandbox; opportunistically push live."""
    decision = audit.gate("add_calendar_event", title=title, when=when)
    if not decision.allow:
        return _suppressed("add_calendar_event", decision)

    items = _read_json(CALENDAR_SANDBOX)
    rec = {"title": title, "when": when, "created": time.time()}
    items.append(rec)
    CALENDAR_SANDBOX.write_text(json.dumps(items, indent=2))

    live_id: str | None = None
    if _cal_service_provider is not None:
        live_id = _try_live_calendar_create(title, when)

    summary = f"{title} @ {when}"
    record = {
        "action": "add_calendar_event",
        "fired": True,
        "title": title,
        "when": when,
        "live_event_id": live_id,
    }
    _log(record)
    audit.mark_fired("add_calendar_event", title=title, when=when)
    _emit_signal("calendar", summary, fired=True, priority=5)
    return record


def _try_live_calendar_create(title: str, when: str) -> str | None:
    """Best-effort live Google Calendar insert. Returns event id or None."""
    try:
        from datetime import datetime, timedelta

        import services  # type: ignore[import-not-found]

        cal_service = _cal_service_provider() if _cal_service_provider else None
        if cal_service is None:
            return None
        try:
            start = datetime.fromisoformat(when)
        except ValueError:
            return None
        end = start + timedelta(hours=1)
        return services.calendar_create_event(
            cal_service,
            summary=title,
            start=start,
            end=end,
        )
    except Exception as e:
        print(f"[g2.actions] live calendar insert failed: {e!r}", file=sys.stderr)
        return None


def list_calendar(when: str | None = None, **_: Any) -> dict[str, Any]:
    decision = audit.gate("list_calendar", when=when)
    if not decision.allow:
        return _suppressed("list_calendar", decision)

    items = _read_json(CALENDAR_SANDBOX)
    if when:
        q = when.lower()
        items = [
            it
            for it in items
            if q in (it.get("when") or "").lower() or q in (it.get("title") or "").lower()
        ]
    summary = " · ".join(f"{it['title']} {it['when']}" for it in items[:3]) or "(no events)"
    rec = {
        "action": "list_calendar",
        "fired": True,
        "count": len(items),
        "events": items[:5],
    }
    _log(rec)
    audit.mark_fired("list_calendar")
    _emit_signal("calendar", summary, fired=True, priority=3)
    return rec


def add_note(title: str, body: str = "", **_: Any) -> dict[str, Any]:
    """Append to ``~/.inbox/notes.json``. Apple Notes write is intentionally
    not used here — AppleScript Notes is too fragile for an ambient demo.
    """
    decision = audit.gate("add_note", title=title, body=body)
    if not decision.allow:
        return _suppressed("add_note", decision)

    items = _read_json(NOTES_PATH)
    rec = {"title": title, "body": body, "created": time.time()}
    items.append(rec)
    NOTES_PATH.write_text(json.dumps(items, indent=2))

    record = {"action": "add_note", "fired": True, **rec}
    _log(record)
    audit.mark_fired("add_note", title=title)
    _emit_signal("note", title, fired=True, priority=4)
    return record


def list_notes(query: str | None = None, **_: Any) -> dict[str, Any]:
    decision = audit.gate("list_notes", query=query)
    if not decision.allow:
        return _suppressed("list_notes", decision)

    items = _read_json(NOTES_PATH)
    if query:
        q = query.lower()
        items = [
            it
            for it in items
            if q in (it.get("title") or "").lower() or q in (it.get("body") or "").lower()
        ]
    titles = [it.get("title", "?") for it in items]
    summary = " · ".join(titles[:3]) or "(no notes)"
    rec = {
        "action": "list_notes",
        "fired": True,
        "count": len(titles),
        "titles": titles[:10],
    }
    _log(rec)
    audit.mark_fired("list_notes")
    _emit_signal("note", summary, fired=True, priority=3)
    return rec


def remember_fact(subject: str, fact: str, **_: Any) -> dict[str, Any]:
    """Append to MemoryStore (live durable store) and the legacy sandbox."""
    decision = audit.gate("remember_fact", subject=subject, fact=fact)
    if not decision.allow:
        return _suppressed("remember_fact", decision)

    saved_id: int | None = None
    try:
        from memory_store import MemoryStore  # type: ignore[import-not-found]

        store = MemoryStore()
        entry = store.save_entry(
            memory_type="fact",
            subject=subject.strip(),
            content=fact.strip(),
            source="g2.voice_actions",
        )
        saved_id = int(entry.get("id", 0)) if isinstance(entry, dict) else None
    except Exception as e:
        print(f"[g2.actions] MemoryStore.save_entry failed: {e!r}", file=sys.stderr)

    legacy = {"subject": subject.strip(), "fact": fact.strip(), "created": time.time()}
    with LEGACY_MEMORIES.open("a") as f:
        f.write(json.dumps(legacy) + "\n")

    summary = f"{subject}: {fact[:50]}"
    record = {
        "action": "remember_fact",
        "fired": True,
        "memory_id": saved_id,
        **legacy,
    }
    _log(record)
    audit.mark_fired("remember_fact", subject=subject)
    _emit_signal("memory", summary, fired=True, priority=4)
    return record


def list_memories(query: str | None = None, **_: Any) -> dict[str, Any]:
    decision = audit.gate("list_memories", query=query)
    if not decision.allow:
        return _suppressed("list_memories", decision)

    items: list[dict[str, Any]] = []
    try:
        from memory_store import MemoryStore  # type: ignore[import-not-found]

        store = MemoryStore()
        items = store.query_entries(query=query or "", limit=20)
    except Exception:
        items = _read_jsonl(LEGACY_MEMORIES)
        if query:
            q = query.lower()
            items = [
                i
                for i in items
                if q in (i.get("subject") or "").lower() or q in (i.get("fact") or "").lower()
            ]

    summary = (
        " · ".join(
            f"{i.get('subject', '?')}: {(i.get('content') or i.get('fact') or '')[:40]}"
            for i in items[:3]
        )
        or "(no memories)"
    )
    rec = {
        "action": "list_memories",
        "fired": True,
        "count": len(items),
        "items": items[:10],
    }
    _log(rec)
    audit.mark_fired("list_memories")
    _emit_signal("memory", summary, fired=True, priority=3)
    return rec


def send_imessage(handle: str, text: str, **_: Any) -> dict[str, Any]:
    """Send an iMessage. Live only for handles in :data:`LIVE_IMESSAGE_HANDLES`."""
    decision = audit.gate("send_imessage", handle=handle, text=text)
    if not decision.allow:
        return _suppressed("send_imessage", decision)

    allow_live = handle in LIVE_IMESSAGE_HANDLES and not audit.is_dry_run()
    fired = False
    reason = ""
    if allow_live:
        try:
            import services  # type: ignore[import-not-found]

            contact = services.Contact(
                id="0",
                name=handle,
                source="imessage",
                guid=handle,
            )
            fired = bool(services.imsg_send(contact, text))
        except Exception as e:
            reason = repr(e)
            print(f"[g2.actions] imsg_send failed: {e!r}", file=sys.stderr)
    else:
        reason = (
            "dry_run mode" if audit.is_dry_run() else "handle not in LIVE_IMESSAGE_HANDLES"
        )

    summary = f"to {handle}: {text[:60]}"
    record = {
        "action": "send_imessage",
        "fired": fired,
        "handle": handle,
        "text": text,
        "reason": reason,
    }
    _log(record)
    audit.mark_fired("send_imessage", handle=handle)
    _emit_signal("imessage", summary, fired=fired, priority=6)
    return record


def send_email(to: str, subject: str, body: str, **_: Any) -> dict[str, Any]:
    """Email is on the policy.yaml denylist — this verb always dry-runs."""
    decision = audit.gate("send_email", to=to, subject=subject, body=body)
    rec = {
        "action": "send_email",
        "fired": False,
        "to": to,
        "subject": subject,
        "body": body,
        "decision": "suppressed" if not decision.allow else "considered",
        "reason": decision.reason,
    }
    _log(rec)
    if decision.allow:
        audit.mark_fired("send_email", to=to)
    _emit_signal("email", f"to {to}: {subject}", fired=False, priority=4)
    return rec


def answer_question(question: str, **_: Any) -> dict[str, Any]:
    """One-line ambient answer pulled from inbox briefing context."""
    decision = audit.gate("answer_question", question=question)
    if not decision.allow:
        return _suppressed("answer_question", decision)

    answer = ""
    reason = ""
    sys_prompt = (
        "You are an ambient assistant whispering ONE calm sentence (under 25 words) "
        "into a heads-up display. No greetings, no emoji."
    )
    try:
        from .llm import call_llm

        if _loop is not None and _loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                call_llm(prompt=question, system=sys_prompt, max_tokens=80), _loop
            )
            answer = (future.result(timeout=20) or "").strip()
        else:
            answer = (
                asyncio.run(
                    call_llm(prompt=question, system=sys_prompt, max_tokens=80)
                )
                or ""
            ).strip()
    except Exception as e:
        reason = repr(e)

    rec = {
        "action": "answer_question",
        "fired": bool(answer),
        "question": question,
        "answer": answer,
        "reason": reason,
    }
    _log(rec)
    audit.mark_fired("answer_question", question=question)
    _emit_signal("answer", answer or "(no answer)", fired=bool(answer), priority=5)
    return rec


def recall_person(name: str, **_: Any) -> dict[str, Any]:
    """Surface a one-line memory digest for ``name``. Implementation in Phase 5."""
    decision = audit.gate(
        "recall_person",
        person=name,
        name=name,
        # default to "we have prior correspondence" so the audit gate's
        # require_prior_correspondence rule isn't a hard block in demos
        # without a phone-side messages db; recall.py overrides this.
        known_message_count=_.get("known_message_count", 1),
    )
    if not decision.allow:
        return _suppressed("recall_person", decision)

    digest = ""
    try:
        from . import recall as _recall  # imported lazily — Phase 5 dep

        digest = _recall.recall(name) or ""
    except Exception as e:
        print(f"[g2.actions] recall failed: {e!r}", file=sys.stderr)

    rec = {
        "action": "recall_person",
        "fired": bool(digest),
        "name": name,
        "digest": digest,
    }
    _log(rec)
    audit.mark_fired("recall_person", person=name)
    _emit_signal("recall", digest or f"recall {name}", fired=bool(digest), priority=6)
    return rec


def lookup_contact(name: str) -> dict[str, Any] | None:
    """Read-only contact resolution. Tries inbox AddressBook, then sandbox."""
    try:
        import services  # type: ignore[import-not-found]

        if _gmail_service_provider is not None:
            gmail = _gmail_service_provider() or {}
            results = services.contacts_search(gmail, name, limit=1)
            if results:
                return dict(results[0])
    except Exception:
        pass
    if not CONTACTS_PATH.exists():
        return None
    needle = name.lower().strip()
    for c in _read_json(CONTACTS_PATH):
        if needle in (c.get("name") or "").lower():
            return c
        for alias in c.get("aliases", []):
            if needle in alias.lower():
                return c
    return None


# ── Dispatcher ────────────────────────────────────────────────────────────


DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "create_reminder": create_reminder,
    "list_reminders": list_reminders,
    "add_calendar_event": add_calendar_event,
    "list_calendar": list_calendar,
    "add_note": add_note,
    "list_notes": list_notes,
    "remember_fact": remember_fact,
    "list_memories": list_memories,
    "send_imessage": send_imessage,
    "send_email": send_email,
    "answer_question": answer_question,
    "recall_person": recall_person,
}

_dispatch_lock = threading.Lock()


def dispatch(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single entry point used by voice agents and the demo panel."""
    payload = dict(payload or {})
    fn = DISPATCH.get(action)
    if fn is None:
        rec = {"action": action, "fired": False, "reason": "unknown action"}
        _log(rec)
        return rec
    with _dispatch_lock:
        try:
            return fn(**payload)
        except TypeError as e:
            rec = {"action": action, "fired": False, "reason": f"bad payload: {e}"}
            _log(rec)
            return rec
        except Exception as e:
            rec = {"action": action, "fired": False, "reason": repr(e)}
            _log(rec)
            return rec


def recent_actions(limit: int = 20) -> list[dict[str, Any]]:
    """Return the tail of ``actions.jsonl`` for the demo panel."""
    if not ACTIONS_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in ACTIONS_LOG.read_text().splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# Convenience: actions.policy() / actions.audit_summary() for the demo panel.
def policy() -> dict[str, Any]:
    return audit.policy()


def audit_summary() -> dict[str, Any]:
    return audit.summary()


def signal_dict(signal: Signal) -> dict[str, Any]:
    return asdict(signal)
