"""G2-mic audio pipeline — separate from the laptop-mic AmbientService.

Bytes arrive at ``POST /g2/audio`` as 16 kHz s16le mono PCM. We frame the
stream into 512-sample chunks, gate through Silero VAD, and only send
confirmed speech segments to Groq's whisper-large-v3 API. The transcript is
published onto ``transcript_bus`` with ``source="g2"`` so downstream agents
(transcript / voice_actions / voice_recall) can pick it up.

Unlike the original `agihouse` pipeline, this module deliberately does
**not** call the extractor or recall directly — those concerns belong to
their own agents. The pipeline is strictly *PCM bytes -> transcribed text*.

Usage from ``inbox_server.py``::

    from inbox import g2
    g2.audio_pipeline.feed(pcm_bytes)
"""

from __future__ import annotations

import io
import os
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np

from .transcript_bus import transcript_bus
from .vad import FRAME_SAMPLES, SAMPLE_RATE, SpeechGate

FRAME_BYTES = FRAME_SAMPLES * 2          # 1024 bytes per VAD frame (int16 mono)
MIN_SEGMENT_MS = 300                     # skip very short blips (cough, click)
# Wearer's voice is closer to the G2 mic so the wearer is louder. Ambient /
# distant-speaker segments tend to be quieter; this RMS floor drops them
# before paying cloud-ASR cost. int16 RMS ~800 corresponds to ~-32 dBFS.
WEARER_RMS_FLOOR = 800
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
GROQ_TIMEOUT_SECONDS = 15.0


def _pcm_to_wav_bytes(pcm_int16: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


class GlassesAudioPipeline:
    """VAD-gated, cloud-transcribed pipeline from G2 mic to ``transcript_bus``."""

    def __init__(
        self,
        *,
        gate: SpeechGate | None = None,
        transcribe_fn=None,
        publish=transcript_bus.publish_from_thread,
    ) -> None:
        self._byte_buf = bytearray()
        self._lock = threading.Lock()
        self._gate = gate or SpeechGate(keep_audio=True)
        self._exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="g2-asr")
        self._publish = publish
        self._transcribe_override = transcribe_fn
        self._groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not self._groq_key and transcribe_fn is None:
            print(
                "[g2.audio] GROQ_API_KEY not set — G2-mic transcription disabled",
                file=sys.stderr,
            )

    def feed(self, pcm: bytes) -> None:
        """Push raw PCM bytes from /g2/audio. Frames into VAD, emits segments async."""
        if not pcm:
            return
        segments = []
        with self._lock:
            self._byte_buf.extend(pcm)
            while len(self._byte_buf) >= FRAME_BYTES:
                frame_bytes = bytes(self._byte_buf[:FRAME_BYTES])
                del self._byte_buf[:FRAME_BYTES]
                frame = np.frombuffer(frame_bytes, dtype=np.int16)
                segments.extend(self._gate.feed(frame))

        for seg in segments:
            if seg.audio is None or seg.duration_ms < MIN_SEGMENT_MS:
                continue
            self._exec.submit(self._process_segment, seg.audio.copy())

    def flush(self) -> None:
        """Force-close any open segment. Useful for tests / shutdown."""
        with self._lock:
            segments = self._gate.flush()
        for seg in segments:
            if seg.audio is None or seg.duration_ms < MIN_SEGMENT_MS:
                continue
            self._exec.submit(self._process_segment, seg.audio.copy())

    def _process_segment(self, audio: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        if rms < WEARER_RMS_FLOOR:
            print(f"[g2.audio] dropped quiet segment rms={rms:.0f}", file=sys.stderr)
            return
        try:
            text = self._transcribe(audio)
        except Exception as e:
            print(f"[g2.audio] transcribe failed: {e!r}", file=sys.stderr)
            return
        text = (text or "").strip()
        if not text:
            return
        print(f"[g2.audio] heard: {text!r}", file=sys.stderr)
        try:
            self._publish(text, source="g2")
        except Exception as e:
            print(f"[g2.audio] publish failed: {e!r}", file=sys.stderr)

    def _transcribe(self, audio: np.ndarray) -> str:
        if self._transcribe_override is not None:
            return self._transcribe_override(audio)
        if not self._groq_key:
            return ""
        wav_bytes = _pcm_to_wav_bytes(audio)
        r = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self._groq_key}"},
            files={"file": ("seg.wav", wav_bytes, "audio/wav")},
            data={
                "model": GROQ_MODEL,
                "response_format": "text",
                "language": "en",
                "temperature": "0",
            },
            timeout=GROQ_TIMEOUT_SECONDS,
        )
        if r.status_code != 200:
            print(f"[g2.audio] groq {r.status_code}: {r.text[:200]}", file=sys.stderr)
            return ""
        return r.text.strip()


# Module-level singleton, instantiated lazily so importing inbox.g2 doesn't
# require torch / silero. Construction happens on first /g2/audio call.
_pipeline: GlassesAudioPipeline | None = None
_pipeline_lock = threading.Lock()


def get_pipeline() -> GlassesAudioPipeline:
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            _pipeline = GlassesAudioPipeline()
        return _pipeline


def feed(pcm: bytes) -> None:
    """Module-level entry point — :func:`inbox_server` calls this directly."""
    get_pipeline().feed(pcm)


def reset_for_tests(pipeline: GlassesAudioPipeline | None = None) -> None:
    """Override the singleton in tests; pass ``None`` to clear."""
    global _pipeline
    with _pipeline_lock:
        _pipeline = pipeline
