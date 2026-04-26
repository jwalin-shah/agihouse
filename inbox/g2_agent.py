"""
G2 Ambient Agent — proactive multi-agent system for Even Realities G2.

Each agent runs as its own asyncio task, continuously and independently.
They all write to a shared Blackboard. The Arbitrator reads the Blackboard
every 10s, calls DeepSeek R1 via OpenRouter, and pushes HUD commands to
connected G2 WebSocket clients.

Start all agents:  tasks = await start_g2_agents(ws_manager)
Stop:              for t in tasks: t.cancel()
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import WebSocket
from loguru import logger

# Load .env from this file's directory, overriding any pre-existing shell env vars
# so the project's own API keys win over stale exports in the user's shell.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

# ── Config ────────────────────────────────────────────────────────────────────

INBOX_URL = os.environ.get("INBOX_SERVER_URL", "http://127.0.0.1:9849")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# DeepSeek models via OpenRouter
MODEL_FAST = "deepseek/deepseek-v4-pro"        # DeepSeek V4 Pro — used for both insight + arbitration
MODEL_REASON = "deepseek/deepseek-v4-pro"      # same; reasoning model would be too slow for the 10s cycle

# Agent polling intervals (seconds)
INTERVAL_CALENDAR   = 60
INTERVAL_TRANSCRIPT = 15
INTERVAL_MESSAGES   = 45
INTERVAL_REMINDERS  = 60
INTERVAL_ARBITRATOR = 10

# Attention state sent from G2 phone
_attention_state: str = "ambient"   # "ambient" | "focused"

# Wearing state from G2 hardware (onDeviceStatusChanged.isWearing)
# Edge-detected: when off→on we fire a one-shot morning/context brief signal.
_wearing_state: bool = False
_wearing_changed_at: float = 0.0
_last_wearing_brief_at: float = 0.0


# ── Blackboard ────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    agent_id: str
    priority: int               # 0–10, higher = more urgent
    category: str               # meeting | insight | message | reminder | system
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    ttl: float = 300            # seconds until expiry
    shown: bool = False

    def is_active(self) -> bool:
        return time.time() - self.timestamp < self.ttl


class Blackboard:
    """Shared state for all agents. Thread-safe via asyncio.Lock."""

    def __init__(self) -> None:
        self._signals: dict[str, Signal] = {}
        self._lock = asyncio.Lock()
        # Stats for demo panel
        self.total_evaluated: int = 0
        self.total_suppressed: int = 0
        self.total_shown: int = 0
        self.last_arbitrator_reasoning: str = ""

    async def write(self, signal: Signal) -> None:
        async with self._lock:
            key = f"{signal.agent_id}"          # one live signal per agent at a time
            self._signals[key] = signal
            self._prune()
            logger.info(f"[blackboard] {signal.agent_id} wrote p={signal.priority} {signal.category}")

    async def read_active(self) -> list[Signal]:
        async with self._lock:
            self._prune()
            return sorted(
                [s for s in self._signals.values() if s.is_active()],
                key=lambda s: s.priority,
                reverse=True,
            )

    async def mark_shown(self, agent_id: str) -> None:
        async with self._lock:
            if agent_id in self._signals:
                self._signals[agent_id].shown = True

    async def inject(self, signal: Signal) -> None:
        """Demo control panel uses this to inject synthetic signals."""
        await self.write(signal)

    async def snapshot(self) -> list[dict]:
        """For the demo panel live feed."""
        async with self._lock:
            self._prune()
            return [
                {
                    "agent_id": s.agent_id,
                    "priority": s.priority,
                    "category": s.category,
                    "data": s.data,
                    "ttl_remaining": max(0, s.ttl - (time.time() - s.timestamp)),
                    "shown": s.shown,
                }
                for s in sorted(self._signals.values(), key=lambda x: x.priority, reverse=True)
            ]

    async def reset(self) -> None:
        """Clear all demo-visible state."""
        async with self._lock:
            self._signals.clear()
            self.total_evaluated = 0
            self.total_suppressed = 0
            self.total_shown = 0
            self.last_arbitrator_reasoning = ""

    def _prune(self) -> None:
        expired = [k for k, v in self._signals.items() if not v.is_active()]
        for k in expired:
            del self._signals[k]


# ── WebSocket Manager ─────────────────────────────────────────────────────────

class G2WebSocketManager:
    """Tracks all connected G2 phone WebView clients."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._last_hud: dict | None = None  # last broadcast HUD payload, for reconnect replay

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"[g2-ws] client connected ({len(self._connections)} total)")
        # Replay last HUD state so reconnects (phone background→foreground) don't blank the screen
        if self._last_hud is not None:
            try:
                await ws.send_json(self._last_hud)
            except Exception:
                pass

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info(f"[g2-ws] client disconnected ({len(self._connections)} remaining)")

    async def broadcast_hud(self, line1: str, line2: str) -> None:
        payload = {"type": "hud", "line1": line1, "line2": line2}
        self._last_hud = payload
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._connections -= dead

    async def broadcast_clear(self) -> None:
        payload = {"type": "clear"}
        self._last_hud = None
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._connections -= dead

    def handle_message(self, data: dict) -> None:
        """Handle messages coming FROM the G2 phone (IMU, wearing state)."""
        global _attention_state, _wearing_state, _wearing_changed_at
        msg_type = data.get("type")
        if msg_type == "imu":
            _attention_state = data.get("attentionState", "ambient")
        elif msg_type == "wearing":
            new_wearing = bool(data.get("isWearing", False))
            if new_wearing != _wearing_state:
                _wearing_state = new_wearing
                _wearing_changed_at = time.time()


