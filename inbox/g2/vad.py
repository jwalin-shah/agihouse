"""On-device VAD gate (Silero) for the G2-mic audio path.

Audio path goal:

    G2 mic frames  ->  SpeechGate  ->  speech-only segments  ->  Whisper

The MLX laptop-mic path already handles its own segmentation. This gate is
specifically for the G2-source path, where raw PCM arrives over HTTP and we
want to drop ambient room noise before paying cloud-ASR cost.

Privacy posture:
  * Model + inference are local PyTorch (CPU). No network.
  * No audio is written to disk.
  * Buffers are bounded by ``max_segment_ms`` and zero-filled in
    ``clear()`` after each segment is read out.

Streaming usage:

    gate = SpeechGate()
    for frame in mic_frames():       # int16, 16 kHz, 512 samples per frame
        for seg in gate.feed(frame):
            forward_to_whisper(seg.audio)
    for seg in gate.flush():
        forward_to_whisper(seg.audio)

Tests inject a fake VAD via ``SpeechGate(vad_fn=...)`` so the segmenter
state machine can be exercised without torch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512                                  # silero requires 512 @ 16 kHz
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE       # 32

VadFn = Callable[[np.ndarray], float]                # int16 frame -> P(speech)


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    audio: np.ndarray | None  # int16 mono @ 16 kHz, or None if keep_audio=False

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class SpeechGate:
    threshold: float = 0.5
    min_speech_ms: int = 256        # confirm speech only after this much voiced audio
    hangover_ms: int = 416          # trailing silence required to close a segment
    max_segment_ms: int = 30_000    # hard cap on a single segment (memory bound)
    keep_audio: bool = True
    vad_fn: VadFn | None = None     # injected for tests; default = silero

    _ms_elapsed: int = field(default=0, init=False)
    _in_speech: bool = field(default=False, init=False)
    _speech_run_ms: int = field(default=0, init=False)
    _silence_run_ms: int = field(default=0, init=False)
    _seg_start_ms: int = field(default=0, init=False)
    _buf: list[np.ndarray] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.vad_fn is None:
            self.vad_fn = _default_silero_vad()

    def feed(self, pcm_int16: np.ndarray) -> list[Segment]:
        """Push one 32 ms / 512-sample int16 frame; return any closed segments."""
        if pcm_int16.dtype != np.int16:
            raise TypeError("expected int16 PCM")
        if pcm_int16.shape != (FRAME_SAMPLES,):
            raise ValueError(f"expected {FRAME_SAMPLES} samples, got {pcm_int16.shape}")

        prob = float(self.vad_fn(pcm_int16))  # type: ignore[misc]
        is_speech = prob >= self.threshold
        out: list[Segment] = []

        if not self._in_speech:
            if is_speech:
                self._speech_run_ms += FRAME_MS
                if self.keep_audio:
                    self._buf.append(pcm_int16.copy())
                if self._speech_run_ms >= self.min_speech_ms:
                    self._in_speech = True
                    self._seg_start_ms = (
                        self._ms_elapsed - self._speech_run_ms + FRAME_MS
                    )
                    self._silence_run_ms = 0
            else:
                self._speech_run_ms = 0
                self._buf.clear()
        else:
            if self.keep_audio:
                self._buf.append(pcm_int16.copy())
            if is_speech:
                self._silence_run_ms = 0
            else:
                self._silence_run_ms += FRAME_MS

            seg_dur = self._ms_elapsed - self._seg_start_ms + FRAME_MS
            if self._silence_run_ms >= self.hangover_ms or seg_dur >= self.max_segment_ms:
                out.append(self._emit(end_ms=self._ms_elapsed + FRAME_MS))
                self._reset_running_state()

        self._ms_elapsed += FRAME_MS
        return out

    def flush(self) -> list[Segment]:
        """Close any open segment. Call once at end-of-stream."""
        if not self._in_speech:
            self._buf.clear()
            return []
        seg = self._emit(end_ms=self._ms_elapsed)
        self._reset_running_state()
        return [seg]

    def clear(self) -> None:
        """Zero-out and drop buffered audio. Defensive for sensitive contexts."""
        for arr in self._buf:
            arr.fill(0)
        self._buf.clear()

    def _emit(self, *, end_ms: int) -> Segment:
        audio: np.ndarray | None = None
        if self.keep_audio and self._buf:
            audio = np.concatenate(self._buf)
        seg = Segment(start_ms=self._seg_start_ms, end_ms=end_ms, audio=audio)
        self.clear()
        return seg

    def _reset_running_state(self) -> None:
        self._in_speech = False
        self._speech_run_ms = 0
        self._silence_run_ms = 0
        self._seg_start_ms = 0


def _default_silero_vad() -> VadFn:
    """Lazy-load Silero VAD. ImportError is deferred to first construction.

    Optional dep — only required when the G2-mic audio path is used. Tests and
    the laptop-mic path don't trigger this loader.
    """
    import torch  # noqa: PLC0415
    from silero_vad import load_silero_vad  # noqa: PLC0415

    model = load_silero_vad()

    def _prob(frame_int16: np.ndarray) -> float:
        f32 = frame_int16.astype(np.float32) / 32768.0
        with torch.no_grad():
            return model(torch.from_numpy(f32), SAMPLE_RATE).item()

    return _prob


def iter_wav_frames(path: str) -> Iterator[np.ndarray]:
    """Yield 512-sample int16 frames from a 16 kHz mono PCM wav."""
    import wave  # noqa: PLC0415
    with wave.open(path, "rb") as w:
        if w.getframerate() != SAMPLE_RATE:
            raise ValueError(f"need {SAMPLE_RATE} Hz wav, got {w.getframerate()}")
        if w.getnchannels() != 1:
            raise ValueError("need mono wav")
        if w.getsampwidth() != 2:
            raise ValueError("need 16-bit PCM wav")
        while True:
            raw = w.readframes(FRAME_SAMPLES)
            if len(raw) < FRAME_SAMPLES * 2:
                return
            yield np.frombuffer(raw, dtype=np.int16)
