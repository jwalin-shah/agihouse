"""Policy-gated runtime around extracted actions.

This module is the single entry point from transcript-extracted intents to
action execution. It enforces:
1) payload validation,
2) policy gate decisions,
3) proposal-first behavior (default),
4) consistent audit logging for fired/proposed/suppressed outcomes.
"""

from __future__ import annotations

import os
import time
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from actions import dispatch as raw_dispatch
from actions import learn_from_proposal_feedback
from audit import gate, log_event, mark_fired, policy
from output import notify


class SendImessagePayload(BaseModel):
    handle: str
    text: str


class ScheduleImessagePayload(BaseModel):
    handle: str
    text: str
    send_at: str


class CreateReminderPayload(BaseModel):
    title: str = "Reminder"
    due: str | None = None


class AddCalendarEventPayload(BaseModel):
    title: str = "Event"
    when: str


class AddNotePayload(BaseModel):
    title: str
    body: str = ""


class SendEmailPayload(BaseModel):
    to: str
    subject: str
    body: str


class ListQueryPayload(BaseModel):
    query: str | None = None


class ListCalendarPayload(BaseModel):
    when: str | None = None


class RememberFactPayload(BaseModel):
    subject: str
    fact: str


class AnswerQuestionPayload(BaseModel):
    question: str


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "send_imessage": SendImessagePayload,
    "schedule_imessage": ScheduleImessagePayload,
    "create_reminder": CreateReminderPayload,
    "add_calendar_event": AddCalendarEventPayload,
    "add_note": AddNotePayload,
    "send_email": SendEmailPayload,
    "list_reminders": ListQueryPayload,
    "list_notes": ListQueryPayload,
    "list_memories": ListQueryPayload,
    "list_calendar": ListCalendarPayload,
    "remember_fact": RememberFactPayload,
    "answer_question": AnswerQuestionPayload,
}

WRITE_ACTIONS = {
    "send_imessage",
    "schedule_imessage",
    "create_reminder",
    "add_calendar_event",
    "add_note",
    "send_email",
    "remember_fact",
}

_PENDING_PROPOSALS: dict[str, dict[str, Any]] = {}


