"""SpeechGate state-machine tests with a stubbed VAD (no torch needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vad import FRAME_MS, FRAME_SAMPLES, SpeechGate  # noqa: E402


def _silent_frame() -> np.ndarray:
    return np.zeros(FRAME_SAMPLES, dtype=np.int16)


def _voiced_frame(value: int = 5_000) -> np.ndarray:
    # Content doesn't matter for tests — VAD is stubbed — but we use a non-zero
    # signal to make buffer-content assertions meaningful.
    return np.full(FRAME_SAMPLES, value, dtype=np.int16)


def _stub(probs: list[float]):
    """Build a VAD fn that emits a fixed sequence of probabilities."""
    it = iter(probs)
    return lambda _frame: next(it)


def _drive(gate: SpeechGate, n_frames: int, frame=_silent_frame):
    segs = []
    for _ in range(n_frames):
        segs += gate.feed(frame())
    return segs


def test_basic_segment_isolation():
    # 5 silent, 15 speech, 20 silent, 15 speech, 5 silent
    pattern = [0.0] * 5 + [1.0] * 15 + [0.0] * 20 + [1.0] * 15 + [0.0] * 5
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=False)
    segs = _drive(gate, len(pattern))
    segs += gate.flush()

    assert len(segs) == 2
    # Segment 1: speech began at frame 5 (=160 ms). Hangover (13 silent frames)
    # closes it after frame index 32, so end = 33 * FRAME_MS = 1056.
    assert segs[0].start_ms == 5 * FRAME_MS == 160
    assert segs[0].end_ms == 33 * FRAME_MS == 1056
    # Segment 2: speech began at frame 40 (=1280 ms). Stream ends with only
    # 5 trailing silent frames — flush() closes at 60 * FRAME_MS = 1920.
    assert segs[1].start_ms == 40 * FRAME_MS == 1280
    assert segs[1].end_ms == 60 * FRAME_MS == 1920


def test_short_blip_does_not_confirm():
    # Two 5-frame speech bursts, neither reaches min_speech_ms=256 (8 frames).
    pattern = [0.0] * 5 + [1.0] * 5 + [0.0] * 5 + [1.0] * 5 + [0.0] * 5
    gate = SpeechGate(vad_fn=_stub(pattern), keep_audio=False)
    segs = _drive(gate, len(pattern))
    segs += gate.flush()
    assert segs == []


def test_keep_audio_concatenates_frames():
    # 5 silent then 25 speech then enough silence to close.
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
    # Buffer holds exactly the speech frames (silence after confirmation
    # is included up through the closing frame).
    expected_speech_ms = (n_speech + n_post) * FRAME_MS
    assert seg.duration_ms == expected_speech_ms
    assert seg.audio.shape == ((n_speech + n_post) * FRAME_SAMPLES,)
    # Voiced portion is non-zero, trailing silence is zero.
    assert (seg.audio[: n_speech * FRAME_SAMPLES] != 0).all()
    assert (seg.audio[n_speech * FRAME_SAMPLES :] == 0).all()


def test_clear_zeroes_buffer_after_emit():
    pattern = [1.0] * 8 + [0.0] * 13   # confirm + close
    gate = SpeechGate(vad_fn=_stub(pattern))
    segs = []
    for i in range(len(pattern)):
        segs += gate.feed(_voiced_frame() if pattern[i] >= 0.5 else _silent_frame())
    assert len(segs) == 1
    # _emit calls clear() — internal buffer must be empty post-emit.
    assert gate._buf == []


def test_max_segment_ms_caps_long_run():
    # Continuous speech longer than max_segment_ms must force a close.
    gate = SpeechGate(
        vad_fn=lambda _frame: 1.0,
        keep_audio=False,
        max_segment_ms=320,   # 10 frames
    )
    n = 30
    segs = _drive(gate, n, frame=_voiced_frame)
    # First close at frame index 9 (seg_dur reaches 320 ms). Then a fresh
    # confirmation needs another 8 frames of speech, so we expect at least
    # one but no more than three caps in 30 frames.
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
