"""Voice trigger — listen for hero-demo phrases and fire recall().

User wears G2 glasses, says "who is this" or "remind me about Daniel" out loud,
HUD shows a one-line digest from inbox memory.

Run:
    cd ~/projects/agihouse
    uv run --with anthropic python voice_trigger.py

Future: gate audio with vad.SpeechGate before whisper. AmbientService today
streams every frame through ASR; routing through SpeechGate first (in
inbox/services.py) means only voiced segments become text, cutting CPU and
narrowing the surface of overheard speech that ever leaves the audio thread.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import sys
import time
from collections import deque
from pathlib import Path

# --- Make sibling inbox project importable -------------------------------
INBOX_DIR = Path.home() / "projects" / "inbox"
if str(INBOX_DIR) not in sys.path:
    sys.path.insert(0, str(INBOX_DIR))
os.chdir(INBOX_DIR)  # services.py uses BASE_DIR-relative paths

from services import AmbientService, google_auth_all, whisper_stream_available  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from output import notify  # noqa: E402
from recall import recall  # noqa: E402


# --- Phrase patterns ------------------------------------------------------
# All matched against lowercased transcript text.
RE_WHO_IS_THIS = re.compile(r"\bwho(?:'s| is)\s+this\b")
RE_REMIND_ABOUT = re.compile(r"\bremind me about\s+([A-Za-z][A-Za-z .'-]{0,40})")
RE_WHAT_DID_SAY = re.compile(r"\bwhat did\s+([A-Za-z][A-Za-z .'-]{0,40}?)\s+say\b")
RE_CONTEXT_ON = re.compile(r"\bcontext on\s+([A-Za-z][A-Za-z .'-]{0,40})")

# Capitalized-token heuristic for "who is this" name extraction. Excludes
# sentence-initial common words to reduce false positives.
RE_PROPER = re.compile(r"\b([A-Z][a-z]{2,})\b")
COMMON_CAPS = {
    "I", "The", "This", "That", "These", "Those", "We", "You", "He", "She",
    "It", "They", "What", "Who", "When", "Where", "Why", "How", "Remind",
    "Tell", "Hey", "Yes", "No", "Okay", "Ok", "So", "And", "But", "Or",
    "If", "Then", "Because", "Maybe", "Today", "Tomorrow", "Yesterday",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
}

NAME_TTL_SECONDS = 60  # "last mentioned name" window for "who is this"


# --- Globals for signal handling -----------------------------------------
_daemon_service: AmbientService | None = None
_should_exit = False


def _clean_name(raw: str) -> str:
    """Trim trailing punctuation/spaces and title-case single-token names."""
    name = raw.strip().rstrip(".,!?;:").strip()
    # If it's just one lowercase token (e.g. user mumbled), title-case it.
    if name and name.islower() and " " not in name:
        name = name.title()
    return name


def _extract_recent_name(history: deque[tuple[float, str]]) -> str | None:
    """Scan last NAME_TTL_SECONDS of transcript for the most recent proper noun."""
    cutoff = time.time() - NAME_TTL_SECONDS
    # Walk newest -> oldest, return last proper noun found.
    for ts, chunk in reversed(history):
        if ts < cutoff:
            break
        # Find all proper-noun candidates in this chunk; return the last one.
        candidates = [m.group(1) for m in RE_PROPER.finditer(chunk)
                      if m.group(1) not in COMMON_CAPS]
        if candidates:
            return candidates[-1]
    return None


def _fire(person: str, *, gmail_svcs: dict, cal_svcs: dict | None) -> None:
    person = _clean_name(person)
    if not person:
        print("[voice] empty name after cleaning, skipping", flush=True)
        return
    print(f"[voice] -> recall({person!r})", flush=True)
    try:
        result = recall(person, gmail_services=gmail_svcs, cal_services=cal_svcs)
    except Exception as e:
        print(f"[voice] recall failed: {e!r}", file=sys.stderr, flush=True)
        return
    if result:
        print(f"[voice] hit: {result}", flush=True)
        notify(result)
    else:
        print(f"[voice] miss: no context for {person}", flush=True)
        notify(f"No recent context for {person}.", speak=False)


def make_on_note(gmail_svcs: dict, cal_svcs: dict | None):
    """Build the AmbientService callback. Closes over auth + transcript history."""
    history: deque[tuple[float, str]] = deque(maxlen=20)

    def on_note(raw_transcript: str, summary: str | None) -> None:
        now = time.time()
        history.append((now, raw_transcript))
        text_lc = raw_transcript.lower()
        print(f"[voice] chunk: {raw_transcript[:120]!r}", flush=True)

        fired = False

        # Explicit-name triggers first (most specific).
        for pattern, label in (
            (RE_REMIND_ABOUT, "remind me about"),
            (RE_WHAT_DID_SAY, "what did X say"),
            (RE_CONTEXT_ON, "context on"),
        ):
            for m in pattern.finditer(text_lc):
                # Recover original casing from raw_transcript at the same span.
                start, end = m.span(1)
                name = raw_transcript[start:end]
                print(f"[voice] phrase={label!r} name={name!r}", flush=True)
                _fire(name, gmail_svcs=gmail_svcs, cal_svcs=cal_svcs)
                fired = True

        # "Who is this" — ambient lookup of last-mentioned name.
        if RE_WHO_IS_THIS.search(text_lc):
            name = _extract_recent_name(history)
            if name:
                print(f"[voice] phrase='who is this' name={name!r}", flush=True)
                _fire(name, gmail_svcs=gmail_svcs, cal_svcs=cal_svcs)
                fired = True
            else:
                print("[voice] phrase='who is this' but no recent name found", flush=True)
                notify("Couldn't catch a name.", speak=False)

        if not fired:
            # No trigger this chunk — just keep listening.
            pass

    return on_note


def handle_signal(signum, frame):
    global _should_exit, _daemon_service
    _should_exit = True
    if _daemon_service:
        with contextlib.suppress(Exception):
            _daemon_service.stop()
    sys.exit(0)


def main() -> None:
    global _daemon_service, _should_exit

    if not whisper_stream_available():
        print("[voice] ERROR: whisper-stream binary or model not available", file=sys.stderr)
        sys.exit(1)

    gmail_svcs, cal_svcs, *_ = google_auth_all()
    if not gmail_svcs:
        print("[voice] ERROR: no Gmail accounts authed — run inbox.py and re-auth", file=sys.stderr)
        sys.exit(1)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("[voice] starting ambient listener...", flush=True)
    _daemon_service = AmbientService(on_note=make_on_note(gmail_svcs, cal_svcs))
    _daemon_service.start()
    print("[voice] listening. Say 'who is this' or 'remind me about <Name>'.", flush=True)

    try:
        while not _should_exit:
            time.sleep(5)
    finally:
        if _daemon_service:
            with contextlib.suppress(Exception):
                _daemon_service.stop()


if __name__ == "__main__":
    main()