# ── OpenRouter LLM ────────────────────────────────────────────────────────────

async def _llm(prompt: str, system: str, model: str = MODEL_FAST, max_tokens: int = 200) -> str | None:
    if not OPENROUTER_KEY:
        logger.warning("[llm] OPENROUTER_API_KEY not set")
        return None
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "HTTP-Referer": "https://github.com/even-realities/everything-evenhub",
                    "X-Title": "G2 Ambient Agent",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            choices = payload.get("choices") or []
            if not choices:
                logger.warning(f"[llm] {model} empty choices: {payload}")
                return None
            content = (choices[0].get("message") or {}).get("content")
            if not content or not content.strip():
                # Some reasoning models return an empty content with reasoning_content;
                # fall back to that so we still get a usable response.
                content = (choices[0].get("message") or {}).get("reasoning_content") or ""
            return content.strip() or None
    except Exception as e:
        logger.warning(f"[llm] {model} failed: {e}")
        return None


# ── Agent 1: Calendar ─────────────────────────────────────────────────────────

async def calendar_agent(blackboard: Blackboard) -> None:
    """
    Polls Google Calendar every 60s via inbox server.
    Writes a signal when a meeting is within 10 minutes.
    Priority scales with urgency: 4 at 10min → 9 at 2min.
    Runs independently, forever.
    """
    logger.info("[calendar_agent] started")
    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{INBOX_URL}/calendar/events?date={today}")
                events = resp.json() if resp.status_code == 200 else []

            now = datetime.now()
            for event in events:
                start_raw = event.get("start_time") or event.get("start") or ""
                if not start_raw:
                    continue
                try:
                    start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    continue

                mins = (start - now).total_seconds() / 60
                if not (0 < mins <= 10):
                    continue

                priority = 9 if mins <= 2 else 7 if mins <= 5 else 4
                attendees = [a.get("name") or a.get("email", "") for a in event.get("attendees", [])]
                attendee_str = ", ".join(attendees[:2])
                if len(attendees) > 2:
                    attendee_str += f" +{len(attendees) - 2}"

                await blackboard.write(Signal(
                    agent_id="calendar",
                    priority=priority,
                    category="meeting",
                    data={
                        "title": event.get("title", "Meeting")[:28],
                        "minutes_away": round(mins),
                        "attendees": attendee_str,
                        "location": event.get("location", ""),
                    },
                    ttl=mins * 60 + 120,
                ))
                break  # only surface the next meeting

        except Exception as e:
            logger.warning(f"[calendar_agent] error: {e}")

        await asyncio.sleep(INTERVAL_CALENDAR)


