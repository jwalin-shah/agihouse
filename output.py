"""Output adapter — print + macOS `say` + best-effort push to the G2 renderer.

Anything notify()'d also fans out to /push on the local trigger_server, which
the Even Hub renderer (g2-renderer/) subscribes to via SSE. If trigger_server
isn't running, the POST quietly times out — terminal+TTS keep working.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

_BRIDGE_URL = os.environ.get("AGIHOUSE_BRIDGE_URL", "http://127.0.0.1:9876").rstrip("/")
_BRIDGE_TIMEOUT = 1.0


def _push_to_bridge(text: str) -> None:
    try:
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"{_BRIDGE_URL}/push",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT)  # noqa: S310 — local LAN
    except (urllib.error.URLError, OSError):
        pass


def notify(text: str, *, speak: bool = True) -> None:
    print(f"\n>>> {text}\n", flush=True)
    if speak and shutil.which("say"):
        subprocess.Popen(["say", text])  # noqa: S603 — local TTS, no shell
    threading.Thread(target=_push_to_bridge, args=(text,), daemon=True).start()
