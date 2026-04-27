"""Transcript agent — event-driven via TranscriptBus.

Subscribes to the bus that AmbientService pushes new ASR segments into.
Coalesces a rolling window of recent segments and asks the LLM to extract
one actionable insight, then writes a Signal to the blackboard.

No more polling /ambient/transcript. As soon as a chunk transcribes the
agent reacts.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque

from loguru import logger

from ..blackboard import Blackboard, Signal
from ..config import TRANSCRIPT_SYSTEM, settings
from ..llm import call_llm, strip_code_fence
from ..transcript_bus import transcript_bus

# Rolling window of recent segments. We re-evaluate each time a new segment
# arrives, but cap how often we hit the LLM so a chatty room doesn't melt
# the budget.
WINDOW_SEGMENTS = 4
MIN_LLM_GAP_SECONDS = 8.0


async def transcript_agent(blackboard: Blackboard) -> None:
    logger.info("[transcript_agent] started (event-driven)")
    window: deque[str] = deque(maxlen=WINDOW_SEGMENTS)
    last_eval_at = 0.0
    last_text = ""

    async with transcript_bus.stream() as queue:
        while True:
            try:
                segment = await queue.get()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[transcript_agent] queue error: {e}")
                await asyncio.sleep(1.0)
                continue

            segment = (segment or "").strip()
            if not segment:
                continue
            window.append(segment)

            # Throttle LLM calls — coalesce bursts of segments.
            now = time.time()
            if (now - last_eval_at) < MIN_LLM_GAP_SECONDS:
                continue

            text = " ".join(window).strip()
            if len(text) < settings.transcript_min_chars or text == last_text:
                continue

            last_eval_at = now
            last_text = text

            try:
                raw = await call_llm(
                    prompt=text,
                    system=TRANSCRIPT_SYSTEM,
                    model=settings.model_fast,
                    max_tokens=80,
                )
                if not raw:
                    continue
                parsed = json.loads(strip_code_fence(raw))
                insight = parsed.get("insight")
                if insight:
                    await blackboard.write(
                        Signal(
                            agent_id="transcript",
                            priority=int(parsed.get("priority", 2)),
                            category="insight",
                            data={"insight": str(insight)[:56]},
                            ttl=90,
                        )
                    )
            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.warning(f"[transcript_agent] error: {e}")
