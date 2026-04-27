"""voice_actions agent — turns ambient speech into deterministic actions.

Subscribes to the ``transcript_bus`` (source-aware), keeps a short rolling
window of recent segments, asks ``g2.extractor.extract`` whether the wearer
just issued an actionable directive, and forwards the resulting verb to
``g2.actions.dispatch``. The dispatcher gates on policy + audit, so this
agent never has to second-guess the rule layer.

The agent is registered in ``start_g2_agents`` *after* ``voice_recall``
because regex-driven recall fires sub-100ms while this agent waits for the
LLM. Cheap recall hits beat the lens before the LLM call even starts.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from loguru import logger

from .. import actions, extractor
from ..config import settings
from ..transcript_bus import TranscriptSegment, transcript_bus

WINDOW_SEGMENTS = 4


async def voice_actions_agent() -> None:
    logger.info("[voice_actions] started (event-driven)")
    window: deque[TranscriptSegment] = deque(maxlen=WINDOW_SEGMENTS)
    last_eval_at = 0.0
    last_text = ""

    async with transcript_bus.stream_with_source() as queue:
        while True:
            try:
                seg = await queue.get()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[voice_actions] queue error: {e}")
                await asyncio.sleep(1.0)
                continue

            text = (seg.text or "").strip()
            if not text:
                continue
            window.append(seg)

            now = time.time()
            if (now - last_eval_at) < settings.extractor_min_gap_seconds:
                continue

            joined = " ".join(s.text for s in window).strip()
            if len(joined) < settings.extractor_min_chars or joined == last_text:
                continue

            last_eval_at = now
            last_text = joined

            try:
                event = await extractor.extract(joined)
            except Exception as e:
                logger.warning(f"[voice_actions] extractor failed: {e}")
                continue
            if not event:
                continue

            verb = event["action"]
            payload = event.get("payload") or {}
            logger.info(
                f"[voice_actions] dispatching {verb} (conf={event['confidence']:.2f}) source={seg.source}"
            )
            try:
                await asyncio.to_thread(actions.dispatch, verb, dict(payload))
            except Exception as e:
                logger.warning(f"[voice_actions] dispatch failed: {e}")
