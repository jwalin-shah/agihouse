"""Command palette registry, fuzzy filter, and NLP intent routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# ── Command data model ───────────────────────────────────────────────────────

CommandDict = dict[str, Any]


def make_command(
    id: str,
    name: str,
    description: str,
    category: str,
    action: Callable[[], None],
) -> CommandDict:
    return {
        "id": id,
        "name": name,
        "description": description,
        "category": category,
        "action": action,
    }


# ── Fuzzy filter ─────────────────────────────────────────────────────────────


def fuzzy_score(query: str, text: str) -> int:
    """Score query against text. Higher = better match. 0 = no match.

    Scoring tiers:
      3 — exact match (case-insensitive)
      2 — prefix match
      1 — substring match
      0 — no match
    """
    q = query.lower()
    t = text.lower()
    if not q:
        return 1  # empty query matches everything
    if q == t:
        return 3
    if t.startswith(q):
        return 2
    if q in t:
        return 1
    return 0


def filter_commands(query: str, commands: list[CommandDict]) -> list[CommandDict]:
    """Return commands matching query, sorted by score descending."""
    if not query.strip():
        return list(commands)

    scored: list[tuple[int, CommandDict]] = []
    for cmd in commands:
        # Score against name and description; name has higher weight
        name_score = fuzzy_score(query, cmd["name"]) * 2
        desc_score = fuzzy_score(query, cmd["description"])
        cat_score = fuzzy_score(query, cmd["category"])
        total = max(name_score, desc_score, cat_score)
        if total > 0:
            scored.append((total, cmd))

    scored.sort(key=lambda x: -x[0])
    return [cmd for _, cmd in scored]


# ── NLP intent routing ───────────────────────────────────────────────────────

NLP_PROMPT = """\
You are a command classifier for a terminal inbox app.
Given a natural-language query, identify which command best matches.

Available commands (id: name):
{command_list}

Query: {query}