# ── Agent 2: Transcript ───────────────────────────────────────────────────────

async def transcript_agent(blackboard: Blackboard) -> None:
    """
    Reads the rolling MLX Whisper transcript buffer every 15s.
    Sends new speech to DeepSeek V3 (fast) to extract insights.
    Runs independently, forever.
    """
    logger.info("[transcript_agent] started")
    last_text = ""

    while True:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{INBOX_URL}/ambient/transcript?limit=6")
                data = resp.json() if resp.status_code == 200 else {}

            segments = data.get("segments", [])
            if not segments:
                await asyncio.sleep(INTERVAL_TRANSCRIPT)
                continue

            text = " ".join(s.get("text", "").strip() for s in segments[-4:])  # last ~60s
            if not text or text == last_text or len(text) < 15:
                await asyncio.sleep(INTERVAL_TRANSCRIPT)
                continue

            last_text = text

            raw = await _llm(
                prompt=text,
                system=(
                    "You are listening to ambient speech. Extract one actionable insight if present. "
                    "Reply ONLY valid JSON: {\"insight\": string|null, \"priority\": 1-5}. "
                    "Return null if nothing notable. Priority 5 = urgent action needed, 1 = minor note. "
                    "Be conservative — most speech is not notable."
                ),
                model=MODEL_FAST,
                max_tokens=80,
            )

            if raw:
                # strip any markdown fences
                clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                parsed = json.loads(clean)
                insight = parsed.get("insight")
                if insight:
                    await blackboard.write(Signal(
                        agent_id="transcript",
                        priority=int(parsed.get("priority", 2)),
                        category="insight",
                        data={"insight": insight[:56]},
                        ttl=90,
                    ))

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.warning(f"[transcript_agent] error: {e}")

        await asyncio.sleep(INTERVAL_TRANSCRIPT)


# ── Agent 3: Messages ─────────────────────────────────────────────────────────

async def messages_agent(blackboard: Blackboard) -> None:
    """
    Polls unread iMessage + Gmail every 45s.
    Surfaces urgent or VIP messages only.
    Uses DeepSeek V3 to triage urgency if needed.
    Runs independently, forever.
    """
    logger.info("[messages_agent] started")
    seen_ids: set[str] = set()

    while True:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{INBOX_URL}/conversations?source=all&limit=20")
                convos = resp.json() if resp.status_code == 200 else []

            urgent = []
            for c in convos:
                if not c.get("unread_count", 0):
                    continue
                cid = c.get("id", "")
                if cid in seen_ids:
                    continue

                name = c.get("name") or c.get("contact_name") or "Someone"
                snippet = (c.get("snippet") or c.get("last_message") or "")[:60]

                # Rule-based fast path — keywords that are always urgent
                urgent_words = {"urgent", "asap", "emergency", "now", "help", "call me"}
                if any(w in snippet.lower() for w in urgent_words):
                    seen_ids.add(cid)
                    urgent.append({"name": name, "snippet": snippet, "priority": 7})
                    continue

                # Otherwise mark as low priority — don't LLM every message
                seen_ids.add(cid)
                urgent.append({"name": name, "snippet": snippet, "priority": 2})

            # Surface only the highest-priority unread
            if urgent:
                top = max(urgent, key=lambda x: x["priority"])
                if top["priority"] >= 5:  # only surface truly urgent
                    await blackboard.write(Signal(
                        agent_id="messages",
                        priority=top["priority"],
                        category="message",
                        data={"from": top["name"][:20], "preview": top["snippet"][:28]},
                        ttl=120,
                    ))

        except Exception as e:
            logger.warning(f"[messages_agent] error: {e}")

        await asyncio.sleep(INTERVAL_MESSAGES)


# ── Agent 4: Reminders ────────────────────────────────────────────────────────

