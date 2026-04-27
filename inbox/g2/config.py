"""Central config for the G2 ambient copilot package.

Everything tunable lives here so we don't have constants scattered across
five agent files. Override any of these via the corresponding env var.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    inbox_url: str
    openrouter_key: str
    openrouter_url: str
    model_fast: str
    model_reason: str
    model_extractor: str
    interval_calendar: int
    interval_messages: int
    interval_reminders: int
    interval_arbitrator: int
    interval_context: int
    transcript_min_chars: int
    arbitrator_focused_floor: int
    arbitrator_show_floor: int
    arbitrator_silence_floor: int
    extractor_confidence_threshold: float
    extractor_remember_fact_threshold: float
    extractor_min_chars: int
    extractor_window_seconds: float
    extractor_min_gap_seconds: float

    @staticmethod
    def from_env() -> Settings:
        return Settings(
            inbox_url=_env_str("INBOX_SERVER_URL", "http://127.0.0.1:9849"),
            openrouter_key=_env_str("OPENROUTER_API_KEY", ""),
            openrouter_url=_env_str(
                "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
            ),
            model_fast=_env_str("G2_MODEL_FAST", "deepseek/deepseek-v4-pro"),
            model_reason=_env_str("G2_MODEL_REASON", "deepseek/deepseek-v4-pro"),
            model_extractor=_env_str(
                "G2_MODEL_EXTRACTOR", "meta-llama/llama-3.3-70b-instruct"
            ),
            interval_calendar=_env_int("G2_INTERVAL_CALENDAR", 60),
            interval_messages=_env_int("G2_INTERVAL_MESSAGES", 45),
            interval_reminders=_env_int("G2_INTERVAL_REMINDERS", 60),
            interval_arbitrator=_env_int("G2_INTERVAL_ARBITRATOR", 10),
            interval_context=_env_int("G2_INTERVAL_CONTEXT", 3),
            transcript_min_chars=_env_int("G2_TRANSCRIPT_MIN_CHARS", 15),
            arbitrator_focused_floor=_env_int("G2_FOCUSED_FLOOR", 8),
            arbitrator_show_floor=_env_int("G2_SHOW_FLOOR", 6),
            arbitrator_silence_floor=_env_int("G2_SILENCE_FLOOR", 2),
            extractor_confidence_threshold=_env_float("G2_EXTRACTOR_CONFIDENCE", 0.7),
            extractor_remember_fact_threshold=_env_float(
                "G2_EXTRACTOR_REMEMBER_CONFIDENCE", 0.85
            ),
            extractor_min_chars=_env_int("G2_EXTRACTOR_MIN_CHARS", 8),
            extractor_window_seconds=_env_float("G2_EXTRACTOR_WINDOW_SECONDS", 4.0),
            extractor_min_gap_seconds=_env_float("G2_EXTRACTOR_MIN_GAP_SECONDS", 2.0),
        )


settings = Settings.from_env()


ARBITRATOR_SYSTEM = f"""\
You are the arbitrator for a smart glasses HUD (576x288 monochrome display).
Multiple agents have written signals to the blackboard. You must decide ONE thing to show, or nothing.

Rules:
- The user glances at the HUD for <2 seconds. Be extremely concise.
- line1 = headline, max 28 characters including spaces.
- line2 = detail, max 28 characters including spaces.
- If in focused mode (attentionState=focused), only show priority >= {settings.arbitrator_focused_floor}.
- If nothing is urgent or novel, show=false. Silence is the correct answer most of the time.
- Never repeat what was just shown (check last_shown).

Reply ONLY valid JSON: {{"show": boolean, "agent_id": string|null, "line1": string, "line2": string, "reasoning": string}}
reasoning = one sentence explaining your choice (for the demo panel).
"""


TRANSCRIPT_SYSTEM = (
    "You are listening to ambient speech. Extract one actionable insight if present. "
    'Reply ONLY valid JSON: {"insight": string|null, "priority": 1-5}. '
    "Return null if nothing notable. Priority 5 = urgent action needed, 1 = minor note. "
    "Be conservative — most speech is not notable."
)


EXTRACTOR_SYSTEM = """You extract a single intended ACTION from a short transcript of speech.

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
- Use remember_fact ONLY when the transcript EXPLICITLY contains a named person AND a fact
  about that person VERBATIM. NEVER invent facts not literally present in the transcript.
  If unsure → action="none". Hallucinating a fact is the worst possible failure mode.
- Use answer_question for open-ended questions not covered by list_* actions.
- Confidence < 0.7 unless the directive/question is unambiguous.

Output ONLY the JSON object, nothing else."""
