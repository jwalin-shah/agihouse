"""WebSocket fan-out + inbound message routing for the G2 phone WebView."""

from __future__ import annotations

import contextlib

from fastapi import WebSocket
from loguru import logger

from .device_state import DeviceState


class G2WebSocketManager:
    """Tracks all connected G2 phone WebView clients and pushes HUD updates."""

    def __init__(self, device_state: DeviceState | None = None) -> None:
        self._connections: set[WebSocket] = set()
        self._last_hud: dict | None = None  # for reconnect replay
        self.device_state: DeviceState = device_state or DeviceState()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"[g2-ws] client connected ({len(self._connections)} total)")
        # Replay last HUD state so reconnects don't blank the screen
        if self._last_hud is not None:
            with contextlib.suppress(Exception):
                await ws.send_json(self._last_hud)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info(f"[g2-ws] client disconnected ({len(self._connections)} remaining)")

    async def broadcast_hud(self, line1: str, line2: str) -> None:
        payload = {"type": "hud", "line1": line1, "line2": line2}
        self._last_hud = payload
        await self._send_to_all(payload)

    async def broadcast_clear(self) -> None:
        payload = {"type": "clear"}
        self._last_hud = None
        await self._send_to_all(payload)

    async def _send_to_all(self, payload: dict) -> None:
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._connections -= dead

    def handle_message(self, data: dict) -> None:
        """Handle messages coming FROM the G2 phone (IMU, wearing state)."""
        msg_type = data.get("type")
        if msg_type == "imu":
            attention = data.get("attentionState", "ambient")
            if attention in ("focused", "ambient"):
                self.device_state.update_attention(attention)
        elif msg_type == "wearing":
            self.device_state.update_wearing(bool(data.get("isWearing", False)))
