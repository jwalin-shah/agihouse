"""Phone/glasses-derived signals (IMU attention + wearing state).

Replaces the module-level globals that used to live in g2_agent.py
(_attention_state, _wearing_state, _wearing_changed_at, _last_wearing_brief_at).
Now there's a single owned object you can pass around and test.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

AttentionState = Literal["ambient", "focused"]


@dataclass
class DeviceState:
    """Live state from the G2 phone WebView, shared with all agents."""

    attention_state: AttentionState = "ambient"
    wearing: bool = False
    wearing_changed_at: float = 0.0
    last_wearing_brief_at: float = 0.0

    # Computed flag the context_agent reads to decide whether to fire a brief.
    wearing_brief_throttle_seconds: float = 600.0
    wearing_edge_window_seconds: float = 5.0

    # Bookkeeping for tests / demo panel
    last_imu_at: float = field(default=0.0)
    last_wearing_msg_at: float = field(default=0.0)

    def update_attention(self, attention: AttentionState) -> None:
        self.attention_state = attention
        self.last_imu_at = time.time()

    def update_wearing(self, is_wearing: bool) -> None:
        now = time.time()
        if is_wearing != self.wearing:
            self.wearing = is_wearing
            self.wearing_changed_at = now
        self.last_wearing_msg_at = now

    def should_fire_wearing_brief(self) -> bool:
        """True iff glasses just went on and we haven't fired a brief recently."""
        if not self.wearing or self.wearing_changed_at <= 0:
            return False
        now = time.time()
        recent_edge = (now - self.wearing_changed_at) < self.wearing_edge_window_seconds
        cooled_down = (now - self.last_wearing_brief_at) > self.wearing_brief_throttle_seconds
        return recent_edge and cooled_down

    def mark_wearing_brief_fired(self) -> None:
        self.last_wearing_brief_at = time.time()
