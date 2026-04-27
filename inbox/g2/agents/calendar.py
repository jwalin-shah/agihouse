"""Calendar agent — surfaces meetings within 10 minutes."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
from loguru import logger

from ..blackboard import Blackboard, Signal
from ..config import settings


async def calendar_agent(blackboard: Blackboard) -> None:
    """
    Polls Google Calendar every 60s via inbox server.
    Writes a signal when a meeting is within 10 minutes.
    Priority scales with urgency: 4 at 10min → 9 at 2min.
    """
    logger.info("[calendar_agent] started")
    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{settings.inbox_url}/calendar/events?date={today}")
                events = resp.json() if resp.status_code == 200 else []

            now = datetime.now()
            for event in events:
                start_raw = event.get("start_time") or event.get("start") or ""
                if not start_raw:
                    continue
                try:
                    start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).replace(
                        tzinfo=None
                    )
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

                await blackboard.write(
                    Signal(
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
                    )
                )
                break  # only surface the next meeting

        except Exception as e:
            logger.warning(f"[calendar_agent] error: {e}")

        await asyncio.sleep(settings.interval_calendar)
