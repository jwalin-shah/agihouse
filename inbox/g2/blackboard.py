"""Shared blackboard — every agent writes Signals here, the arbitrator reads."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


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
    """Shared state for all agents. Async-safe via asyncio.Lock."""

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
            self._signals[signal.agent_id] = signal  # one live signal per agent
            self._prune()
            logger.info(
                f"[blackboard] {signal.agent_id} wrote p={signal.priority} {signal.category}"
            )

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
