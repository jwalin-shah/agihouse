"""Output adapter — currently print + macOS `say`. Swap with G2 SDK tomorrow.

The G2 swap should land in `notify()` only — keep the rest of the codebase
unaware of the surface (terminal vs. glasses HUD vs. TTS).
"""

from __future__ import annotations

import shutil
import subprocess


def notify(text: str, *, speak: bool = True) -> None:
    print(f"\n>>> {text}\n", flush=True)
    if speak and shutil.which("say"):
        subprocess.Popen(["say", text])  # noqa: S603 — local TTS, no shell


# --- G2 stub (fill in tomorrow morning) -----------------------------------
# def notify_g2(text: str) -> None:
#     from even_g2_sdk import Glasses  # whatever the SDK is called
#     Glasses.connected().show_text(text, duration_ms=4000)
