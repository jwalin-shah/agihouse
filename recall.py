"""Person → one-line HUD memory digest.

Hero demo: someone walks up; the HUD whispers the last meaningful exchange
and any pending action. Resolves a name/email to identifiers via the inbox
project, pulls recent messages, and asks Claude for a calm one-liner.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic

from audit import gate, mark_fired

# inbox project must already be on sys.path (ambient.py sets this up); make
# this module also work when imported standalone.
_INBOX_DIR = Path.home() / "projects" / "inbox"
if str(_INBOX_DIR) not in sys.path:
    sys.path.insert(0, str(_INBOX_DIR))

from services import (  # type: ignore[import-not-found]  # noqa: E402
    contacts_search,
    imsg_contacts,
    imsg_thread,
    search_all,
)

_client: Anthropic | None = None
_MODEL = "claude-haiku-4-5-20251001"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SYSTEM = """You are an ambient assistant whispering a single line into a heads-up display.

Input: a person's name and a small list of recent exchanges with them.
Output: ONE calm sentence (under 25 words) the user can read while shaking
the person's hand. No greetings, no preamble, no emoji, no quotation marks.

Preferred shape:
  "<Person> — <last meaningful exchange, short> — <pending action or next beat>."
If there is no pending action, drop that segment.
Even if items are short/casual, still produce a one-line digest using what's
there. Never output "skip".

Never invent facts. Use only what's in the items."""


def _client_singleton() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _looks_like_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s.strip()))


def _resolve_identifiers(
    person: str, gmail_services: dict
) -> tuple[str, list[str], list[str], str | None]:
    """Return (display_name, emails, phones, imsg_chat_id)."""
    person = person.strip()
    emails: list[str] = []
    phones: list[str] = []
    chat_id: str | None = None
    name = person

    if _looks_like_email(person):
        emails.append(person)
    else:
        try:
            matches = contacts_search(gmail_services, person, limit=5)
        except Exception:
            matches = []
        if matches:
            top = matches[0]
            name = top.get("name") or person
            emails = list(top.get("emails") or [])
            phones = list(top.get("phones") or [])

    # Fuzzy-match an iMessage chat by name or member identifier.
    try:
        convs = imsg_contacts(limit=200)
    except Exception:
        convs = []
    def _norm_phone(p: str) -> str:
        digits = re.sub(r"\D", "", p)
        if len(digits) == 10:
            digits = "1" + digits
        return digits

    needle = person.lower()
    name_lc = name.lower()
    phone_digits = {_norm_phone(p) for p in phones if p}
    id_set = {e.lower() for e in emails}
    for c in convs:
        if c.is_group:
            continue
        hay = (c.name or "").lower()
        member_hits = any(
            (m.lower() in id_set)
            or (m and _norm_phone(m) and _norm_phone(m) in phone_digits)
            or (needle and needle in m.lower())
            for m in (c.members or [])
        )
        if needle and (needle in hay or name_lc in hay) or member_hits:
            chat_id = c.id
            # Backfill: if contacts_search missed, harvest member identifiers.
            for m in c.members or []:
                if "@" in m and m.lower() not in {e.lower() for e in emails}:
                    emails.append(m)
                elif "@" not in m and m not in phones:
                    phones.append(m)
            break

    return name, emails, phones, chat_id


def _gmail_items(query_emails: list[str], gmail_services: dict, per: int) -> list[dict]:
    out: list[dict] = []
    for email in query_emails:
        try:
            res = search_all(
                email,
                sources=["gmail"],
                limit=per,
                gmail_services=gmail_services,
                from_addr=email,
            )
        except Exception:
            continue
        out.extend(res.get("results", []) or [])
    return out


def _imsg_items(chat_id: str, limit: int) -> list[dict]:
    try:
        msgs = imsg_thread(chat_id, limit=limit)
    except Exception:
        return []
    out: list[dict] = []
    for m in msgs[-limit:]:
        out.append(
            {
                "source": "imessage",
                "sender": "you" if m.is_me else (m.sender or "them"),
                "timestamp": m.ts.isoformat() if m.ts else "",
                "snippet": (m.body or "").strip()[:240],
            }
        )
    return out


def _render(items: list[dict]) -> str:
    if not items:
        return "(no recent messages)"
    lines = []
    for it in items:
        who = it.get("sender") or it.get("from") or "?"
        when = (it.get("timestamp") or "")[:16].replace("T", " ")
        snip = (it.get("snippet") or it.get("body") or "").strip()[:240]
        if not snip:
            continue
        lines.append(f"- [{it.get('source', '?')}] {who} {when}: {snip}")
    return "\n".join(lines) if lines else "(no recent messages)"


def recall(
    person: str,
    *,
    gmail_services: dict,
    cal_services: dict | None = None,
    lookback_days: int = 30,
) -> str | None:
    """Given a person's name or email, return a one-line memory digest."""
    if not person or not person.strip():
        return None

    name, emails, phones, chat_id = _resolve_identifiers(person, gmail_services)

    items: list[dict] = []
    if chat_id:
        items.extend(_imsg_items(chat_id, limit=4))
    if emails:
        items.extend(_gmail_items(emails, gmail_services, per=3))

    # Gate AFTER we know how much prior correspondence exists, so the
    # "require_prior_correspondence" rule has real input to check against.
    decision = gate(
        "recall",
        person=name,
        known_message_count=len(items),
        emails=emails,
        chat_id=chat_id,
    )
    if not decision.allow:
        return None

    # Freshest first, then cap.
    items.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    # Dedup on (source, snippet head).
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for it in items:
        key = (it.get("source", ""), (it.get("snippet") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    items = deduped[:6]

    if not items:
        return None

    # No-LLM path: surface the freshest snippet directly. Demo-grade,
    # deterministic, no API key required.
    first = items[0]
    snippet = (first.get("snippet") or "").strip().replace("\n", " ")
    if len(snippet) > 140:
        snippet = snippet[:137] + "…"
    src = first.get("source", "")
    text = f'{name} — "{snippet}"' if snippet else f"{name} — recent contact"
    if src:
        text = f"{text} ({src})"
    mark_fired("recall", person=name, output=text)
    return text
