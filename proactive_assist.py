"""Proactive assist agent: when the wearer says something, scan the
Obsidian vault for a relevant past memory and surface it on the HUD
*unprompted*. This is the "the glasses remembered for me" demo moment.

Pure additive: another agent feeding the single watcher (output.notify).
Best-effort, never blocks; failures are logged and swallowed.

Strategy (kept simple for live demo reliability):
1. Tokenize the new transcript into salient terms (names + topical words).
2. Walk vault/Daily/**/*.md, score notes by token overlap on people/topics.
3. If best score >= threshold, push "💡 Earlier you said: <summary>".
4. Per-key cooldown so we don't spam the HUD with the same memory.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from output import notify

_VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "").strip() or (Path(__file__).parent / "vault"))
_COOLDOWN_SECONDS = 60.0
_MIN_OVERLAP = 2  # require this many shared salient tokens to surface a memory
_MAX_NOTES_SCANNED = 100
_recent: dict[str, float] = {}

_STOPWORDS = {
    "the","a","an","and","or","but","of","for","to","in","on","at","by","with","is","are",
    "was","were","be","been","being","i","you","we","they","he","she","it","this","that",
    "these","those","my","your","our","their","me","him","her","us","them","do","did","does",
    "have","has","had","what","where","when","how","why","who","ok","yeah","yes","no",
    "going","said","say","says","just","like","really","im","ive","dont","its","not",
}


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z'\-]+", text.lower())
    return {t.strip("'-") for t in raw if len(t) > 2 and t not in _STOPWORDS}


def _read_vault_notes() -> list[tuple[Path, str, str]]:
    """Return (path, summary, body) for recent vault notes (newest first)."""
    if not _VAULT.exists():
        return []
    md_files = sorted(_VAULT.glob("Daily/**/*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    notes: list[tuple[Path, str, str]] = []
    for p in md_files[:_MAX_NOTES_SCANNED]:
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        # Pull H1 summary line for HUD output.
        m = re.search(r"^# (.+)$", txt, re.MULTILINE)
        summary = m.group(1).strip() if m else p.stem
        notes.append((p, summary, txt))
    return notes


def assist(transcript: str) -> None:
    """Scan vault for a memory matching this transcript; notify if found."""
    try:
        if not transcript or len(transcript.strip()) < 6:
            return
        query_tokens = _tokens(transcript)
        if len(query_tokens) < 2:
            return

        notes = _read_vault_notes()
        if not notes:
            return

        best_path: Path | None = None
        best_summary = ""
        best_score = 0
        for path, summary, body in notes:
            note_tokens = _tokens(body)
            overlap = query_tokens & note_tokens
            score = len(overlap)
            if score > best_score:
                best_score = score
                best_summary = summary
                best_path = path

        if best_score < _MIN_OVERLAP or not best_path:
            return

        key = str(best_path)
        now = time.time()
        if now - _recent.get(key, 0.0) < _COOLDOWN_SECONDS:
            return
        _recent[key] = now

        msg = f"💡 Earlier: {best_summary}"[:180]
        notify(msg, speak=False)
    except Exception as e:
        print(f"[proactive-assist] failed: {e!r}", file=sys.stderr)