Respond with JSON. If confident (>=0.6), output:
{{"command_id": "<id>", "confidence": <float>, "args": {{}}}}
If no good match:
{{"command_id": null, "reason": "<brief reason>"}}"""


def _build_command_list(commands: list[CommandDict]) -> str:
    return "\n".join(f"  {c['id']}: {c['name']}" for c in commands)


def nlp_classify(
    query: str,
    commands: list[CommandDict],
) -> dict[str, Any] | None:
    """Use the LLM to classify a natural-language query into a command.

    Returns a dict with command_id + confidence, or None if LLM is unavailable.
    """
    try:
        import services  # imported lazily to avoid hard dep in tests

        if not services.llm_is_loaded():
            return None

        try:
            from pydantic import BaseModel as _Base

            class _NlpResult(_Base):
                command_id: str | None = None
                confidence: float = 0.0
                args: dict = {}  # type: ignore[assignment]
                reason: str = ""

        except ImportError:
            return None

        prompt = NLP_PROMPT.format(
            command_list=_build_command_list(commands),
            query=query,
        )
        result = services.generate_json(prompt, _NlpResult)
        return {
            "command_id": getattr(result, "command_id", None),
            "confidence": getattr(result, "confidence", 0.0),
            "args": getattr(result, "args", {}),
            "reason": getattr(result, "reason", ""),
        }
    except Exception:
        return None


def resolve_nlp(
    query: str,
    commands: list[CommandDict],
    confidence_threshold: float = 0.6,
) -> tuple[CommandDict | None, str]:
    """Try NLP classification; return (matched_command_or_None, status_message).

    status_message is used for UI feedback.
    """
    result = nlp_classify(query, commands)
    if result is None:
        return None, "LLM unavailable — try exact command name"

    cmd_id = result.get("command_id")
    confidence = result.get("confidence", 0.0)

    if cmd_id is None:
        reason = result.get("reason", "no match")
        return None, f"No command matches — {reason}"

    if confidence < confidence_threshold:
        # Find suggestions via fuzzy
        suggestions = [c["name"] for c in filter_commands(query, commands)[:3]]
        hint = ", ".join(suggestions) if suggestions else "try a different query"
        return None, f"Low confidence — try: {hint}"

    matched = next((c for c in commands if c["id"] == cmd_id), None)
    if matched is None:
        return None, f"Unknown command id: {cmd_id}"

    return matched, ""


# ── Command registry builder ─────────────────────────────────────────────────


def build_commands(app: Any) -> list[CommandDict]:
    """Build the full command list by binding to the app's action_ methods."""

    def act(method_name: str) -> Callable[[], None]:
        return lambda: getattr(app, method_name)()

    commands: list[CommandDict] = [
        # Navigate
        make_command(
            "switch_all",
            "Switch to All",
            "Show all conversations",
            "Navigate",
            act("action_filter_all"),
        ),
        make_command(
            "switch_imessage",
            "Switch to iMessage",
            "Show iMessage conversations",
            "Navigate",
            act("action_filter_imsg"),
        ),
        make_command(
            "switch_gmail",
            "Switch to Gmail",
            "Show Gmail conversations",
            "Navigate",
            act("action_filter_gmail"),
        ),
        make_command(
            "switch_calendar",
            "Switch to Calendar",
            "Show calendar events",
            "Navigate",
            act("action_filter_cal"),
        ),
        make_command(
            "switch_notes",
            "Switch to Notes",
            "Show Apple Notes",
            "Navigate",
            act("action_filter_notes"),
        ),
        make_command(
            "switch_reminders",
            "Switch to Reminders",
            "Show Apple Reminders",
            "Navigate",
            act("action_filter_rem"),
        ),
        make_command(
            "switch_github",
            "Switch to GitHub",
            "Show GitHub notifications",
            "Navigate",
            act("action_filter_gh"),
        ),
        make_command(
            "switch_drive",
            "Switch to Drive",
            "Show Google Drive files",
            "Navigate",
            act("action_filter_drv"),
        ),
        # Action
        make_command(
            "refresh", "Refresh", "Reload all data from server", "Action", act("action_refresh")
        ),
        make_command("quit", "Quit", "Exit the application", "Action", act("action_quit")),
        make_command(
            "toggle_ambient",
            "Toggle Ambient Listening",
            "Start or stop ambient audio capture",
            "Action",
            act("action_toggle_ambient"),
        ),
        make_command(
            "mark_all_gh_read",
            "Mark All GitHub Notifications Read",
            "Mark all GitHub notifications as read",
            "Action",
            act("action_mark_all_notifications_read"),
        ),
        make_command(
            "ask_inbox_assistant",
            "Ask Inbox Assistant",
            "Run a readonly local assistant against the current Inbox context",
            "AI",
            act("action_ask_assistant"),
        ),
        # Create
        make_command(
            "new_event",
            "New Calendar Event",
            "Create a new calendar event",
            "Create",
            act("action_new_event"),
        ),
        make_command(
            "delete_event",
            "Delete Calendar Event",
            "Delete the selected calendar event",
            "Create",
            act("action_delete_event"),
        ),
        make_command(
            "jump_to_date",
            "Jump to Date",
            "Navigate the calendar to a specific date",
            "Create",
            act("action_jump_to_date"),
        ),
        make_command(
            "new_reminder",
            "New Reminder",
            "Switch to Reminders tab to create a reminder",
            "Create",
            act("action_filter_rem"),
        ),
        make_command(
            "filter_reminder_list",
            "Filter Reminder List",
            "Filter by reminder list",
            "Create",
            act("action_filter_reminder_list"),
        ),
        make_command(
            "gmail_compose",
            "New Gmail Message",
            "Compose a new Gmail message",
            "Create",
            act("action_gmail_compose"),
        ),
        # Settings
        make_command(
            "add_account",
            "Add Google Account",
            "Add a new Google account via OAuth",
            "Settings",
            act("action_add_account"),
        ),
        make_command(
            "reauth_account",
            "Re-auth Account",
            "Re-authenticate the current Google account",
            "Settings",
            act("action_reauth_account"),
        ),
    ]

    return commands
