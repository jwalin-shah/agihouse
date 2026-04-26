#!/usr/bin/env python3
"""
Standalone ambient listening daemon.
Runs independently from the inbox server.
Continuously listens, captures audio, extracts notes to vault.

Usage:
    uv run python ambient_daemon.py

Auto-start on macOS:
    launchctl load ~/Library/LaunchAgents/com.jwalin.ambient.plist
"""

from __future__ import annotations

import contextlib
import signal
import sys
import time

from ambient_notes import save_note
from services import AmbientService, whisper_stream_available

# Global daemon reference for signal handling
_daemon_service = None
_should_exit = False


def on_note(raw_transcript: str, summary: str | None):
    """Callback to save notes with extracted topics."""
    try:
        topics = ""
        if summary:
            from services import extract

            result = extract(raw_transcript)
            topics = ", ".join(result.topics)
    except Exception:
        topics = ""
    save_note(raw_transcript, summary, topics)


def handle_signal(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global _should_exit, _daemon_service
    _should_exit = True
    if _daemon_service:
        with contextlib.suppress(Exception):
            _daemon_service.stop()
    sys.exit(0)


def main():
    """Run the ambient listening daemon."""
    global _daemon_service, _should_exit

    if not whisper_stream_available():
        print("[ambient] ERROR: whisper-stream not available")
        sys.exit(1)

    try:
        # Set up signal handlers early
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        print("[ambient] Daemon starting...")
        sys.stdout.flush()

        _daemon_service = AmbientService(on_note=on_note)
        _daemon_service.start()

        print("[ambient] Daemon running. Listening for audio...")
        sys.stdout.flush()

        # Keep process alive indefinitely
        while not _should_exit:
            time.sleep(5)

    except Exception as e:
        print(f"[ambient] Fatal error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        if _daemon_service:
            with contextlib.suppress(Exception):
                _daemon_service.stop()


if __name__ == "__main__":
    main()
