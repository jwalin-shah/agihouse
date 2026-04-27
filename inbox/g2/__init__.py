"""
G2 Ambient Copilot — multi-agent system for the Even Realities G2.

Public API mirrors the old `g2_agent.py` module so callers like
`inbox_server.py` keep working with `import g2` after the split.

Notable changes vs. the old monolith:
- agents live in inbox/g2/agents/*.py (one file per concern)
- attention/wearing state is owned by a DeviceState object, not module globals
- the transcript agent subscribes to TranscriptBus instead of polling /ambient/transcript
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

# Load .env from inbox/ early so settings see the keys.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

from .agents import (  # noqa: E402
    calendar_agent,
    context_agent,
    messages_agent,
    reminders_agent,
    transcript_agent,
    voice_actions_agent,
)
from . import actions  # noqa: E402, F401
from . import audio_pipeline  # noqa: E402, F401
from . import audit  # noqa: E402, F401
from .arbitrator import apply_demo_arbitration as _apply_demo_arbitration  # noqa: E402
from .arbitrator import arbitrator_loop  # noqa: E402
from .blackboard import Blackboard, Signal  # noqa: E402
from .device_state import DeviceState  # noqa: E402
from .transcript_bus import transcript_bus  # noqa: E402
from .ws_manager import G2WebSocketManager  # noqa: E402

# ── Singletons ──────────────────────────────────────────────────────────────
# These are constructed at import time so existing callers
# (inbox_server.py routes) keep working unchanged.

device_state = DeviceState()
blackboard = Blackboard()
ws_manager = G2WebSocketManager(device_state=device_state)


async def apply_demo_arbitration() -> dict:
    """Backward-compatible wrapper used by /g2/signal in inbox_server.py."""
    return await _apply_demo_arbitration(blackboard, ws_manager, device_state)


async def start_g2_agents() -> list[asyncio.Task]:
    """Launch every agent + the arbitrator as independent asyncio tasks."""
    loop = asyncio.get_running_loop()
    # Bind the transcript bus to the running loop so AmbientService's sync
    # capture thread can publish into it via run_coroutine_threadsafe.
    transcript_bus.bind_loop(loop)
    # Wire actions.dispatch so verbs can echo Signals back into the
    # blackboard from any thread (audio worker, demo panel, regex agent).
    actions.bind(loop=loop, blackboard=blackboard)

    tasks = [
        asyncio.create_task(calendar_agent(blackboard), name="g2:calendar"),
        asyncio.create_task(transcript_agent(blackboard), name="g2:transcript"),
        asyncio.create_task(messages_agent(blackboard), name="g2:messages"),
        asyncio.create_task(reminders_agent(blackboard), name="g2:reminders"),
        asyncio.create_task(context_agent(blackboard, device_state), name="g2:context"),
        asyncio.create_task(voice_actions_agent(), name="g2:voice_actions"),
        asyncio.create_task(
            arbitrator_loop(blackboard, ws_manager, device_state), name="g2:arbitrator"
        ),
    ]
    from loguru import logger

    logger.info(f"[g2] {len(tasks)} agents started independently")
    return tasks


__all__ = [
    "Blackboard",
    "DeviceState",
    "G2WebSocketManager",
    "Signal",
    "actions",
    "apply_demo_arbitration",
    "audio_pipeline",
    "audit",
    "blackboard",
    "device_state",
    "start_g2_agents",
    "transcript_bus",
    "ws_manager",
]