def _proposal_only_default() -> bool:
    return os.environ.get("AGIHOUSE_PROPOSAL_ONLY", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _preview(action: str, payload: dict[str, Any]) -> str:
    if action == "send_imessage":
        return f"Message {payload.get('handle', '?')}: {payload.get('text', '')[:90]}"
    if action == "schedule_imessage":
        return f"Schedule message {payload.get('handle', '?')} @ {payload.get('send_at', '?')}: {payload.get('text', '')[:70]}"
    if action == "create_reminder":
        due = payload.get("due")
        return f"Reminder: {payload.get('title', '?')}" + (f" by {due}" if due else "")
    if action == "add_calendar_event":
        return f"Calendar: {payload.get('title', '?')} @ {payload.get('when', '?')}"
    if action == "add_note":
        body = payload.get("body")
        return f"Note: {payload.get('title', '?')}" + (f" - {body[:70]}" if body else "")
    if action == "send_email":
        return f"Email {payload.get('to', '?')}: {payload.get('subject', '')[:70]}"
    if action == "remember_fact":
        return f"Remember {payload.get('subject', '?')}: {payload.get('fact', '')[:80]}"
    if action == "answer_question":
        return f"Answer: {payload.get('question', '')[:100]}"
    if action.startswith("list_"):
        value = payload.get("query") or payload.get("when")
        return action.replace("_", " ").title() + (f": {value}" if value else "")
    compact = ", ".join(f"{k}={v!r}" for k, v in payload.items())
    return f"{action}: {compact}" if compact else action


def _action_title(action: str, payload: dict[str, Any]) -> str:
    if action == "create_reminder":
        return f"Reminder: {payload.get('title', '')}".strip()
    if action == "send_imessage":
        return f"Message: {payload.get('handle', '')}".strip()
    if action == "schedule_imessage":
        return f"Schedule: {payload.get('handle', '')}".strip()
    if action == "add_calendar_event":
        return f"Calendar: {payload.get('title', '')}".strip()
    if action == "remember_fact":
        return f"Memory: {payload.get('subject', '')}".strip()
    if action == "add_note":
        return f"Note: {payload.get('title', '')}".strip()
    if action == "send_email":
        return f"Email: {payload.get('to', '')}".strip()
    return action


def _proposal_card(
    action: str,
    payload: dict[str, Any],
    *,
    confidence: float | None,
    reason: str,
    proposal_id: str,
) -> dict[str, Any]:
    conf = None if confidence is None else round(float(confidence), 2)
    return {
        "id": proposal_id,
        "title": _action_title(action, payload)[:64],
        "action": action,
        "preview": _preview(action, payload)[:140],
        "reason": reason[:120],
        "confidence": conf,
    }


def _render_card_line(card: dict[str, Any]) -> str:
    conf = card.get("confidence")
    conf_txt = f"{int(conf * 100)}%" if isinstance(conf, (float, int)) else "?"
    return f"Proposed[{card['id'][:8]}] {card['title']} ({conf_txt})"


def _person_for_action(action: str, payload: dict[str, Any]) -> str | None:
    if action == "remember_fact":
        return payload.get("subject")
    if action in {"send_imessage", "schedule_imessage"}:
        return payload.get("handle")
    if action == "send_email":
        return payload.get("to")
    return None


def _auto_fire_actions() -> set[str]:
    configured = (policy().get("execution") or {}).get("auto_fire_actions")
    if configured is not None:
        return set(configured or [])
    return {"list_reminders", "list_calendar", "list_notes", "list_memories", "answer_question"}


def validate_payload(action: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    model = PAYLOAD_MODELS.get(action)
    if model is None:
        return None, f"unknown action {action!r}"
    try:
        if hasattr(model, "model_validate"):
            validated = model.model_validate(payload)
        else:
            validated = model(**payload)
    except ValidationError as exc:
        return None, str(exc)
    if hasattr(validated, "model_dump"):
        return validated.model_dump(), None
    return validated.dict(), None


def evaluate_and_dispatch(
    action: str,
    payload: dict[str, Any],
    *,
    transcript: str = "",
    confidence: float | None = None,
    proposal_only: bool | None = None,
) -> dict[str, Any]:
    """Validate, gate, and propose/execute one extracted action."""
    proposal_mode = _proposal_only_default() if proposal_only is None else proposal_only
    ts = time.time()

    validated, err = validate_payload(action, payload)
    if err is not None:
        log_event(
            "suppressed",
            action=action,
            reason=f"payload_validation_failed: {err}",
            transcript_chunk=transcript[:240],
            payload=payload,
            confidence=confidence,
            ts_runtime=ts,
        )
        return {"ok": False, "status": "suppressed", "reason": "invalid_payload", "error": err}
    assert validated is not None

    decision = gate(
        action,
        transcript_chunk=transcript[:240],
        snippet=_preview(action, validated)[:240],
        summary=_preview(action, validated)[:240],
        confidence=confidence,
        payload=validated,
        person=_person_for_action(action, validated),
    )
    if not decision.allow:
        return {"ok": False, "status": "suppressed", "reason": decision.reason}

    if proposal_only is None and action in _auto_fire_actions():
        proposal_mode = False

    if proposal_mode and action in WRITE_ACTIONS:
        proposal_id = str(uuid4())
        card = _proposal_card(
            action,
            validated,
            confidence=confidence,
            reason="awaiting_user_confirmation",
            proposal_id=proposal_id,
        )
        _PENDING_PROPOSALS[proposal_id] = {
            "id": proposal_id,
            "ts": ts,
            "action": action,
            "payload": validated,
            "confidence": confidence,
            "transcript": transcript[:240],
            "status": "proposed",
            "card": card,
        }
        log_event(
            "proposed",
            action=action,
            reason="proposal_first_mode",
            proposal_id=proposal_id,
            payload=validated,
            confidence=confidence,
            transcript_chunk=transcript[:240],
            ts_runtime=ts,
        )
        notify(f"🟡 PROPOSAL: {card['title']}\nSay 'confirm' or 'reject'", speak=False)
        return {
            "ok": True,
            "status": "proposed",
            "proposal_id": proposal_id,
            "card": card,
            "action": action,
            "payload": validated,
            "preview": _preview(action, validated),
        }

    result = raw_dispatch(action, validated)
    mark_fired(
        action,
        person=_person_for_action(action, validated),
        payload=validated,
        confidence=confidence,
    )
    return {
        "ok": True,
        "status": "fired",
        "action": action,
        "payload": validated,
        "result": result,
    }


def list_pending_proposals() -> list[dict[str, Any]]:
    return sorted(_PENDING_PROPOSALS.values(), key=lambda p: p["ts"], reverse=True)


def confirm_proposal(proposal_id: str) -> dict[str, Any]:
    proposal = _PENDING_PROPOSALS.get(proposal_id)
    if not proposal:
        return {"ok": False, "status": "missing", "proposal_id": proposal_id}
    if proposal.get("status") != "proposed":
        return {"ok": False, "status": "not_confirmable", "proposal_id": proposal_id}
    action = proposal["action"]
    payload = proposal["payload"]
    result = raw_dispatch(action, payload)
    proposal["status"] = "fired"
    proposal["executed_at"] = time.time()
    mark_fired(
        action,
        person=_person_for_action(action, payload),
        payload=payload,
        confidence=proposal.get("confidence"),
        proposal_id=proposal_id,
    )
    log_event("fired", action=action, reason="user_confirmed", proposal_id=proposal_id)
    learned = learn_from_proposal_feedback(
        proposal_id=proposal_id,
        action=action,
        payload=payload,
        outcome="confirmed",
        transcript=proposal.get("transcript", ""),
    )
    notify(f"Executed[{proposal_id[:8]}]: {_preview(action, payload)}", speak=False)
    return {"ok": True, "status": "fired", "proposal_id": proposal_id, "result": result, "learned": learned}


def reject_proposal(proposal_id: str, reason: str = "user_rejected") -> dict[str, Any]:
    proposal = _PENDING_PROPOSALS.get(proposal_id)
    if not proposal:
        return {"ok": False, "status": "missing", "proposal_id": proposal_id}
    if proposal.get("status") != "proposed":
        return {"ok": False, "status": "not_rejectable", "proposal_id": proposal_id}
    proposal["status"] = "rejected"
    proposal["rejected_at"] = time.time()
    proposal["reject_reason"] = reason
    log_event(
        "suppressed",
        action=proposal["action"],
        reason=reason,
        proposal_id=proposal_id,
        payload=proposal["payload"],
    )
    learned = learn_from_proposal_feedback(
        proposal_id=proposal_id,
        action=proposal["action"],
        payload=proposal["payload"],
        outcome="rejected",
        transcript=proposal.get("transcript", ""),
    )
    notify(f"Rejected[{proposal_id[:8]}]: {_preview(proposal['action'], proposal['payload'])}", speak=False)
    return {"ok": True, "status": "rejected", "proposal_id": proposal_id, "learned": learned}
