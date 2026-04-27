"""In-process pub/sub for ambient transcript segments.

The AmbientService capture loop runs in a sync thread, so we can't directly
await an asyncio.Queue. Instead we expose `publish_from_thread(text, source)`
which hops onto the bound asyncio loop via `run_coroutine_threadsafe`.

Subscribers get their own bounded asyncio.Queue and consume with
`async for segment in bus.stream():`.

The `source` tag (default ``"laptop"``) lets downstream agents differentiate
between MLX-laptop-mic and G2-mic transcripts. Wearer-voice agents (recall,
voice_actions) bias toward ``"g2"``; existing string-only subscribers remain
unchanged via the legacy `subscribe()` channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    source: str = "laptop"


class TranscriptBus:
    def __init__(self, queue_size: int = 100) -> None:
        self._text_subs: set[asyncio.Queue[str]] = set()
        self._tagged_subs: set[asyncio.Queue[TranscriptSegment]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue_size = queue_size

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Call once from inside the event loop you want to publish on."""
        self._loop = loop

    # ── Legacy text-only API (still used by transcript_agent) ──────────────

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        self._text_subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._text_subs.discard(q)

    @asynccontextmanager
    async def stream(self) -> AsyncIterator[asyncio.Queue[str]]:
        q = self.subscribe()
        try:
            yield q
        finally:
            self.unsubscribe(q)

    # ── Tagged API for source-aware consumers ──────────────────────────────

    def subscribe_with_source(self) -> asyncio.Queue[TranscriptSegment]:
        q: asyncio.Queue[TranscriptSegment] = asyncio.Queue(maxsize=self._queue_size)
        self._tagged_subs.add(q)
        return q

    def unsubscribe_with_source(self, q: asyncio.Queue[TranscriptSegment]) -> None:
        self._tagged_subs.discard(q)

    @asynccontextmanager
    async def stream_with_source(self) -> AsyncIterator[asyncio.Queue[TranscriptSegment]]:
        q = self.subscribe_with_source()
        try:
            yield q
        finally:
            self.unsubscribe_with_source(q)

    # ── Publish path ───────────────────────────────────────────────────────

    async def publish(self, segment: str, source: str = "laptop") -> None:
        if not segment:
            return
        for q in list(self._text_subs):
            _put_or_drop(q, segment)
        seg = TranscriptSegment(text=segment, source=source)
        for q in list(self._tagged_subs):
            _put_or_drop(q, seg)

    def publish_from_thread(self, segment: str, source: str = "laptop") -> None:
        """Thread-safe entry point used by AmbientService._capture_loop and
        the G2-mic audio_pipeline worker thread.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.publish(segment, source), loop)
        except RuntimeError:
            logger.debug("[transcript_bus] loop not running; segment dropped")


def _put_or_drop(q: asyncio.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        # Drop oldest then re-try; subscriber is too slow.
        try:
            q.get_nowait()
            q.put_nowait(item)
        except Exception:
            pass


# Singleton — the rest of the package imports this directly.
transcript_bus = TranscriptBus()
