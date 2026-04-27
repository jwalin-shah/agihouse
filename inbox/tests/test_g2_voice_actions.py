"""Tests for the voice_actions agent — bus -> extractor -> dispatch glue."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from g2 import actions  # noqa: E402
from g2.agents import voice_actions  # noqa: E402

transcript_bus_module = importlib.import_module("g2.transcript_bus")


def _loosen_settings(monkeypatch) -> None:
    """Replace voice_actions.settings with a copy whose windows are tiny."""
    cur = voice_actions.settings
    loose = dataclasses.replace(
        cur,
        extractor_min_chars=4,
        extractor_min_gap_seconds=0.0,
    )
    monkeypatch.setattr(voice_actions, "settings", loose)


def test_voice_actions_dispatches_extracted_verb(monkeypatch):
    """Push a transcript onto the bus, assert dispatch fires once with payload."""

    bus = transcript_bus_module.TranscriptBus()
    monkeypatch.setattr(transcript_bus_module, "transcript_bus", bus)
    monkeypatch.setattr(voice_actions, "transcript_bus", bus)

    fake_extract = AsyncMock(
        return_value={
            "action": "create_reminder",
            "payload": {"title": "buy milk"},
            "confidence": 0.92,
            "reason": "ok",
        }
    )
    monkeypatch.setattr(voice_actions.extractor, "extract", fake_extract)

    dispatched: list = []
    fake_dispatch = MagicMock(side_effect=lambda v, p: dispatched.append((v, p)))
    monkeypatch.setattr(actions, "dispatch", fake_dispatch)
    monkeypatch.setattr(voice_actions, "actions", actions)

    _loosen_settings(monkeypatch)

    async def scenario():
        bus.bind_loop(asyncio.get_running_loop())
        task = asyncio.create_task(voice_actions.voice_actions_agent())
        await asyncio.sleep(0.05)
        await bus.publish("remind me to buy milk tomorrow", source="g2")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if dispatched:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.new_event_loop().run_until_complete(scenario())

    assert fake_extract.await_count >= 1
    assert dispatched == [("create_reminder", {"title": "buy milk"})]


def test_voice_actions_skips_when_extractor_returns_none(monkeypatch):
    bus = transcript_bus_module.TranscriptBus()
    monkeypatch.setattr(transcript_bus_module, "transcript_bus", bus)
    monkeypatch.setattr(voice_actions, "transcript_bus", bus)

    monkeypatch.setattr(voice_actions.extractor, "extract", AsyncMock(return_value=None))

    fake_dispatch = MagicMock()
    monkeypatch.setattr(actions, "dispatch", fake_dispatch)
    monkeypatch.setattr(voice_actions, "actions", actions)

    _loosen_settings(monkeypatch)

    async def scenario():
        bus.bind_loop(asyncio.get_running_loop())
        task = asyncio.create_task(voice_actions.voice_actions_agent())
        await asyncio.sleep(0.05)
        await bus.publish("just chatting today", source="g2")
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.new_event_loop().run_until_complete(scenario())

    fake_dispatch.assert_not_called()


def test_voice_actions_swallows_extractor_exceptions(monkeypatch):
    bus = transcript_bus_module.TranscriptBus()
    monkeypatch.setattr(transcript_bus_module, "transcript_bus", bus)
    monkeypatch.setattr(voice_actions, "transcript_bus", bus)

    raises = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(voice_actions.extractor, "extract", raises)
    fake_dispatch = MagicMock()
    monkeypatch.setattr(actions, "dispatch", fake_dispatch)
    monkeypatch.setattr(voice_actions, "actions", actions)

    _loosen_settings(monkeypatch)

    async def scenario():
        bus.bind_loop(asyncio.get_running_loop())
        task = asyncio.create_task(voice_actions.voice_actions_agent())
        await asyncio.sleep(0.05)
        await bus.publish("remind me to do the thing", source="g2")
        await asyncio.sleep(0.2)
        # Agent must still be running; bus publish #2 still gets handled.
        await bus.publish("show me reminders", source="g2")
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.new_event_loop().run_until_complete(scenario())
    # Two attempts, each raised; never reached dispatch.
    assert raises.await_count >= 1
    fake_dispatch.assert_not_called()
