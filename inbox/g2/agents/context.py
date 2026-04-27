"""Context agent — time-of-day triggers + glasses-on edge.

Reads DeviceState (no module globals) so the wearing/attention logic
is fully testable.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger

from ..blackboard import Blackboard, Signal
from ..config import settings
from ..device_state import DeviceState


async def context_agent(blackboard: Blackboard, device_state: DeviceState) -> None:
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

            if h == 9 and m < 5 and "morning" not in fired_today:
                fired_today.add("morning")
                await blackboard.write(
                    Signal(
                        agent_id="context",
                        priority=5,
                        category="system",
                        data={"message": "Morning brief ready", "label": "GOOD MORNING"},
                        ttl=300,
                    )
                )

            elif h == 9 and m >= 55 and "standup" not in fired_today:
                fired_today.add("standup")
                await blackboard.write(
                    Signal(
                        agent_id="context",
                        priority=6,
                        category="system",
                        data={"message": "Standup in 5 min", "label": "STANDUP SOON"},
                        ttl=300,
                    )
                )

            elif h == 17 and m < 5 and "eod" not in fired_today:
                fired_today.add("eod")
                await blackboard.write(
                    Signal(
                        agent_id="context",
                        priority=4,
                        category="system",
                        data={"message": "Wrap up open items", "label": "END OF DAY"},
                        ttl=300,
                    )
                )

            # Wearing detection: glasses just put on → fire morning/context brief.
            if device_state.should_fire_wearing_brief():
                device_state.mark_wearing_brief_fired()
                hh = now.strftime("%H:%M")
                await blackboard.write(
                    Signal(
                        agent_id="context",
                        priority=5,
                        category="system",
                        data={"label": f"○ HELLO  {hh}", "message": "Glasses on"},
                        ttl=20,
                    )
                )

        except Exception as e:
            logger.warning(f"[context_agent] error: {e}")

        await asyncio.sleep(settings.interval_context)
