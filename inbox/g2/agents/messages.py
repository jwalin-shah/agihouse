"""Messages agent — surfaces only urgent/VIP unread iMessage + Gmail."""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from ..blackboard import Blackboard, Signal
from ..config import settings

URGENT_WORDS = {"urgent", "asap", "emergency", "now", "help", "call me"}


async def messages_agent(blackboard: Blackboard) -> None:
    logger.info("[messages_agent] started")
    seen_ids: set[str] = set()

    while True:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{settings.inbox_url}/conversations?source=all&limit=20")
                convos = resp.json() if resp.status_code == 200 else []

            urgent: list[dict] = []
            for c in convos:
                if not c.get("unread_count", 0):
                    continue
                cid = c.get("id", "")
                if cid in seen_ids:
                    continue

                name = c.get("name") or c.get("contact_name") or "Someone"
                snippet = (c.get("snippet") or c.get("last_message") or "")[:60]

                if any(w in snippet.lower() for w in URGENT_WORDS):
                    seen_ids.add(cid)
                    urgent.append({"name": name, "snippet": snippet, "priority": 7})
                    continue

                # Otherwise low priority — don't LLM every message
                seen_ids.add(cid)
                urgent.append({"name": name, "snippet": snippet, "priority": 2})

            if urgent:
                top = max(urgent, key=lambda x: x["priority"])
                if top["priority"] >= 5:
                    await blackboard.write(
                        Signal(
                            agent_id="messages",
                            priority=top["priority"],
                            category="message",
                            data={
                                "from": top["name"][:20],
                                "preview": top["snippet"][:28],
                            },
                            ttl=120,
                        )
                    )

        except Exception as e:
            logger.warning(f"[messages_agent] error: {e}")

        await asyncio.sleep(settings.interval_messages)