async def reminders_agent(blackboard: Blackboard) -> None:
    """
    Polls Apple Reminders every 60s.
    Surfaces items due within 30 minutes or overdue.
    Runs independently, forever.
    """
    logger.info("[reminders_agent] started")

    while True:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{INBOX_URL}/reminders?show_completed=false&limit=50")
                items = resp.json() if resp.status_code == 200 else []

            now = datetime.now()
            for item in items:
                due_raw = item.get("due_date") or item.get("due") or ""
                if not due_raw:
                    continue
                try:
                    due = datetime.fromisoformat(due_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    continue

                mins = (due - now).total_seconds() / 60
                if mins > 30:
                    continue

                priority = 8 if mins < 0 else 6 if mins < 10 else 4
                label = "OVERDUE" if mins < 0 else f"DUE {round(mins)}m"

                await blackboard.write(Signal(
                    agent_id="reminders",
                    priority=priority,
                    category="reminder",
                    data={
                        "title": item.get("title", "Reminder")[:28],
                        "label": label,
                    },
                    ttl=60,
                ))
                break  # surface only the most urgent one

        except Exception as e:
            logger.warning(f"[reminders_agent] error: {e}")

        await asyncio.sleep(INTERVAL_REMINDERS)


# ── Agent 5: Time/Context ─────────────────────────────────────────────────────

async def context_agent(blackboard: Blackboard) -> None:
    """
    Time-based triggers: morning startup, standup window, EOD.
    Fully rule-based, no LLM. Runs independently, forever.
    """
    logger.info("[context_agent] started")
    fired_today: set[str] = set()
    last_date = ""

    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if today != last_date:
                fired_today.clear()
                last_date = today

            h, m = now.hour, now.minute

            # Morning startup brief (9:00–9:05)
            if h == 9 and m < 5 and "morning" not in fired_today:
                fired_today.add("morning")
                await blackboard.write(Signal(
                    agent_id="context",
                    priority=5,
                    category="system",
                    data={"message": "Morning brief ready", "label": "GOOD MORNING"},
                    ttl=300,
                ))

            # Standup window (9:55–10:00)
            elif h == 9 and m >= 55 and "standup" not in fired_today:
                fired_today.add("standup")
                await blackboard.write(Signal(
                    agent_id="context",
                    priority=6,
                    category="system",
                    data={"message": "Standup in 5 min", "label": "STANDUP SOON"},
                    ttl=300,
                ))

            # EOD wrap (17:00–17:05)
            elif h == 17 and m < 5 and "eod" not in fired_today:
                fired_today.add("eod")
                await blackboard.write(Signal(
                    agent_id="context",
                    priority=4,
                    category="system",
                    data={"message": "Wrap up open items", "label": "END OF DAY"},
                    ttl=300,
                ))

            # Wearing detection: glasses just put on → fire morning/context brief.
            # Throttled to once per 10 minutes so toggling glasses doesn't spam.
            global _last_wearing_brief_at
            now_ts = time.time()
            if (
                _wearing_state
                and _wearing_changed_at > 0
                and (now_ts - _wearing_changed_at) < 5
                and (now_ts - _last_wearing_brief_at) > 600
            ):
                _last_wearing_brief_at = now_ts
                hh = now.strftime("%H:%M")
                await blackboard.write(Signal(
                    agent_id="context",
                    priority=5,
                    category="system",
                    data={"label": f"○ HELLO  {hh}", "message": "Glasses on"},
                    ttl=20,
                ))

        except Exception as e:
            logger.warning(f"[context_agent] error: {e}")

        # Faster cadence so wearing-edge fires within ~3s of glasses going on
        await asyncio.sleep(3)


# ── Arbitrator ────────────────────────────────────────────────────────────────

ARBITRATOR_SYSTEM = """\
You are the arbitrator for a smart glasses HUD (576x288 monochrome display).
Multiple agents have written signals to the blackboard. You must decide ONE thing to show, or nothing.

Rules:
- The user glances at the HUD for <2 seconds. Be extremely concise.
- line1 = headline, max 28 characters including spaces.
- line2 = detail, max 28 characters including spaces.
- If in focused mode (attentionState=focused), only show priority >= 8.
- If nothing is urgent or novel, show=false. Silence is the correct answer most of the time.
- Never repeat what was just shown (check last_shown).

Reply ONLY valid JSON: {"show": boolean, "agent_id": string|null, "line1": string, "line2": string, "reasoning": string}
reasoning = one sentence explaining your choice (for the demo panel).
"""


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


async def apply_demo_arbitration() -> dict[str, Any]:
    """
    Deterministic fast path for the demo panel.
    This makes synthetic scenarios reliable even if the LLM arbitrator is slow.
    """
    signals = await blackboard.read_active()
    blackboard.total_evaluated += 1

    if not signals:
        blackboard.last_arbitrator_reasoning = "Demo rule: no active signals."
        return {"show": False, "reasoning": blackboard.last_arbitrator_reasoning}

    top = signals[0]
    if _attention_state == "focused" and top.priority < 8:
        blackboard.total_suppressed += 1
        blackboard.last_arbitrator_reasoning = (
            f"Demo rule: focused mode suppresses {top.agent_id} priority {top.priority}."
        )
        return {"show": False, "agent_id": top.agent_id, "reasoning": blackboard.last_arbitrator_reasoning}

    if top.priority <= 2:
        blackboard.total_suppressed += 1
        blackboard.last_arbitrator_reasoning = (
            f"Demo rule: {top.agent_id} priority {top.priority} is low-noise, so stay silent."
        )
        return {"show": False, "agent_id": top.agent_id, "reasoning": blackboard.last_arbitrator_reasoning}

    if top.priority >= 6:
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
    return {"show": False, "agent_id": top.agent_id, "reasoning": blackboard.last_arbitrator_reasoning}


async def arbitrator_loop(blackboard: Blackboard, ws_manager: G2WebSocketManager) -> None:
    """
    Reads the blackboard every 10s.
    Calls DeepSeek V3 (fast chat model) to decide what to show — R1's reasoning
    latency (15-30s) is too long for a 10s cycle and the decision here is
    constrained enough that V3 handles it cleanly.
    Pushes HUD commands to all connected G2 clients.
    Runs independently, forever.
    """
    logger.info("[arbitrator] started")
    last_shown_agent: str | None = None

    while True:
        await asyncio.sleep(INTERVAL_ARBITRATOR)
        try:
            signals = await blackboard.read_active()

            if not signals:
                await asyncio.sleep(0)
                continue

            blackboard.total_evaluated += 1

            context_lines = [
                f"attentionState={_attention_state}",
                f"last_shown={last_shown_agent or 'none'}",
                f"time={datetime.now().strftime('%H:%M')}",
                "",
                "Active signals:",
            ]
            for s in signals[:6]:
                context_lines.append(
                    f"  agent={s.agent_id} priority={s.priority} category={s.category} data={json.dumps(s.data)}"
                )

            raw = await _llm(
                prompt="\n".join(context_lines),
                system=ARBITRATOR_SYSTEM,
                model=MODEL_FAST,
                max_tokens=200,
            )

            if not raw:
                continue

            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            action = json.loads(clean)

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


# ── Public API ────────────────────────────────────────────────────────────────

# Singletons shared across the server
blackboard = Blackboard()
ws_manager = G2WebSocketManager()


async def start_g2_agents() -> list[asyncio.Task]:
    """
    Launch all agents as independent asyncio tasks.
    Call this from the inbox_server lifespan.
    Returns task list so they can be cancelled on shutdown.
    """
    tasks = [
        asyncio.create_task(calendar_agent(blackboard),   name="g2:calendar"),
        asyncio.create_task(transcript_agent(blackboard), name="g2:transcript"),
        asyncio.create_task(messages_agent(blackboard),   name="g2:messages"),
        asyncio.create_task(reminders_agent(blackboard),  name="g2:reminders"),
        asyncio.create_task(context_agent(blackboard),    name="g2:context"),
        asyncio.create_task(arbitrator_loop(blackboard, ws_manager), name="g2:arbitrator"),
    ]
    logger.info(f"[g2] {len(tasks)} agents started independently")
    return tasks
