"""Transcript -> rich memory analysis via Groq (Llama 3.3 70B).

Parallel concern to event_extractor: instead of producing the next ACTION,
this produces a structured MEMORY record (people/topics/decisions/tasks/
promises/category/importance/hud) for archival into the Obsidian vault.

Returns None on any failure so callers can fan out without blocking.
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

_SYSTEM = "You are a precise memory extraction engine. Return JSON only."

_PROMPT_TEMPLATE = """Analyze this transcript snippet for a private wearable memory agent.
Return strict JSON with keys: summary, people, topics, decisions, tasks, promises, category, importance, hud.

Rules:
- summary: one concise sentence in English.
- people/topics/decisions/tasks/promises: arrays of short strings (may be empty).
- category: one of work, personal, health, idea, promise, ambient.
- importance: number from 0 to 1.
- hud: at most 3 short lines, max about 20 chars per line, no markdown.
- Extract only concrete memories. Ignore filler.

Transcript:
{transcript}"""

_VALID_CATEGORIES = {"work", "personal", "health", "idea", "promise", "ambient"}


def _coerce_str_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _normalize(parsed: dict[str, Any], transcript: str) -> dict[str, Any]:
    summary = str(parsed.get("summary") or "").strip() or transcript.strip()[:120]
    people = _coerce_str_list(parsed.get("people"))
    topics = _coerce_str_list(parsed.get("topics"))
    decisions = _coerce_str_list(parsed.get("decisions"))
    tasks = _coerce_str_list(parsed.get("tasks"))
    promises = _coerce_str_list(parsed.get("promises"))
    cat = str(parsed.get("category") or "").strip().lower()
    if cat not in _VALID_CATEGORIES:
        cat = "promise" if promises else "work" if tasks else "ambient"
    try:
        importance = float(parsed.get("importance") or 0)
    except Exception:
        importance = 0.0
    importance = max(0.0, min(1.0, importance))
    hud = str(parsed.get("hud") or summary[:60]).strip()
    return {
        "summary": summary,
        "people": people,
        "topics": topics,
        "decisions": decisions,
        "tasks": tasks,
        "promises": promises,
        "category": cat,
        "importance": importance,
        "hud": hud,
    }


def analyze(transcript: str) -> dict[str, Any] | None:
    """Return rich memory dict, or None if unusable / API unavailable."""
    if not transcript or len(transcript.strip()) < 4:
        return None
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    body = {
        "model": _MODEL,
        "max_tokens": 512,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _PROMPT_TEMPLATE.format(transcript=transcript.strip())},
        ],
    }
    headers = {"Authorization": f"Bearer {key}"}
    text = ""
    for attempt in range(2):
        try:
            r = httpx.post(_GROQ_URL, headers=headers, json=body, timeout=15)
        except Exception as e:
            print(f"[memory-analyzer] api failed: {e!r}", file=sys.stderr)
            return None
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"].strip()
            break
        if r.status_code == 429 and attempt == 0:
            time.sleep(2.0)
            continue
        print(f"[memory-analyzer] groq {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except Exception:
        print(f"[memory-analyzer] non-json: {text[:200]!r}", file=sys.stderr)
        return None
    if not isinstance(parsed, dict):
        return None
    return _normalize(parsed, transcript)
