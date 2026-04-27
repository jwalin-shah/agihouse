"""Tests for g2.vad SpeechGate state machine and g2.audio_pipeline framing.

Both layers are exercised without torch/silero by injecting fake VAD probabilities
and a fake transcribe function. This lets CI run on a clean machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from g2.audio_pipeline import (  # noqa: E402
    FRAME_BYTES,
    GlassesAudioPipeline,
    WEARER_RMS_FLOOR,
)
from g2.vad import FRAME_MS, FRAME_SAMPLES, SpeechGate  # noqa: E402


# ── SpeechGate state-machine tests (mirror agihouse/tests/test_vad.py) ────────


def _silent_frame() -> np.ndarray:
    return np.zeros(FRAME_SAMPLES, dtype=np.int16)


def _voiced_frame(value: int = 5_000) -> np.ndarray:
    return np.full(FRAME_SAMPLES, value, dtype=np.int16)


def _stub(probs: list[float]):
    it = iter(probs)
    return lambda _frame: next(it)


def _drive(gate: SpeechGate, n_frames: int, frame=_silent_frame):
    segs = []
    for _ in range(n_frames):
        segs += gate.feed(frame())
    return segs


def test_basic_segment_isolation():
    pattern = [0.0] * 5 + [1.0] * 15 + [0.0] * 20 + [1.0] * 15 + [0.0] * 5
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=False)
    segs = _drive(gate, len(pattern))
    segs += gate.flush()

    assert len(segs) == 2
    assert segs[0].start_ms == 5 * FRAME_MS == 160
    assert segs[0].end_ms == 33 * FRAME_MS == 1056
    assert segs[1].start_ms == 40 * FRAME_MS == 1280
    assert segs[1].end_ms == 60 * FRAME_MS == 1920


def test_short_blip_does_not_confirm():
    pattern = [0.0] * 5 + [1.0] * 5 + [0.0] * 5 + [1.0] * 5 + [0.0] * 5
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=False)
    segs = _drive(gate, len(pattern))
    segs += gate.flush()
    assert segs == []


def test_keep_audio_concatenates_frames():
    n_pre, n_speech, n_post = 5, 25, 13
    pattern = [0.0] * n_pre + [1.0] * n_speech + [0.0] * n_post
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=True)

    segs = []
    for i in range(len(pattern)):
        frame = _voiced_frame() if pattern[i] >= 0.5 else _silent_frame()
        segs += gate.feed(frame)

    assert len(segs) == 1
    seg = segs[0]
    assert seg.audio is not None
    expected_speech_ms = (n_speech + n_post) * FRAME_MS
    assert seg.duration_ms == expected_speech_ms
    assert seg.audio.shape == ((n_speech + n_post) * FRAME_SAMPLES,)
    assert (seg.audio[: n_speech * FRAME_SAMPLES] != 0).all()
    assert (seg.audio[n_speech * FRAME_SAMPLES :] == 0).all()


def test_clear_zeroes_buffer_after_emit():
    pattern = [1.0] * 8 + [0.0] * 13
    gate = SpeechGate(vad_fn=_stub(pattern))
    segs = []
    for i in range(len(pattern)):
        segs += gate.feed(_voiced_frame() if pattern[i] >= 0.5 else _silent_frame())
    assert len(segs) == 1
    assert gate._buf == []


def test_max_segment_ms_caps_long_run():
    gate = SpeechGate(
        vad_fn=lambda _frame: 1.0,
        keep_audio=False,
        max_segment_ms=320,
    )
    segs = _drive(gate, 30, frame=_voiced_frame)
    assert 1 <= len(segs) <= 3
    assert all(s.duration_ms <= gate.max_segment_ms for s in segs)


def test_rejects_wrong_shape():
    gate = SpeechGate(vad_fn=lambda _f: 0.0)
    with pytest.raises(ValueError):
        gate.feed(np.zeros(256, dtype=np.int16))


def test_rejects_wrong_dtype():
    gate = SpeechGate(vad_fn=lambda _f: 0.0)
    with pytest.raises(TypeError):
        gate.feed(np.zeros(FRAME_SAMPLES, dtype=np.float32))


def test_flush_without_open_segment_is_noop():
    gate = SpeechGate(vad_fn=lambda _f: 0.0)
    _drive(gate, 5)
    assert gate.flush() == []


# ── GlassesAudioPipeline tests ───────────────────────────────────────────────


def _voiced_pcm_bytes(n_frames: int, value: int = 8_000) -> bytes:
    """Produce ``n_frames`` of voiced int16 PCM bytes above WEARER_RMS_FLOOR."""
    arr = np.full(n_frames * FRAME_SAMPLES, value, dtype=np.int16)
    return arr.tobytes()


def test_pipeline_publishes_transcribed_text(monkeypatch):
    """End-to-end: feed enough voiced PCM to trigger one segment, assert publish."""
    pattern_count = 22  # 8 confirm + 13 hangover + 1 extra closing frame is enough
    pattern = [1.0] * 9 + [0.0] * 13
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=True)

    published: list[tuple[str, str]] = []

    def fake_publish(text: str, source: str = "laptop") -> None:
        published.append((text, source))

    pipe = GlassesAudioPipeline(
        gate=gate,
        transcribe_fn=lambda audio: "remind me about Anita",
        publish=fake_publish,
    )

    pcm = _voiced_pcm_bytes(pattern_count)
    pipe.feed(pcm)

    pipe._exec.shutdown(wait=True)

    assert len(published) == 1
    assert published[0] == ("remind me about Anita", "g2")


def test_pipeline_drops_quiet_segments():
    """RMS below WEARER_RMS_FLOOR should never reach the transcriber."""
    pattern = [1.0] * 9 + [0.0] * 13
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=True)
    transcribed: list[np.ndarray] = []

    def fake_transcribe(audio: np.ndarray) -> str:
        transcribed.append(audio)
        return "should not happen"

    pipe = GlassesAudioPipeline(
        gate=gate,
        transcribe_fn=fake_transcribe,
        publish=lambda text, source="laptop": None,
    )

    quiet_value = max(1, WEARER_RMS_FLOOR // 4)
    pcm = _voiced_pcm_bytes(22, value=quiet_value)
    pipe.feed(pcm)
    pipe._exec.shutdown(wait=True)

    assert transcribed == []


def test_pipeline_frames_partial_pcm_correctly():
    """Half-frame writes must be buffered, not dropped."""
    gate = SpeechGate(vad_fn=lambda _f: 0.0, keep_audio=True)
    pipe = GlassesAudioPipeline(
        gate=gate,
        transcribe_fn=lambda audio: "x",
        publish=lambda text, source="laptop": None,
    )

    half = FRAME_BYTES // 2
    pipe.feed(b"\x00" * half)
    assert len(pipe._byte_buf) == half
    pipe.feed(b"\x00" * half)
    assert len(pipe._byte_buf) == 0
