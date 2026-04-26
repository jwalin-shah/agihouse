"""
Ambient notes — writes to Obsidian vault as markdown files.
Daily notes go to {vault}/daily/YYYY-MM-DD.md, ambient captures to {vault}/ambient/.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_PATH = Path.home() / "vault"
DAILY_DIR = VAULT_PATH / "daily"
AMBIENT_DIR = VAULT_PATH / "ambient"


def _ensure_dirs():
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    AMBIENT_DIR.mkdir(parents=True, exist_ok=True)


def _today_file() -> Path:
    return DAILY_DIR / f"{datetime.date.today()}.md"


def append_to_daily(content: str):
    """Append a note entry to today's daily note."""
    _ensure_dirs()
    path = _today_file()
    ts = datetime.datetime.now().strftime("%H:%M")

    if not path.exists():
        path.write_text(f"# {datetime.date.today()}\n\n")

    with open(path, "a") as f:
        f.write(f"## {ts}\n{content}\n\n")


def save_note(
    raw_transcript: str,
    summary: str | None,
    topics: str = "",
):
    """Save an ambient capture — appends to daily note with structured content."""
    parts = []

    if summary:
        parts.append(summary)

    # Action items as checkboxes
    if "→" in (summary or ""):
        # extract_summary formats action items as "→ item1; item2"
        for segment in (summary or "").split("→"):
            segment = segment.strip()
            if segment and segment != summary:
                for item in segment.split(";"):
                    item = item.strip()
                    if item:
                        parts.append(f"- [ ] {item}")

    # Topics as tags
    if topics:
        tags = " ".join(f"#{t.strip().replace(' ', '-')}" for t in topics.split(",") if t.strip())
        parts.append(tags)

    # Raw transcript in collapsed details
    parts.append(f"> [!note]- Transcript\n> {raw_transcript}")

    append_to_daily("\n".join(parts))

    # Log the capture so user sees activity
    preview = (summary or raw_transcript)[:60]
    logger.info(f"[ambient] Captured: {preview}...")
    print(f"[ambient] Captured: {preview}...")


def list_daily_notes(limit: int = 30) -> list[dict]:
    """List recent daily notes. Returns [{date, path, size}]."""
    _ensure_dirs()
    files = sorted(DAILY_DIR.glob("*.md"), reverse=True)[:limit]
    return [
        {
            "date": f.stem,
            "path": str(f),
            "size": f.stat().st_size,
        }
        for f in files
    ]


def read_daily_note(date: str) -> str | None:
    """Read a daily note by date string (YYYY-MM-DD)."""
    path = DAILY_DIR / f"{date}.md"
    if path.exists():
        return path.read_text()
    return None


def get_recent_captures(limit: int = 10) -> list[dict]:
    """Parse today's daily note and return recent captures with timestamps."""
    path = _today_file()
    if not path.exists():
        return []

    content = path.read_text()
    captures = []

    # Parse sections starting with "## HH:MM"
    import re

    pattern = r"^## (\d{2}:\d{2})\n((?:(?!^## ).*\n?)*)"
    matches = re.finditer(pattern, content, re.MULTILINE)

    for match in matches:
        timestamp = match.group(1)
        body = match.group(2).strip()

        # Extract first line (summary) and strip markdown formatting
        lines = body.split("\n")
        summary = lines[0] if lines else ""
        summary = re.sub(r"[#*`\[\]!-]", "", summary)[:80].strip()

        if summary:
            captures.append({"timestamp": timestamp, "summary": summary})

    return list(reversed(captures))[-limit:]
