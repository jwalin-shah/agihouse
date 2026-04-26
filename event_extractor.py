"""Transcript → structured event via NVIDIA NIM (OpenAI-compatible).

Given a short ASR transcript, returns either None (no actionable event) or
a dict suitable for actions.dispatch():

    {"action": "create_reminder", "payload": {...}, "confidence": 0.0-1.0, "reason": "..."}

Action types correspond to actions.DISPATCH keys plus "note" via add_note.
We threshold on confidence; below the threshold the lens stays silent so
ambient ramble doesn't produce spurious proposals.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx

_MODEL = "llama-3.3-70b-versatile"
_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CONFIDENCE_THRESHOLD = 0.7

SYSTEM = """You extract a single intended ACTION from a short transcript of speech.

Return JSON ONLY with these fields:
{
  "action": "send_imessage" | "create_reminder" | "add_calendar_event" | "add_note" | "send_email" | "list_reminders" | "list_calendar" | "list_notes" | "list_memories" | "remember_fact" | "answer_question" | "none",
  "payload": { ...action-specific... },
  "confidence": 0.0 to 1.0,
  "reason": "<one short sentence>"
}

Action payload shapes:
- send_imessage: {"handle": "<name from transcript>", "text": "<short message>"}
- create_reminder: {"title": "<what>", "due": "<when, ISO-like or null>"}
- add_calendar_event: {"title": "<what>", "when": "<when string>"}
- add_note: {"title": "<one-line summary>", "body": "<longer context if any>"}
- send_email: {"to": "<name>", "subject": "<short>", "body": "<short>"}
- list_reminders: {"query": "<optional substring filter or null>"}
- list_calendar: {"when": "<optional day/keyword or null>"}
- list_notes: {"query": "<optional substring filter or null>"}
- list_memories: {"query": "<optional substring filter or null>"}
- remember_fact: {"subject": "<person/topic name>", "fact": "<one-sentence fact about them>"}
- answer_question: {"question": "<the question verbatim>"}
- none: {}

Rules:
- Be CONSERVATIVE. Small talk, narration, half-formed thoughts → action="none".
- The transcript may contain speech from PEOPLE OTHER THAN the wearer (the wearer is the user we serve).
- WRITE actions (create_reminder, send_imessage, add_calendar_event, add_note, send_email):
  ONLY fire on FIRST-PERSON DIRECTIVES from the wearer. Look for "I", "I'll", "I want to",
  "remind me", "let me", "we are meeting", "my <thing>", or imperative directives at the
  wearer's assistant. Do NOT fire WRITE actions on third-person statements ("Tarun is going to...",
  "she will text you", "they are meeting").
- READ actions (list_*, answer_question): fire on direct questions to the assistant
  ("what are MY reminders", "what's on MY calendar", "what should I do").
- remember_fact: fire on declarative facts about a NAMED OTHER PERSON, regardless of who said it.
  This is the right action when someone (wearer or third party) describes someone else.
- Examples:
  "Remind me to call mom tomorrow" → create_reminder
  "I'll text Tarun the demo" → send_imessage
  "We're meeting at 3" → add_calendar_event
  "Note that the build broke" → add_note
  "What are my reminders?" → list_reminders
  "What's on my calendar today?" → list_calendar(when="today")
  "Show me my notes about X" → list_notes(query="X")
  "What's the weather" / "Hello" / "I think..." → none
  "<NAME> is applying for a job at <COMPANY>" → remember_fact(NAME, "applying for a job at COMPANY")
  "<NAME> got married last <SEASON>" → remember_fact(NAME, "got married last SEASON")
  "What should I do today?" / "Whats going on with Jay?" → answer_question
- Use remember_fact ONLY when the transcript EXPLICITLY contains a named person AND a fact
  about that person VERBATIM. NEVER invent facts not literally present in the transcript.
  If unsure → action="none". Hallucinating a fact is the worst possible failure mode.
- Use answer_question for open-ended questions not covered by list_* actions.
- Confidence < 0.7 unless the directive/question is unambiguous.

Output ONLY the JSON object, nothing else."""


def extract(transcript: str) -> dict[str, Any] | None:
    """Return action dict if confident, else None."""
    if not transcript or len(transcript.strip()) < 4:
        return None
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    body = {
        "model": _MODEL,
        "max_tokens": 256,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": transcript.strip()},
        ],
    }
    headers = {"Authorization": f"Bearer {key}"}
    text = ""
    for attempt in range(2):
        try:
            r = httpx.post(_GROQ_URL, headers=headers, json=body, timeout=15)
        except Exception as e:
            print(f"[extractor] api failed: {e!r}", file=sys.stderr)
            return None
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"].strip()
            break
        if r.status_code == 429 and attempt == 0:
            print(f"[extractor] 429, retrying in 2s for: {transcript[:60]!r}", file=sys.stderr)
            time.sleep(2.0)
            continue
        print(f"[extractor] groq {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    if not text:
        return None

    # Strip code fences if Haiku wrapped them.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        obj = json.loads(text)
    except Exception:
        print(f"[extractor] non-json response: {text[:200]!r}", file=sys.stderr)
        return None

    # Model occasionally returns a list of actions; take the first concrete one.
    if isinstance(obj, list):
        obj = next((x for x in obj if isinstance(x, dict) and x.get("action")
                    and x.get("action") != "none"), None)
        if obj is None:
            return None
    if not isinstance(obj, dict):
        return None

    action = obj.get("action")
    if action in (None, "none", ""):
        return None
    conf = float(obj.get("confidence", 0) or 0)
    # Hallucination defense: remember_fact must be near-certain because it's
    # the easiest action for the model to confabulate from any transcript.
    needed = 0.85 if action == "remember_fact" else CONFIDENCE_THRESHOLD
    if conf < needed:
        return None
    return obj
