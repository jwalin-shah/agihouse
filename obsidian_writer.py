"""Write each captured memory to a local Obsidian-style markdown vault.

Vault layout:
    <vault>/Daily/YYYY-MM-DD/YYYY-MM-DD-HHMM-<slug>.md

Each note has YAML frontmatter (id, time, category, importance, people,
topics, tasks, promises, decisions) and a body with the summary, raw
transcript, and HUD line. Obsidian indexes wiki-links of the form [[Name]]
in people/topics so the vault graph populates automatically.

Side-effect-free on import. Failures are logged and swallowed so a broken
vault path never wedges the audio pipeline.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_VAULT = Path(__file__).parent / "vault"


def _vault_path() -> Path:
    raw = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_VAULT


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:max_len] or "memory").rstrip("-")


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    cleaned = [v.replace('"', "'") for v in values]
    return "[" + ", ".join(f'"{v}"' for v in cleaned) + "]"


def _wiki_links(values: list[str]) -> str:
    return ", ".join(f"[[{v}]]" for v in values) if values else "_none_"


def write_memory(memory: dict[str, Any], transcript: str, *, source: str = "audio") -> Path | None:
    """Write a markdown note for a memory analysis. Returns path or None."""
    try:
        vault = _vault_path()
        now = datetime.now()
        day_dir = vault / "Daily" / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        slug = _slug(memory.get("summary") or transcript)
        mem_id = uuid.uuid4().hex[:8]
        fname = f"{now.strftime('%Y-%m-%d-%H%M')}-{slug}-{mem_id}.md"
        path = day_dir / fname

        people = memory.get("people") or []
        topics = memory.get("topics") or []
        tasks = memory.get("tasks") or []
        promises = memory.get("promises") or []
        decisions = memory.get("decisions") or []

        frontmatter = "\n".join([
            "---",
            f"id: {mem_id}",
            f"time: {now.isoformat(timespec='seconds')}",
            f"date: {now.strftime('%Y-%m-%d')}",
            f"hour: {now.strftime('%H')}",
            f"minute: {now.strftime('%M')}",
            f"category: {memory.get('category', 'ambient')}",
            f"importance: {memory.get('importance', 0):.2f}",
            f"source: {source}",
            f"people: {_yaml_list(people)}",
            f"topics: {_yaml_list(topics)}",
            f"tasks: {_yaml_list(tasks)}",
            f"promises: {_yaml_list(promises)}",
            f"decisions: {_yaml_list(decisions)}",
            "---",
        ])

        body = "\n".join([
            f"# {memory.get('summary', 'Memory')}",
            "",
            "## HUD",
            "```",
            str(memory.get("hud", "")),
            "```",
            "",
            "## Transcript",
            f"> {transcript.strip()}",
            "",
            "## People",
            _wiki_links(people),
            "",
            "## Topics",
            _wiki_links(topics),
            "",
            "## Tasks",
            "- " + "\n- ".join(tasks) if tasks else "_none_",
            "",
            "## Promises",
            "- " + "\n- ".join(promises) if promises else "_none_",
            "",
            "## Decisions",
            "- " + "\n- ".join(decisions) if decisions else "_none_",
            "",
        ])

        path.write_text(frontmatter + "\n\n" + body, encoding="utf-8")
        return path
    except Exception as e:
        print(f"[obsidian-writer] failed: {e!r}", file=sys.stderr)
        return None
