"""Glasses-audio pipeline — separate from voice_trigger.py (which uses laptop mic).

Bytes arrive at trigger_server's /audio endpoint as 16kHz s16le mono PCM. We
frame the stream into 512-sample chunks, gate through Silero VAD, and only
send confirmed speech segments to Groq's whisper-large-v3 API. On extracted
text we run the same phrase patterns voice_trigger.py uses and call recall().

Usage from trigger_server:
    from audio_pipeline import GlassesAudioPipeline
    pipeline = GlassesAudioPipeline(gmail_services_getter)
    # in /audio handler:
    pipeline.feed(pcm_bytes)
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np
import requests

from action_runtime import evaluate_and_dispatch
from actions import lookup_contact
from event_extractor import extract as extract_event
from memory_analyzer import analyze as analyze_memory
from obsidian_writer import write_memory as write_memory_note
from proactive_assist import assist as proactive_assist
from output import notify
from recall import recall
from vad import SpeechGate, FRAME_SAMPLES, SAMPLE_RATE

# --- Config --------------------------------------------------------------
FRAME_BYTES = FRAME_SAMPLES * 2          # 1024 bytes per VAD frame (int16 mono)
MIN_SEGMENT_MS = 300                     # skip very short blips (cough, click)
NAME_TTL_SECONDS = 60
# Wearer's voice is closer to the G2 mic → louder. Ambient/other-speaker
# segments tend to be quieter. RMS floor drops them before transcription.
# int16 RMS ≈ 1500 corresponds to roughly -27 dBFS — quiet conversation
# from across the room. Tune by watching [audio] rms= logs.
WEARER_RMS_FLOOR = 800
# Utterance coalescing: hold transcripts for this long after the last
# Groq segment, then run the extractor on the joined text.
UTTERANCE_QUIET_MS = 1500
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"

# --- Reused phrase patterns (mirror voice_trigger.py) --------------------
RE_WHO_IS_THIS = re.compile(r"\bwho(?:'s| is)\s+this\b")
RE_REMIND_ABOUT = re.compile(r"\bremind me about\s+([A-Za-z][A-Za-z .'-]{0,40})")
RE_WHAT_DID_SAY = re.compile(r"\bwhat did\s+([A-Za-z][A-Za-z .'-]{0,40}?)\s+say\b")
RE_CONTEXT_ON = re.compile(r"\bcontext on\s+([A-Za-z][A-Za-z .'-]{0,40})")
RE_PROPER = re.compile(r"\b([A-Z][a-z]{2,})\b")
COMMON_CAPS = {
    "I", "The", "This", "That", "These", "Those", "We", "You", "He", "She",
    "It", "They", "What", "Who", "When", "Where", "Why", "How", "Remind",
    "Tell", "Hey", "Yes", "No", "Okay", "Ok", "So", "And", "But", "Or",
}


def _clean_name(raw: str) -> str:
    name = raw.strip().rstrip(".,!?;:").strip()
    if name and name.islower() and " " not in name:
        name = name.title()
    return name


def _pcm_to_wav_bytes(pcm_int16: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


class GlassesAudioPipeline:
    """VAD-gated, cloud-transcribed pipeline from G2 mic to recall()."""

    def __init__(self, gmail_services_getter: Callable[[], dict | None]):
        self._get_gmail = gmail_services_getter
        self._byte_buf = bytearray()
        self._lock = threading.Lock()
        self._gate = SpeechGate(keep_audio=True)
        self._exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="g2-asr")
        self._name_history: deque[tuple[float, str]] = deque(maxlen=64)
        self._last_recalled: dict[str, float] = {}
        self._groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not self._groq_key:
            print("[glasses-audio] GROQ_API_KEY not set — transcription disabled",
                  file=sys.stderr)
        # Utterance buffer: accumulate consecutive Groq segments into one
        # "full thought" before running the extractor. Flushed after
        # UTTERANCE_QUIET_MS of silence. Avoids fragmented HUD decisions.
        self._utt_lock = threading.Lock()
        self._utt_buf: list[str] = []
        self._utt_timer: threading.Timer | None = None

    def feed(self, pcm: bytes) -> None:
        """Push raw PCM bytes from /audio. Frames into VAD, emits segments async."""
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

    def _process_segment(self, audio: np.ndarray) -> None:
        # Amplitude gate: drop quiet segments (ambient / distant speakers).
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        if rms < WEARER_RMS_FLOOR:
            print(f"[glasses-audio] dropped quiet segment rms={rms:.0f}",
                  file=sys.stderr)
            return
        try:
            text = self._transcribe(audio)
        except Exception as e:
            print(f"[glasses-audio] transcribe failed: {e!r}", file=sys.stderr)
            notify(f"ASR Error: {str(e)[:50]}")
            return
        if not text:
            return
        print(f"[glasses-audio] heard: {text!r}", file=sys.stderr)
        echo = text[:80] + ("..." if len(text) > 80 else "")
        notify(f"> {echo}")
        self._name_history.append((time.time(), text))

        # Voice intent on a single segment: confirm / reject act immediately,
        # don't wait for the utterance window to close.
        lower = text.strip().lower().rstrip(".?!")
        if lower in {"confirm", "yes confirm", "confirm it", "go ahead", "do it", "send it", "yes send it"}:
            self._cancel_utt_timer()
            self._utt_buf.clear()
            self._exec.submit(self._voice_confirm_latest)
            return
        if lower in {"reject", "cancel", "no", "don't", "stop", "nevermind", "never mind"}:
            self._cancel_utt_timer()
            self._utt_buf.clear()
            self._exec.submit(self._voice_reject_latest)
            return

        # Otherwise: accumulate this segment into the current utterance and
        # arm a flush timer. The extractor runs on the JOINED text after
        # UTTERANCE_QUIET_MS of silence.
        self._append_to_utterance(text)

    def _cancel_utt_timer(self) -> None:
        with self._utt_lock:
            if self._utt_timer is not None:
                self._utt_timer.cancel()
                self._utt_timer = None

    def _append_to_utterance(self, text: str) -> None:
        with self._utt_lock:
            self._utt_buf.append(text)
            if self._utt_timer is not None:
                self._utt_timer.cancel()
            self._utt_timer = threading.Timer(
                UTTERANCE_QUIET_MS / 1000.0, self._flush_utterance
            )
            self._utt_timer.daemon = True
            self._utt_timer.start()

    def _flush_utterance(self) -> None:
        with self._utt_lock:
            if not self._utt_buf:
                self._utt_timer = None
                return
            full = " ".join(self._utt_buf).strip()
            self._utt_buf.clear()
            self._utt_timer = None
        if not full:
            return
        print(f"[glasses-audio] utterance: {full!r}", file=sys.stderr)
        self._handle_text(full)
        self._exec.submit(self._handle_event_then_assist, full)

    def _handle_event_then_assist(self, text: str) -> None:
        # Lean path: extractor only. No memory analyzer Groq call per transcript.
        # Vault recall pulls from seeded notes (34 already there).
        try:
            event = extract_event(text)
        except Exception:
            event = None
        if event and event.get("action") not in (None, "none", ""):
            self._handle_event(text, prefetched=event)
            return
        # No action and no pending proposal → soft proactive recall.
        try:
            from action_runtime import list_pending_proposals
            if list_pending_proposals():
                return
        except Exception:
            pass
        self._exec.submit(proactive_assist, text)

    def _handle_memory_silent(self, text: str) -> None:
        try:
            memory = analyze_memory(text)
        except Exception:
            return
        if not memory:
            return
        try:
            write_memory_note(memory, text, source="g2-audio")
        except Exception:
            pass

    def _voice_confirm_latest(self) -> None:
        try:
            r = requests.post("http://localhost:9876/proposals/confirm-latest", timeout=5)
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if j.get("ok"):
                notify("✅ Confirmed", speak=False)
            else:
                notify(f"⚠️ Nothing to confirm ({j.get('reason','')})", speak=False)
        except Exception as e:
            print(f"[glasses-audio] voice confirm failed: {e!r}", file=sys.stderr)

    def _voice_reject_latest(self) -> None:
        try:
            from action_runtime import list_pending_proposals, reject_proposal
            pending = list_pending_proposals()
            if not pending:
                notify("⚠️ Nothing to reject", speak=False)
                return
            pid = pending[0].get("id") or pending[0].get("proposal_id")
            reject_proposal(pid, reason="voice_rejected")
            remaining = list_pending_proposals()
            if remaining:
                title = remaining[0].get("title") or remaining[0].get("preview") or "next action"
                notify(f"🚫 Rejected — 🟡 NEXT: {title}\nSay 'confirm' or 'reject' ({len(remaining)} queued)", speak=False)
            else:
                notify("🚫 Rejected", speak=False)
        except Exception as e:
            print(f"[glasses-audio] voice reject failed: {e!r}", file=sys.stderr)

    def _handle_memory(self, text: str) -> None:
        try:
            memory = analyze_memory(text)
        except Exception as e:
            print(f"[glasses-audio] memory analyze failed: {e!r}", file=sys.stderr)
            return
        if not memory:
            return
        path = write_memory_note(memory, text, source="g2-audio")
        if path:
            print(f"[glasses-audio] memory -> {path}", file=sys.stderr)

    def _handle_event(self, text: str, prefetched: dict | None = None) -> None:
        # No pre-filter: Groq 8b is fast + cheap, and the model itself is a
        # better "is this actionable" classifier than any keyword regex.
        if prefetched is not None:
            event = prefetched
        else:
            try:
                event = extract_event(text)
            except Exception as e:
                print(f"[glasses-audio] extractor failed: {e!r}", file=sys.stderr)
                notify(f"⚠️ Extractor Error: {str(e)[:50]}")
                return
        if not event:
            return
        action = event.get("action")
        payload = dict(event.get("payload") or {})
        # Resolve handle → phone for message actions via contacts.json.
        if action in {"send_imessage", "schedule_imessage"} and "handle" in payload:
            contact = lookup_contact(payload["handle"])
            if contact and contact.get("phone"):
                payload["handle"] = contact["phone"]
        confidence = event.get("confidence")
        print(
            f"[glasses-audio] event: {action} payload={payload} conf={confidence}",
            file=sys.stderr,
        )
        evaluate_and_dispatch(
            action,
            payload,
            transcript=text,
            confidence=confidence,
        )

    def _transcribe(self, audio: np.ndarray) -> str:
        if not self._groq_key:
            return ""
        wav_bytes = _pcm_to_wav_bytes(audio)
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                r = requests.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {self._groq_key}"},
                    files={"file": ("seg.wav", wav_bytes, "audio/wav")},
                    data={
                        "model": GROQ_MODEL,
                        "response_format": "text",
                        "language": "en",
                        "temperature": "0",
                    },
                    timeout=30,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                print(f"[glasses-audio] groq attempt {attempt+1} failed: {e!r}",
                      file=sys.stderr)
                continue
            if r.status_code == 200:
                return r.text.strip()
            if r.status_code in (429, 500, 502, 503, 504):
                print(f"[glasses-audio] groq {r.status_code} (retrying): {r.text[:200]}",
                      file=sys.stderr)
                continue
            print(f"[glasses-audio] groq {r.status_code}: {r.text[:200]}",
                  file=sys.stderr)
            return ""
        if last_err is not None:
            raise last_err
        return ""

    def _handle_text(self, text: str) -> None:
        text_lc = text.lower()

        for pat in (RE_REMIND_ABOUT, RE_WHAT_DID_SAY, RE_CONTEXT_ON):
            m = pat.search(text)
            if m:
                self._fire(_clean_name(m.group(1)))
                return

        if RE_WHO_IS_THIS.search(text_lc):
            cutoff = time.time() - NAME_TTL_SECONDS
            for ts, chunk in reversed(self._name_history):
                if ts < cutoff:
                    break
                cands = [m.group(1) for m in RE_PROPER.finditer(chunk)
                         if m.group(1) not in COMMON_CAPS]
                if cands:
                    self._fire(_clean_name(cands[-1]))
                    return

    def _fire(self, person: str) -> None:
        if not person:
            return
        now = time.time()
        last = self._last_recalled.get(person.lower(), 0.0)
        if now - last < 8.0:
            return
        self._last_recalled[person.lower()] = now

        gmail_svcs = self._get_gmail()
        if not gmail_svcs:
            notify(f"(no gmail auth) {person}")
            return
        try:
            text = recall(person, gmail_services=gmail_svcs)
        except Exception as e:
            print(f"[glasses-audio] recall failed for {person!r}: {e!r}",
                  file=sys.stderr)
            return
        if text:
            notify(text)
        else:
            notify(f"No recent context for {person}.")
