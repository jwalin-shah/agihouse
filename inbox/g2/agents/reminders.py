"""Reminders agent — Apple Reminders due within 30 minutes or overdue."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
from loguru import logger

from ..blackboard import Blackboard, Signal
from ..config import settings


async def reminders_agent(blackboard: Blackboard) -> None:
    logger.info("[reminders_agent] started")

    while True:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    f"{settings.inbox_url}/reminders?show_completed=false&limit=50"
                )
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

                await blackboard.write(
                    Signal(
                        agent_id="reminders",
                        priority=priority,
                        category="reminder",
                        data={
                            "title": item.get("title", "Reminder")[:28],
                            "label": label,
                        },
                        ttl=60,
                    )
                )
                break

        except Exception as e:
            logger.warning(f"[reminders_agent] error: {e}")

        await asyncio.sleep(settings.interval_reminders)
