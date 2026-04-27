"""Arbitrator — reads the blackboard and decides what (if anything) shows.

Two modes:
- `arbitrator_loop`: LLM-driven, runs every interval_arbitrator seconds.
- `apply_demo_arbitration`: deterministic rule path used by the demo panel
  so synthetic scenarios don't depend on LLM availability.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from loguru import logger

from .blackboard import Blackboard, Signal
from .config import ARBITRATOR_SYSTEM, settings
from .device_state import DeviceState
from .llm import call_llm, strip_code_fence
from .ws_manager import G2WebSocketManager


def _signal_title(signal: Signal) -> str:
    data = signal.data
    return (
        data.get("title")
        or data.get("label")
        or data.get("from")
        or data.get("message")
        or data.get("insight")
        or signal.category.upper()
    )


def _signal_detail(signal: Signal) -> str:
    data = signal.data
    if signal.category == "meeting":
        mins = data.get("minutes_away")
        attendees = data.get("attendees", "")
        return f"{mins} min · {attendees}" if mins is not None else str(attendees)
    if signal.category == "message":
        sender = data.get("from", "Message")
        preview = data.get("preview", "")
        return f"{sender}: {preview}" if preview else str(sender)
    if signal.category == "reminder":
        label = data.get("label", "Reminder")
        title = data.get("title", "")
        return f"{label} · {title}" if title else str(label)
    return data.get("detail") or data.get("message") or data.get("insight") or signal.category


def _rule_hud(signal: Signal) -> tuple[str, str]:
    return str(_signal_title(signal))[:28], str(_signal_detail(signal))[:28]


async def apply_demo_arbitration(
    blackboard: Blackboard,
    ws_manager: G2WebSocketManager,
    device_state: DeviceState,
) -> dict[str, Any]:
    """Deterministic fast path used by the demo panel."""
    signals = await blackboard.read_active()
    blackboard.total_evaluated += 1

    if not signals:
        blackboard.last_arbitrator_reasoning = "Demo rule: no active signals."
        return {"show": False, "reasoning": blackboard.last_arbitrator_reasoning}

    top = signals[0]
    if (
        device_state.attention_state == "focused"
        and top.priority < settings.arbitrator_focused_floor
    ):
        blackboard.total_suppressed += 1
        blackboard.last_arbitrator_reasoning = (
            f"Demo rule: focused mode suppresses {top.agent_id} priority {top.priority}."
        )
        return {
            "show": False,
            "agent_id": top.agent_id,
            "reasoning": blackboard.last_arbitrator_reasoning,
        }

    if top.priority <= settings.arbitrator_silence_floor:
        blackboard.total_suppressed += 1
        blackboard.last_arbitrator_reasoning = (
            f"Demo rule: {top.agent_id} priority {top.priority} is low-noise, so stay silent."
        )
        return {
            "show": False,
            "agent_id": top.agent_id,
            "reasoning": blackboard.last_arbitrator_reasoning,
        }

    if top.priority >= settings.arbitrator_show_floor:
        line1, line2 = _rule_hud(top)
        await ws_manager.broadcast_hud(line1, line2)
        await blackboard.mark_shown(top.agent_id)
        blackboard.total_shown += 1
        blackboard.last_arbitrator_reasoning = (
            f"Demo rule: showing highest-priority signal from {top.agent_id}."
        )
        return {
            "show": True,
            "agent_id": top.agent_id,
            "line1": line1,
            "line2": line2,
            "reasoning": blackboard.last_arbitrator_reasoning,
        }

    blackboard.total_suppressed += 1
    blackboard.last_arbitrator_reasoning = (
        f"Demo rule: {top.agent_id} priority {top.priority} is informative but not urgent."
    )
    return {
        "show": False,
        "agent_id": top.agent_id,
        "reasoning": blackboard.last_arbitrator_reasoning,
    }


async def arbitrator_loop(
    blackboard: Blackboard,
    ws_manager: G2WebSocketManager,
    device_state: DeviceState,
) -> None:
    logger.info("[arbitrator] started")
    last_shown_agent: str | None = None

    while True:
        await asyncio.sleep(settings.interval_arbitrator)
        try:
            signals = await blackboard.read_active()
            if not signals:
                await asyncio.sleep(0)
                continue

            blackboard.total_evaluated += 1

            context_lines = [
                f"attentionState={device_state.attention_state}",
                f"last_shown={last_shown_agent or 'none'}",
                f"time={datetime.now().strftime('%H:%M')}",
                "",
                "Active signals:",
            ]
            for s in signals[:6]:
                context_lines.append(
                    f"  agent={s.agent_id} priority={s.priority} "
                    f"category={s.category} data={json.dumps(s.data)}"
                )

            raw = await call_llm(
                prompt="\n".join(context_lines),
                system=ARBITRATOR_SYSTEM,
                model=settings.model_fast,
                max_tokens=200,
            )
            if not raw:
                continue

            action = json.loads(strip_code_fence(raw))

            reasoning = action.get("reasoning", "")
            blackboard.last_arbitrator_reasoning = reasoning

            if action.get("show"):
                line1 = action.get("line1", "")[:28]
                line2 = action.get("line2", "")[:28]
                agent_id = action.get("agent_id")

                await ws_manager.broadcast_hud(line1, line2)
                last_shown_agent = agent_id
                blackboard.total_shown += 1

                if agent_id:
                    await blackboard.mark_shown(agent_id)

                logger.info(f"[arbitrator] SHOW → '{line1}' | '{line2}' — {reasoning}")
            else:
                blackboard.total_suppressed += 1
                logger.info(f"[arbitrator] SILENCE — {reasoning}")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.warning(f"[arbitrator] error: {e}")
