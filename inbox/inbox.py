"""
Unified inbox TUI — iMessage + Gmail + Calendar + Notes
Thin client that connects to inbox_server.py via HTTP.
Auto-starts the server on launch.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta

import httpx
from rich.align import Align
from rich.panel import Panel
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    Tab,
    Tabs,
)

from command_palette import CommandDict, build_commands, filter_commands, resolve_nlp
from inbox_client import InboxClient

DEFAULT_POLL_INTERVAL = 10.0


def _poll_interval_from_env() -> float:
    raw_value = os.environ.get("INBOX_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)).strip()
    try:
        interval = float(raw_value)
    except ValueError:
        return DEFAULT_POLL_INTERVAL
    return interval if interval > 0 else DEFAULT_POLL_INTERVAL


def _format_request_error(action: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return f"{action} failed (HTTP {status_code})"
    if isinstance(exc, httpx.RequestError):
        return "Server unreachable — press Ctrl+R to retry"
    return f"{action} failed: {exc}"


# ── UI Widgets ───────────────────────────────────────────────────────────────


_PRIORITY_ICONS = {
    "urgent": ("🔴", "bold red"),
    "normal": ("⚪", "dim"),
    "low": ("🔵", "dim blue"),
}


class ConversationItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        source = d.get("source", "")
        source_icon = "󰍦" if source == "imessage" else "󰊫"
        t = Text()

        # Priority indicator (only when non-normal or explicitly set)
        priority = d.get("_priority", "")
        if priority and priority != "normal":
            icon, style = _PRIORITY_ICONS.get(priority, ("⚪", "dim"))
            t.append(f"{icon} ", style=style)

        t.append(f"{source_icon} ", style="dim")
        if d.get("_favorite"):
            t.append("⭐ ", style="bold yellow")

        if source == "gmail":
            snippet = d.get("snippet", "")
            if d.get("_starred"):
                t.append("★ ", style="bold yellow")
            if d.get("unread"):
                t.append(snippet[:40] or "(no subject)", style="bold white")
                t.append(" ●", style="bold yellow")
            else:
                t.append(snippet[:40] or "(no subject)", style="white")
            ts = d.get("last_ts", "")
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%b %d %H:%M")
            except (ValueError, TypeError):
                time_str = ""
            name = d.get("name", "")[:20]
            t.append(f"\n  {name}", style="dim")
            acct = d.get("gmail_account", "")
            if acct:
                t.append(f" · {acct.split('@')[0]}", style="dim italic")
            if time_str:
                t.append(f"  {time_str}", style="dim")
        else:
            name = d.get("name", "?")
            if d.get("unread"):
                t.append(name, style="bold white")
                t.append(" ●", style="bold yellow")
            else:
                t.append(name, style="white")
            ts = d.get("last_ts", "")
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                time_str = ""
            snippet = d.get("snippet", "")[:38]
            line2 = f"{time_str}  {snippet}" if time_str else snippet
            t.append(f"\n  {line2}", style="dim")

        yield Static(t)


class EventItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        t = Text()
        t.append("󰃮 ", style="dim")
        if d.get("all_day"):
            t.append("All day", style="cyan")
        else:
            try:
                start = datetime.fromisoformat(d["start"]).strftime("%H:%M")
                end = datetime.fromisoformat(d["end"]).strftime("%H:%M")
                t.append(f"{start}–{end}", style="cyan")
            except (ValueError, KeyError):
                t.append("???", style="cyan")
        t.append(f"  {d.get('summary', '?')}", style="bold white")
        loc = d.get("location", "")
        if loc:
            t.append(f"\n  📍 {loc}", style="dim")
        yield Static(t)


class NoteItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        t = Text()
        t.append("󰎞 ", style="dim")
        t.append(d.get("title", "Untitled"), style="bold white")
        snippet = d.get("snippet", "")[:40]
        folder = d.get("folder", "")
        line2 = ""
        if folder:
            line2 = f"{folder} · "
        line2 += snippet
        if line2:
            t.append(f"\n  {line2}", style="dim")
        yield Static(t)


class ReminderItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        t = Text()
        t.append("☐ ", style="yellow")
        t.append(d.get("title", "Untitled"), style="bold white")
        due = d.get("due_date", "")
        list_name = d.get("list_name", "")
        line2_parts: list[str] = []
        if due:
            try:
                dt = datetime.fromisoformat(due)
                line2_parts.append(dt.strftime("%b %d"))
            except (ValueError, TypeError):
                line2_parts.append(due)
        else:
            line2_parts.append("No date")
        if list_name:
            line2_parts.append(list_name)
        line2 = " · ".join(line2_parts)
        if d.get("flagged"):
            t.append(" 🏴", style="red")
        if d.get("priority", 0) > 0:
            t.append(" ❗", style="yellow")
        if line2:
            t.append(f"\n  {line2}", style="dim")
        if d.get("notes"):
            snippet = d["notes"][:40]
            t.append(f"\n  {snippet}", style="dim italic")
        yield Static(t)


class DriveItem(ListItem):
    """Widget for a Google Drive file/folder in the sidebar."""

    _MIME_ICONS: dict[str, str] = {
        "application/vnd.google-apps.folder": "📁",
        "application/vnd.google-apps.document": "📝",
        "application/vnd.google-apps.spreadsheet": "📊",
        "application/vnd.google-apps.presentation": "📽️",
        "application/pdf": "📄",
        "image/": "🖼️",
        "video/": "🎬",
        "audio/": "🎵",
        "text/": "📄",
    }

    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    @staticmethod
    def _icon_for_mime(mime: str) -> str:
        if mime in DriveItem._MIME_ICONS:
            return DriveItem._MIME_ICONS[mime]
        for prefix, icon in DriveItem._MIME_ICONS.items():
            if "/" in prefix and mime.startswith(prefix):
                return icon
        return "📄"

    @staticmethod
    def _human_size(size: int) -> str:
        if size <= 0:
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
            size /= 1024  # type: ignore[assignment]
        return f"{size:.1f} TB"

    def compose(self) -> ComposeResult:
        d = self.data
        mime = d.get("mime_type", "")
        t = Text()
        t.append(f"{self._icon_for_mime(mime)} ", style="dim")
        t.append(d.get("name", "Untitled"), style="bold white")

        line2_parts: list[str] = []
        mod = d.get("modified", "")
        if mod:
            try:
                dt = datetime.fromisoformat(mod)
                line2_parts.append(dt.strftime("%b %d %H:%M"))
            except (ValueError, TypeError):
                pass
        size = d.get("size", 0)
        human = self._human_size(size)
        if human:
            line2_parts.append(human)
        acct = d.get("account", "")
        if acct:
            line2_parts.append(acct.split("@")[0])
        if line2_parts:
            t.append(f"\n  {' · '.join(line2_parts)}", style="dim")
        yield Static(t)


class NotificationItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        t = Text()
        reason = d.get("reason", "")
        ntype = d.get("type", "")
        is_pr_review = reason == "review_requested" or ntype == "PullRequest"

        if is_pr_review:
            t.append("🔀 ", style="magenta bold")
        elif ntype == "Issue":
            t.append("🐛 ", style="dim")
        elif ntype == "Release":
            t.append("📦 ", style="dim")
        else:
            t.append("🔔 ", style="dim")

        # Title
        if d.get("unread"):
            t.append(d.get("title", "Untitled"), style="bold white")
        else:
            t.append(d.get("title", "Untitled"), style="white")

        if d.get("unread"):
            t.append(" ●", style="bold yellow")

        # Repo name + reason on line 2
        repo = d.get("repo", "")
        line2_parts: list[str] = []
        if repo:
            line2_parts.append(repo)
        if reason:
            # Make reason more human-readable
            reason_display = reason.replace("_", " ")
            line2_parts.append(reason_display)
        line2 = " · ".join(line2_parts)
        if line2:
            t.append(f"\n  {line2}", style="dim")

        # Timestamp on line 3
        ts = d.get("updated_at", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%b %d %H:%M")
                t.append(f"\n  {time_str}", style="dim")
            except (ValueError, TypeError):
                pass

        yield Static(t)


class IndexedThreadItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        t = Text()
        if d.get("needs_reply"):
            t.append("↩ ", style="bold yellow")
        else:
            t.append("• ", style="dim")
        t.append(d.get("subject", "Untitled"), style="bold white")
        participants = ", ".join(d.get("participants", [])[:2])
        meta_parts: list[str] = []
        if participants:
            meta_parts.append(participants)
        workflow = d.get("workflow", "")
        if workflow:
            meta_parts.append(workflow)
        if meta_parts:
            t.append(f"\n  {' · '.join(meta_parts)}", style="dim")
        brief = d.get("brief", "") or d.get("summary", "")
        if brief:
            t.append(f"\n  {brief[:72]}", style="dim")
        yield Static(t)


class MessageView(Static):
    DEFAULT_CSS = """
    MessageView {
        padding: 1 2;
        overflow-y: auto;
    }
    """
    messages: reactive[list[dict]] = reactive([], recompose=True)
    ai_summary: reactive[dict | None] = reactive(None, recompose=True)

    def compose(self) -> ComposeResult:
        if not self.messages:
            yield Label("[dim]Select a conversation[/]")
            return

        # Show AI summary banner if available
        if self.ai_summary:
            s = self.ai_summary
            t = Text()
            t.append("✨ AI Summary\n", style="bold cyan")
            if s.get("summary"):
                t.append(f"{s['summary']}\n", style="white")
            if s.get("action_items"):
                t.append("Actions: ", style="bold yellow")
                t.append(", ".join(s["action_items"][:3]) + "\n", style="yellow")
            yield Static(
                Panel(t, border_style="cyan dim", padding=(0, 1)),
            )

        for msg in self.messages:
            body = msg.get("body", "").strip()
            if not body:
                continue
            try:
                ts = datetime.fromisoformat(msg["ts"]).strftime("%H:%M")
            except (ValueError, KeyError):
                ts = ""
            # Append attachment metadata if present
            atts = msg.get("attachments", [])
            if atts:
                att_parts = []
                for att in atts:
                    fname = att.get("filename", "file")
                    size = att.get("size", 0)
                    if size >= 1024 * 1024:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                    elif size >= 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size} B"
                    att_parts.append(f"📎 {fname} ({size_str})")
                body = body + "\n" + "\n".join(att_parts)

            body_text = Text(body)
            is_me = msg.get("is_me", False)
            if is_me:
                panel = Panel(
                    Align.right(body_text),
                    title=f"[dim]{ts}[/]",
                    title_align="right",
                    border_style="cyan",
                    padding=(0, 1),
                )
                yield Static(Align.right(panel))
            else:
                sender = msg.get("sender", "?")
                panel = Panel(
                    body_text,
                    title=f"[bold green]{sender}[/]  [dim]{ts}[/]",
                    title_align="left",
                    border_style="green",
                    padding=(0, 1),
                )
                yield Static(panel)

    def watch_messages(self) -> None:
        self.call_later(self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        self.scroll_end(animate=False)


class DetailView(Static):
    DEFAULT_CSS = """
    DetailView {
        padding: 1 2;
        overflow-y: auto;
    }
    """
    detail: reactive[dict | None] = reactive(None, recompose=True)

    def compose(self) -> ComposeResult:
        if not self.detail:
            yield Label("[dim]Select an item[/]")
            return
        d = self.detail
        t = Text()

        if "summary" in d:
            # Calendar event
            t.append(f"{d['summary']}\n", style="bold white")
            t.append("─" * 40 + "\n", style="dim")
            if d.get("all_day"):
                t.append("All day\n", style="cyan")
            else:
                try:
                    start = datetime.fromisoformat(d["start"]).strftime("%H:%M")
                    end = datetime.fromisoformat(d["end"]).strftime("%H:%M")
                    t.append(f"{start} – {end}\n", style="cyan")
                except (ValueError, KeyError):
                    pass
            if d.get("location"):
                t.append(f"\n📍 {d['location']}\n", style="green")
            if d.get("description"):
                t.append(f"\n{d['description']}\n", style="white")
            # Attendees
            attendees = d.get("attendees", [])
            if attendees:
                t.append("\n👥 Attendees\n", style="bold")
                for att in attendees:
                    name = att.get("name") or att.get("email", "?")
                    email = att.get("email", "")
                    status = att.get("responseStatus", "")
                    icon = {
                        "accepted": "✅",
                        "declined": "❌",
                        "tentative": "❓",
                        "needsAction": "⏳",
                    }.get(status, "•")
                    label = name
                    if email and email != name:
                        label += f" ({email})"
                    t.append(f"  {icon} {label}\n", style="white")
            if d.get("account"):
                t.append(f"\n[{d['account']}]", style="dim italic")

        elif d.get("completed") is not None and "list_name" in d:
            # Reminder — identified by completed field + list_name
            t.append(f"{d['title']}\n", style="bold white")
            t.append("─" * 40 + "\n", style="dim")
            if d.get("completed"):
                t.append("✅ Completed\n", style="green")
            else:
                t.append("☐ Incomplete\n", style="yellow")
            if d.get("due_date"):
                try:
                    dt = datetime.fromisoformat(d["due_date"])
                    t.append(f"📅 Due: {dt.strftime('%b %d, %Y %H:%M')}\n", style="cyan")
                except (ValueError, KeyError):
                    t.append(f"📅 Due: {d['due_date']}\n", style="cyan")
            else:
                t.append("📅 No due date\n", style="dim")
            if d.get("list_name"):
                t.append(f"📋 List: {d['list_name']}\n", style="dim")
            if d.get("priority", 0) > 0:
                t.append(f"❗ Priority: {d['priority']}\n", style="yellow")
            if d.get("flagged"):
                t.append("🏴 Flagged\n", style="red")
            if d.get("notes"):
                t.append(f"\n{d['notes']}\n", style="white")

        elif "reason" in d and "repo" in d:
            # GitHub notification — identified by reason + repo fields
            is_pr_review = d.get("reason") == "review_requested" or d.get("type") == "PullRequest"
            if is_pr_review:
                t.append("🔀 ", style="magenta bold")
            else:
                t.append("🔔 ", style="dim")
            t.append(f"{d.get('title', 'Untitled')}\n", style="bold white")
            t.append("─" * 40 + "\n", style="dim")
            t.append(f"📦 {d.get('repo', '?')}\n", style="cyan")
            reason = d.get("reason", "").replace("_", " ")
            t.append(f"📌 {reason}\n", style="yellow" if is_pr_review else "dim")
            ntype = d.get("type", "")
            if ntype:
                t.append(f"🏷  {ntype}\n", style="dim")
            if d.get("unread"):
                t.append("● Unread\n", style="bold yellow")
            else:
                t.append("  Read\n", style="dim")
            ts = d.get("updated_at", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    t.append(f"🕐 {dt.strftime('%b %d, %Y %H:%M')}\n", style="dim")
                except (ValueError, TypeError):
                    pass
            url = d.get("url", "")
            if url:
                t.append(f"\n🔗 {url}\n", style="blue")
            t.append("\n", style="")
            t.append("[dim]r: mark read · R: read all · o: open in browser[/]\n", style="dim")

        elif "title" in d:
            # Note
            t.append(f"{d['title']}\n", style="bold white")
            t.append("─" * 40 + "\n", style="dim")
            if d.get("folder"):
                t.append(f"📁 {d['folder']}\n", style="dim")
            if d.get("modified"):
                try:
                    mod = datetime.fromisoformat(d["modified"]).strftime("%b %d, %Y %H:%M")
                    t.append(f"Modified: {mod}\n", style="dim")
                except (ValueError, KeyError):
                    pass
            body = d.get("body", d.get("snippet", ""))
            if body:
                t.append(f"\n{body}\n", style="white")

        elif "thread_id" in d and "owning_account" in d:
            t.append(f"{d.get('subject', 'Untitled')}\n", style="bold white")
            t.append("─" * 40 + "\n", style="dim")
            participants = d.get("participants", [])
            if participants:
                t.append(f"Participants: {', '.join(participants)}\n", style="dim")
            if d.get("owning_account"):
                t.append(f"Account: {d['owning_account']}\n", style="dim")
            if d.get("workflow"):
                t.append(f"Workflow: {d['workflow']}\n", style="cyan")
            if d.get("needs_reply"):
                t.append("Needs reply\n", style="bold yellow")
            if d.get("summary"):
                t.append(f"\n{d['summary']}\n", style="white")
            action_items = d.get("action_items", [])
            if action_items:
                t.append("\nAction items\n", style="bold yellow")
                for action in action_items[:5]:
                    t.append(f"  • {action}\n", style="yellow")
            if d.get("brief"):
                t.append(f"\n{d['brief']}\n", style="dim")

        yield Static(t)


# ── Contact Profile Screen ───────────────────────────────────────────────────


class ContactProfileScreen(Screen):
    DEFAULT_CSS = """
    ContactProfileScreen {
        align: center middle;
    }
    #profile-container {
        width: 70;
        height: 35;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    #profile-scroll {
        height: 1fr;
        overflow-y: auto;
    }
    """
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, profile: dict) -> None:
        super().__init__()
        self._profile = profile

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-container"):
            yield Static(self._render_header(), id="profile-header")
            with ScrollableContainer(id="profile-scroll"):
                yield Static(self._render_body(), id="profile-body")

    def _render_header(self) -> Text:
        contact = self._profile.get("contact", {})
        t = Text()
        name = contact.get("name", "Unknown")
        t.append(f"  {name}\n", style="bold white")
        t.append("─" * 40 + "\n", style="dim")
        for email in contact.get("emails", []):
            t.append(f"  {email}\n", style="cyan")
        for phone in contact.get("phones", []):
            t.append(f"  {phone}\n", style="green")
        counts = contact.get("source_counts", {})
        parts = []
        if counts.get("imessage"):
            parts.append(f"iMsg: {counts['imessage']}")
        if counts.get("gmail"):
            parts.append(f"Gmail: {counts['gmail']}")
        if counts.get("calendar"):
            parts.append(f"Cal: {counts['calendar']}")
        if parts:
            t.append("  " + "  ·  ".join(parts) + "\n", style="dim")
        t.append("[dim]Esc: close[/]\n", style="dim")
        return t

    def _render_body(self) -> Text:
        t = Text()
        timeline = self._profile.get("timeline", [])
        if not timeline:
            t.append("No activity found.\n", style="dim")
            return t
        t.append("\nTimeline\n", style="bold yellow")
        t.append("─" * 38 + "\n", style="dim")
        for item in timeline[:20]:
            src = item.get("source", "")
            src_label = {"imessage": "[iMsg]", "gmail": "[Gmail]", "calendar": "[Cal]"}.get(
                src, f"[{src}]"
            )
            ts_str = item.get("ts") or item.get("start", "")
            try:
                ts_label = datetime.fromisoformat(ts_str).strftime("%b %d %H:%M")
            except (ValueError, TypeError):
                ts_label = ""
            t.append(f"{src_label} ", style="dim")
            if ts_label:
                t.append(f"{ts_label}  ", style="dim cyan")
            body = item.get("body") or item.get("summary", "")
            sender = item.get("sender", "")
            if sender and not item.get("is_me"):
                t.append(f"{sender}: ", style="dim italic")
            t.append(f"{body[:80]}\n", style="white")
        return t

    def action_dismiss(self) -> None:
        self.app.pop_screen()


# ── Command Palette ───────────────────────────────────────────────────────────


class CommandPaletteScreen(ModalScreen[CommandDict | None]):
    """Modal overlay for the command palette (Ctrl+P)."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center middle;
    }
    #palette-container {
        width: 60;
        height: auto;
        max-height: 24;
        background: $surface;
        border: solid $primary;
        padding: 0;
    }
    #palette-input {
        width: 1fr;
        border-bottom: solid $primary-darken-2;
    }
    #palette-list {
        height: auto;
        max-height: 18;
    }
    #palette-footer {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def __init__(self, commands: list[CommandDict], llm_available: bool = False) -> None:
        super().__init__()
        self._commands = commands
        self._filtered: list[CommandDict] = list(commands)
        self._llm_available = llm_available
        self._nlp_hint: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Input(placeholder="Type a command…", id="palette-input")
            yield ListView(id="palette-list")
            yield Static(self._footer_text(), id="palette-footer")

    def _footer_text(self) -> str:
        if self._nlp_hint:
            return self._nlp_hint
        if not self._llm_available:
            return "↑↓ navigate · Enter execute · Esc close · (LLM unavailable)"
        return "↑↓ navigate · Enter execute · Esc close · NLP enabled"

    def on_mount(self) -> None:
        self._rebuild_list(self._commands)
        self.query_one("#palette-input", Input).focus()

    def _rebuild_list(self, cmds: list[CommandDict]) -> None:
        lv = self.query_one("#palette-list", ListView)
        lv.clear()
        for cmd in cmds:
            t = Text()
            t.append(f"[{cmd['category']}] ", style="dim cyan")
            t.append(cmd["name"], style="bold white")
            if cmd["description"]:
                t.append(f"  {cmd['description']}", style="dim")
            item = ListItem(Static(t))
            item.data = cmd  # type: ignore[attr-defined]
            lv.append(item)
        if cmds:
            lv.index = 0

    @on(Input.Changed, "#palette-input")
    def on_query_changed(self, event: Input.Changed) -> None:
        query = event.value
        self._filtered = filter_commands(query, self._commands)
        self._nlp_hint = ""
        self._rebuild_list(self._filtered)
        self.query_one("#palette-footer", Static).update(self._footer_text())

    @on(Input.Submitted, "#palette-input")
    def on_query_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()

        # If there's a selected item in the list, run it
        lv = self.query_one("#palette-list", ListView)
        if lv.highlighted_child is not None:
            item = lv.highlighted_child
            cmd = getattr(item, "data", None)
            if cmd is not None:
                self.dismiss(cmd)
                return

        # If query matches nothing directly, try NLP
        if query and not self._filtered:
            self._try_nlp(query)
        elif self._filtered:
            self.dismiss(self._filtered[0])

    def _try_nlp(self, query: str) -> None:
        matched, msg = resolve_nlp(query, self._commands)
        if matched is not None:
            self.dismiss(matched)
        else:
            self._nlp_hint = msg
            self.query_one("#palette-footer", Static).update(msg)

    @on(ListView.Selected, "#palette-list")
    def on_list_selected(self, event: ListView.Selected) -> None:
        item = event.item
        cmd = getattr(item, "data", None)
        if cmd is not None:
            self.dismiss(cmd)


# ── Search Overlay ───────────────────────────────────────────────────────────

_SOURCE_ICONS: dict[str, str] = {
    "imessage": "󰍦",
    "gmail": "󰊫",
    "notes": "󰎞",
    "reminders": "☐",
    "calendar": "󰃮",
}


class SearchResultItem(ListItem):
    def __init__(self, data: dict) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        source = d.get("source", "")
        icon = _SOURCE_ICONS.get(source, "●")
        t = Text()
        t.append(f"{icon} ", style="dim")
        t.append(d.get("title", "?"), style="bold white")
        ts = d.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                t.append(f"  {dt.strftime('%b %d %H:%M')}", style="dim")
            except (ValueError, TypeError):
                pass
        snippet = d.get("snippet", "")
        if snippet:
            t.append(f"\n  {snippet[:80]}", style="dim italic")
        t.append(f"\n  [{source}]", style="dim cyan")
        yield Static(t)


class SearchScreen(ModalScreen):
    DEFAULT_CSS = """
    SearchScreen {
        align: center middle;
    }
    SearchScreen > Vertical {
        width: 80;
        height: 30;
        border: solid $primary;
        background: $background;
        padding: 0;
    }
    SearchScreen #search-input {
        width: 1fr;
        height: 3;
        border: none;
    }
    SearchScreen #search-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    SearchScreen #search-results {
        height: 1fr;
        border-top: solid $primary-darken-2;
    }
    """

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client
        self._search_results: list[dict] = []
        self._debounce_timer = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="Search… (Esc to close)", id="search-input")
            yield Static("", id="search-status")
            yield ListView(id="search-results")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        q = event.value.strip()
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
        if not q:
            self.query_one("#search-results", ListView).clear()
            self.query_one("#search-status", Static).update("")
            return
        self._debounce_timer = self.set_timer(0.3, lambda: self._do_search(q))

    def _do_search(self, q: str) -> None:
        self.query_one("#search-status", Static).update("[dim]Searching…[/]")
        self._run_search(q)

    @work(thread=True, exit_on_error=False)
    def _run_search(self, q: str) -> None:
        try:
            result = self._client.search(q, sources=["all"], limit=50)
        except Exception:
            self.app.call_from_thread(  # type: ignore[attr-defined]
                self.query_one("#search-status", Static).update,
                "[red]Search failed[/]",
            )
            return
        self.app.call_from_thread(self._show_results, result)  # type: ignore[attr-defined]

    def _show_results(self, result: dict) -> None:
        self._search_results = result.get("results", [])
        lv = self.query_one("#search-results", ListView)
        lv.clear()
        for r in self._search_results:
            lv.append(SearchResultItem(r))
        total = result.get("total", 0)
        source_counts: dict[str, int] = {}
        for r in self._search_results:
            s = r.get("source", "?")
            source_counts[s] = source_counts.get(s, 0) + 1
        count_str = "  ".join(f"{s}:{n}" for s, n in source_counts.items())
        status = f"[green]{total} results[/]"
        if count_str:
            status += f"  [dim]{count_str}[/]"
        self.query_one("#search-status", Static).update(status)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.prevent_default()
            self.dismiss(None)
        elif event.key == "enter":
            lv = self.query_one("#search-results", ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self._search_results):
                event.prevent_default()
                self.dismiss(self._search_results[idx])

    @on(ListView.Selected, "#search-results")
    def on_result_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SearchResultItem):
            self.dismiss(item.data)


# ── AI Widgets ───────────────────────────────────────────────────────────────


class BriefingModal(ModalScreen):
    """Modal overlay showing the morning briefing (Ctrl+B)."""

    DEFAULT_CSS = """
    BriefingModal {
        align: center middle;
    }
    BriefingModal > Vertical {
        width: 70;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, briefing: dict) -> None:
        super().__init__()
        self._briefing = briefing

    def compose(self) -> ComposeResult:
        d = self._briefing
        t = Text()
        t.append("Morning Briefing\n", style="bold cyan")
        t.append("─" * 40 + "\n", style="dim")

        summary = d.get("summary")
        if summary:
            t.append(f"{summary}\n\n", style="white")

        counts = d.get("unread_counts", {})
        imsg = counts.get("imessage", 0)
        gmail = counts.get("gmail", 0)
        gh_notifs = counts.get("github_notifications", 0)
        gh_prs = counts.get("github_prs", 0)
        t.append("📬 Unread\n", style="bold")
        t.append(f"  iMessage: {imsg}  Gmail: {gmail}  GitHub: {gh_notifs}", style="white")
        if gh_prs:
            t.append(f"  ({gh_prs} PRs for review)", style="yellow")
        t.append("\n\n")

        events = d.get("events", [])
        if events:
            t.append(f"📅 Today — {len(events)} event(s)\n", style="bold")
            for e in events[:5]:
                summary_txt = e.get("summary", "?")
                if e.get("all_day"):
                    t.append(f"  • {summary_txt} (all day)\n", style="white")
                else:
                    try:
                        start = datetime.fromisoformat(e["start"]).strftime("%H:%M")
                        end = datetime.fromisoformat(e["end"]).strftime("%H:%M")
                        t.append(f"  • {start}–{end} {summary_txt}\n", style="white")
                    except (ValueError, KeyError):
                        t.append(f"  • {summary_txt}\n", style="white")
        else:
            t.append("📅 No events today\n", style="dim")

        t.append("\n")
        pending = d.get("pending_reminders", [])
        if pending:
            t.append(f"☐ {len(pending)} pending reminder(s)\n", style="bold yellow")
            for r in pending[:3]:
                t.append(f"  • {r.get('title', '?')}\n", style="white")
        else:
            t.append("☐ All reminders done!\n", style="dim green")

        t.append("\n[dim]Press Escape to close[/]\n", style="dim")

        with Vertical():
            yield Static(t)


class AssistantPromptModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    AssistantPromptModal {
        align: center middle;
    }
    AssistantPromptModal > Vertical {
        width: 72;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #assistant-query {
        width: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[bold cyan]Ask Inbox Assistant[/]\n"
                "[dim]Readonly local Codex session with Inbox MCP attached.[/]"
            )
            yield Input(placeholder="Ask about the current selection…", id="assistant-query")

    def on_mount(self) -> None:
        self.query_one("#assistant-query", Input).focus()

    @on(Input.Submitted, "#assistant-query")
    def on_query_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self.dismiss(query or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AssistantResultModal(ModalScreen):
    DEFAULT_CSS = """
    AssistantResultModal {
        align: center middle;
    }
    AssistantResultModal > Vertical {
        width: 88;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #assistant-result-body {
        height: 1fr;
        overflow-y: auto;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, query: str, result_text: str, status_line: str) -> None:
        super().__init__()
        self._query = query
        self._result_text = result_text
        self._status_line = status_line

    def compose(self) -> ComposeResult:
        header = Text()
        header.append("Inbox Assistant\n", style="bold cyan")
        header.append(f"{self._query}\n", style="white")
        header.append(self._status_line, style="dim")
        with Vertical():
            yield Static(header)
            with ScrollableContainer(id="assistant-result-body"):
                yield Static(Text(self._result_text))

    def action_dismiss(self) -> None:
        self.app.pop_screen()


# ── Main App ─────────────────────────────────────────────────────────────────


class InboxApp(App):
    CSS = """
    Screen { layout: vertical; }
    #main { layout: horizontal; height: 1fr; }
    #sidebar {
        width: 32;
        border-right: solid $primary-darken-2;
        layout: vertical;
    }
    #tabs { height: 3; }
    Tabs { height: 3; }
    Tab { padding: 0 2; }
    #contact-list { height: 1fr; }
    #content { width: 1fr; layout: vertical; }
    #messages {
        height: 1fr;
        border-bottom: solid $primary-darken-2;
        overflow-y: auto;
        padding: 1 2;
    }
    #detail-view {
        height: 1fr;
        border-bottom: solid $primary-darken-2;
        overflow-y: auto;
        padding: 1 2;
    }
    #compose-area { height: 3; layout: horizontal; padding: 0 1; }
    #compose { width: 1fr; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+backslash", "search", "Search"),
        Binding("ctrl+1", "filter_all", "All"),
        Binding("ctrl+2", "filter_imsg", "iMessage"),
        Binding("ctrl+3", "filter_gmail", "Gmail"),
        Binding("ctrl+4", "filter_cal", "Calendar"),
        Binding("ctrl+5", "filter_notes", "Notes"),
        Binding("ctrl+6", "filter_rem", "Reminders"),
        Binding("ctrl+7", "filter_gh", "GitHub"),
        Binding("ctrl+8", "filter_drv", "Drive"),
        Binding("ctrl+9", "filter_actionable", "Actionable"),
        Binding("ctrl+0", "filter_waiting", "Waiting On"),
        Binding("ctrl+shift+6", "toggle_ambient", "Ambient"),
        Binding("ctrl+a", "add_account", "Add Account"),
        Binding("ctrl+shift+a", "reauth_account", "Re-auth"),
        Binding("ctrl+n", "new_event", "New Event"),
        Binding("ctrl+d", "delete_event", "Delete Event"),
        Binding("ctrl+g", "jump_to_date", "Go to Date"),
        Binding("ctrl+b", "morning_briefing", "Briefing"),
        Binding("escape", "clear_compose", "Clear"),
        Binding("ctrl+q", "quit", "Quit"),
        # ── Vim mode bindings (active when no input has focus) ──
        Binding("j", "vim_down", "↓", show=False),
        Binding("k", "vim_up", "↑", show=False),
        Binding("g", "vim_top", "Top", show=False),
        Binding("shift+g", "vim_bottom", "Bottom", show=False),
        Binding("slash", "search", "Search", show=False),
        Binding("question_mark", "vim_help", "Help", show=False),
    ]

    POLL_INTERVAL = _poll_interval_from_env()

    # After this many consecutive poll errors, show a persistent outage message
    _SUSTAINED_OUTAGE_THRESHOLD = 3

    # Bell indicator: reactive string shown in the header sub-title area
    bell_indicator: reactive[str] = reactive("", recompose=False)

    def __init__(self) -> None:
        super().__init__()
        # Use longer timeout for first requests (data loading can be slow)
        self.client = InboxClient(timeout=60)
        self.conversations: list[dict] = []
        self.now_threads: list[dict] = []
        self.actionable_threads: list[dict] = []
        self.waiting_threads: list[dict] = []
        self.events: list[dict] = []
        self.notes_data: list[dict] = []
        self.reminders_data: list[dict] = []
        self.reminder_lists: list[dict] = []
        self.github_data: list[dict] = []
        self.drive_data: list[dict] = []
        self.active_conv: dict | None = None
        self.active_index_thread: dict | None = None
        self.active_event: dict | None = None
        self.active_reminder: dict | None = None
        self.active_notification: dict | None = None
        self.active_drive_file: dict | None = None
        self._active_filter: str = "all"
        self._rem_list_filter: str = ""  # "" = all lists
        self._editing_reminder: dict | None = None
        self._drive_folder_id: str = ""
        self._drive_folder_stack: list[str] = []
        self._drive_upload_mode: bool = False
        self._drive_new_folder_mode: bool = False
        self._editing_event: dict | None = None
        self._editing_event_field: str = ""  # current field being edited
        self._poll_timer = None
        self._client_closed = False
        self._poll_had_error = False
        self._consecutive_errors = 0
        # Calendar state
        self._calendar_date: date = date.today()
        self._calendar_view_mode: str = "day"  # "day" | "week" | "agenda"
        self._jump_to_date_mode: bool = False
        # Per-tab state: stores selected conversation, messages, detail, etc.
        # keyed by filter name ("all", "imessage", "gmail", "calendar", etc.)
        self._tab_state: dict[str, dict] = {}
        # Gmail compose state
        self._gmail_compose_mode: str = ""  # "" | "to" | "subject" | "body"
        self._gmail_compose_to: str = ""
        self._gmail_compose_subject: str = ""
        # Gmail label browsing
        self._gmail_label_filter: str = "INBOX"
        self._gmail_labels: list[dict] = []
        # Gmail starred conversations (local cache for ★ display)
        self._gmail_starred: set[str] = set()
        # Notification tracking: previous unread counts for change detection
        self._prev_imsg_unread: int = -1
        self._prev_gmail_unread: int = -1
        self._prev_github_unread: int = -1
        # Calendar event notification tracking: set of event_ids already notified
        self._notified_events: set[str] = set()
        # Favorited contact IDs (persisted to ~/.config/inbox/favorites.json)
        self._favorites: set[str] = set()
        # AI triage priorities: {conv_id: "urgent"|"normal"|"low"}
        self._triage_priorities: dict[str, str] = {}
        # AI thread summary cache: {thread_id: summary_dict}
        self._thread_summaries: dict[str, dict] = {}
        # AI action items cache
        self._message_actions: dict[str, list] = {}  # type: ignore[type-arg]

    def _update_github_badge(self) -> None:
        """Update the GitHub tab label with the unread count badge.

        Attempts to update the tab label. If the label update doesn't
        render (a known Textual issue in some versions), the unread
        count is still visible in the status bar text.
        """
        unread = sum(1 for n in self.github_data if n.get("unread"))
        try:
            tabs = self.query_one("#tabs", Tabs)
            gh_tab = tabs.get_tab("tab-gh")
            if gh_tab is not None:
                label = f"GitHub ({unread})" if unread else "GitHub"
                gh_tab.label = label
        except Exception:
            pass

    def _update_bell_indicator(self) -> None:
        """Recompute total unread count and update the sub-title bell indicator."""
        imsg_unread = sum(
            c.get("unread", 0) for c in self.conversations if c.get("source") == "imessage"
        )
        gmail_unread = sum(
            c.get("unread", 0) for c in self.conversations if c.get("source") == "gmail"
        )
        github_unread = sum(1 for n in self.github_data if n.get("unread"))
        total = imsg_unread + gmail_unread + github_unread
        self.bell_indicator = f"🔔 {total}" if total > 0 else ""
        try:
            header = self.query_one(Header)
            header.sub_title = self.bell_indicator  # type: ignore[attr-defined]
        except Exception:
            pass

    def _check_and_fire_notifications(
        self,
        convos: list[dict],
        github_data: list[dict],
        events: list[dict],
    ) -> None:
        """Compare new data against prior counts and fire desktop notifications for changes."""
        # iMessage: new unreads
        imsg_unread = sum(c.get("unread", 0) for c in convos if c.get("source") == "imessage")
        if self._prev_imsg_unread >= 0 and imsg_unread > self._prev_imsg_unread:
            delta = imsg_unread - self._prev_imsg_unread
            self._fire_notification(f"iMessage: {delta} new message(s)", "", "imessage")
        self._prev_imsg_unread = imsg_unread

        # Gmail: new unreads
        gmail_unread = sum(c.get("unread", 0) for c in convos if c.get("source") == "gmail")
        if self._prev_gmail_unread >= 0 and gmail_unread > self._prev_gmail_unread:
            delta = gmail_unread - self._prev_gmail_unread
            self._fire_notification(f"Gmail: {delta} new email(s)", "", "gmail")
        self._prev_gmail_unread = gmail_unread

        # GitHub: new @mentions
        github_unread = sum(1 for n in github_data if n.get("unread"))
        if self._prev_github_unread >= 0 and github_unread > self._prev_github_unread:
            mentions = [n for n in github_data if n.get("unread") and n.get("reason") == "mention"]
            if mentions:
                title = mentions[0].get("title", "New mention")
                repo = mentions[0].get("repo", "")
                self._fire_notification(f"GitHub mention: {title}", repo, "github")
            else:
                delta = github_unread - self._prev_github_unread
                self._fire_notification(f"GitHub: {delta} new notification(s)", "", "github")
        self._prev_github_unread = github_unread

        # Calendar: events starting within 15 minutes
        now = datetime.now()
        soon = now + timedelta(minutes=15)
        for ev in events:
            event_id = ev.get("event_id", "")
            if not event_id or event_id in self._notified_events:
                continue
            try:
                start = datetime.fromisoformat(ev["start"])
                if now <= start <= soon:
                    self._notified_events.add(event_id)
                    summary = ev.get("summary", "Event")
                    start_str = start.strftime("%H:%M")
                    self._fire_notification(f"Starting at {start_str}: {summary}", "", "calendar")
            except (ValueError, KeyError):
                pass

    def _fire_notification(self, title: str, body: str, source: str) -> None:
        """Send a desktop notification in a background thread (non-blocking)."""

        def _send() -> None:
            with contextlib.suppress(Exception):
                self.client._client.post(
                    "/notifications/test",
                    json={"title": title, "body": body or title},
                    timeout=5,
                )

        threading.Thread(target=_send, daemon=True).start()

    def _notification_still_exists(self, notification: dict) -> bool:
        """Check whether a notification is still present in github_data.

        Returns False if github_data is empty or the notification's id
        is not found in the current list. Used to detect stale selections
        after data refreshes.
        """
        if not self.github_data:
            return False
        notif_id = notification.get("id")
        return any(n.get("id") == notif_id for n in self.github_data)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                yield Tabs(
                    Tab("Now", id="tab-all"),
                    Tab("Actionable", id="tab-act"),
                    Tab("Waiting On", id="tab-wait"),
                    Tab("iMessage", id="tab-imsg"),
                    Tab("Gmail", id="tab-gmail"),
                    Tab("Calendar", id="tab-cal"),
                    Tab("Notes", id="tab-notes"),
                    Tab("Reminders", id="tab-rem"),
                    Tab("GitHub", id="tab-gh"),
                    Tab("Drive", id="tab-drv"),
                    id="tabs",
                )
                yield ListView(id="contact-list")
            with Vertical(id="content"):
                yield Static(id="status")
                yield MessageView(id="messages")
                yield DetailView(id="detail-view", classes="hidden")
                with Horizontal(id="compose-area"):
                    yield Input(
                        placeholder="Reply… (Enter to send)",
                        id="compose",
                    )
        yield Footer()

    # ── Tab switching ────────────────────────────────────────────────────

    def _save_tab_state(self, tab_name: str) -> None:
        """Save the current tab's state so it can be restored later."""
        state: dict = {}
        if tab_name in ("all", "imessage", "gmail"):
            state["active_conv"] = self.active_conv
            state["active_index_thread"] = self.active_index_thread
            msg_view = self.query_one("#messages", MessageView)
            state["messages"] = list(msg_view.messages) if msg_view.messages else []
        elif tab_name in ("actionable", "waiting"):
            state["active_index_thread"] = self.active_index_thread
            msg_view = self.query_one("#messages", MessageView)
            state["messages"] = list(msg_view.messages) if msg_view.messages else []
        elif tab_name == "calendar":
            state["active_event"] = self.active_event
            state["calendar_date"] = self._calendar_date
            state["calendar_view_mode"] = self._calendar_view_mode
            state["active_conv"] = None
        elif tab_name == "notes":
            # Note detail is in DetailView, already handled via detail reactive
            pass
        elif tab_name == "reminders":
            state["active_reminder"] = self.active_reminder
            state["rem_list_filter"] = self._rem_list_filter
            state["active_conv"] = None
        elif tab_name == "github":
            state["active_notification"] = self.active_notification
            state["active_conv"] = None
        elif tab_name == "drive":
            state["active_drive_file"] = self.active_drive_file
            state["drive_folder_id"] = self._drive_folder_id
            state["drive_folder_stack"] = list(self._drive_folder_stack)
            state["active_conv"] = None
        self._tab_state[tab_name] = state

    def _restore_tab_state(self, tab_name: str) -> None:
        """Restore a tab's previously saved state."""
        state = self._tab_state.get(tab_name, {})
        if not state:
            # No previously saved state for this tab — don't clear anything
            return

        if tab_name in ("all", "imessage", "gmail"):
            # Restore conversation selection and messages
            saved_conv = state.get("active_conv")
            saved_index_thread = state.get("active_index_thread")
            saved_msgs = state.get("messages", [])
            self.active_conv = saved_conv
            self.active_index_thread = saved_index_thread
            self.active_event = None
            self.active_reminder = None
            msg_view = self.query_one("#messages", MessageView)
            if saved_msgs:
                msg_view.messages = saved_msgs
            elif saved_index_thread:
                self._show_index_thread(saved_index_thread)
            elif saved_conv:
                # Re-load the thread if we have a conv but no cached messages
                self._load_thread(saved_conv)
            else:
                msg_view.messages = []
        elif tab_name in ("actionable", "waiting"):
            saved_index_thread = state.get("active_index_thread")
            saved_msgs = state.get("messages", [])
            self.active_conv = None
            self.active_index_thread = saved_index_thread
            self.active_event = None
            self.active_reminder = None
            msg_view = self.query_one("#messages", MessageView)
            if saved_msgs:
                msg_view.messages = saved_msgs
            elif saved_index_thread:
                self._show_index_thread(saved_index_thread)
            else:
                msg_view.messages = []
        elif tab_name == "calendar":
            self.active_event = state.get("active_event")
            self._calendar_date = state.get("calendar_date", date.today())
            self._calendar_view_mode = state.get("calendar_view_mode", "day")
            self.active_conv = None
            self.active_index_thread = None
            self.active_reminder = None
            if self.active_event:
                self.query_one("#detail-view", DetailView).detail = self.active_event
        elif tab_name == "reminders":
            self.active_reminder = state.get("active_reminder")
            self._rem_list_filter = state.get("rem_list_filter", "")
            self.active_conv = None
            self.active_event = None
            if self.active_reminder:
                self.query_one("#detail-view", DetailView).detail = self.active_reminder
        elif tab_name == "github":
            self.active_notification = state.get("active_notification")
            self.active_conv = None
            self.active_event = None
            self.active_reminder = None
            if self.active_notification:
                self.query_one("#detail-view", DetailView).detail = self.active_notification
        elif tab_name == "drive":
            self.active_drive_file = state.get("active_drive_file")
            self._drive_folder_id = state.get("drive_folder_id", "")
            self._drive_folder_stack = state.get("drive_folder_stack", [])
            self.active_conv = None
            self.active_event = None
            self.active_reminder = None
            self.active_notification = None
            if self.active_drive_file:
                self.query_one("#detail-view", DetailView).detail = self.active_drive_file
        else:
            # For tabs without saved state, just clear active selections
            self.active_conv = None
            self.active_index_thread = None
            self.active_event = None
            self.active_reminder = None

    @on(Tabs.TabActivated, "#tabs")
    def on_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_map = {
            "tab-all": "all",
            "tab-act": "actionable",
            "tab-wait": "waiting",
            "tab-imsg": "imessage",
            "tab-gmail": "gmail",
            "tab-cal": "calendar",
            "tab-notes": "notes",
            "tab-rem": "reminders",
            "tab-gh": "github",
            "tab-drv": "drive",
        }
        new_filter = tab_map.get(event.tab.id or "", "all")
        # Save state of the tab we're leaving
        if self._active_filter != new_filter:
            self._save_tab_state(self._active_filter)
        self._active_filter = new_filter
        self._render_sidebar()
        self._toggle_views()
        # Restore state of the tab we're entering
        self._restore_tab_state(new_filter)
        # Re-highlight the selected item in the sidebar
        self._restore_sidebar_selection()
        # Auto-load drive files when switching to Drive tab
        if new_filter == "drive" and not self.drive_data:
            self._load_drive_files()

    def _toggle_views(self) -> None:
        is_detail = self._active_filter in (
            "calendar",
            "notes",
            "reminders",
            "github",
            "drive",
        )
        msg_view = self.query_one("#messages", MessageView)
        det_view = self.query_one("#detail-view", DetailView)
        compose_input = self.query_one("#compose", Input)

        if is_detail:
            msg_view.add_class("hidden")
            det_view.remove_class("hidden")
            if self._active_filter == "calendar":
                compose_input.placeholder = "New event: Title 2pm-3pm @ Location (Enter)"
            elif self._active_filter == "reminders":
                compose_input.placeholder = "New reminder (Enter to create)"
            elif self._active_filter == "drive":
                compose_input.placeholder = "Search Drive files… (Enter)"
            else:
                compose_input.placeholder = ""
        else:
            msg_view.remove_class("hidden")
            det_view.add_class("hidden")
            if self._active_filter in ("all", "actionable", "waiting"):
                compose_input.placeholder = "Open Gmail/iMessage tabs for raw replies"
            else:
                compose_input.placeholder = "Reply… (Enter to send)"

    def _render_sidebar(self) -> None:
        lv = self.query_one("#contact-list", ListView)
        lv.clear()

        if self._active_filter == "calendar":
            mode = self._calendar_view_mode
            if mode == "day":
                for e in self.events:
                    lv.append(EventItem(e))
            elif mode in ("week", "agenda"):
                # Group events by date
                events_by_date: dict[str, list[dict]] = {}
                for e in self.events:
                    try:
                        edate = datetime.fromisoformat(e["start"]).date()
                    except (ValueError, KeyError):
                        edate = self._calendar_date
                    key = edate.isoformat()
                    events_by_date.setdefault(key, []).append(e)

                if mode == "week":
                    weekday = self._calendar_date.weekday()
                    monday = self._calendar_date - timedelta(days=weekday)
                    days = [monday + timedelta(days=i) for i in range(7)]
                else:  # agenda
                    days = [self._calendar_date + timedelta(days=i) for i in range(14)]

                for d in days:
                    day_events = events_by_date.get(d.isoformat(), [])
                    # Also include multi-day all-day events
                    for e in self.events:
                        if e.get("all_day"):
                            try:
                                estart = datetime.fromisoformat(e["start"]).date()
                                eend = datetime.fromisoformat(e["end"]).date()
                                if estart < d < eend and e not in day_events:
                                    day_events.append(e)
                            except (ValueError, KeyError):
                                pass
                    is_today = d == date.today()
                    day_label = d.strftime("%a %b %d")
                    if is_today:
                        day_label = f"● {day_label} (today)"
                    header_style = "bold cyan" if is_today else "bold"
                    lv.append(ListItem(Static(Text(f"── {day_label} ──", style=header_style))))
                    if day_events:
                        for e in day_events:
                            lv.append(EventItem(e))
                    else:
                        lv.append(ListItem(Static(Text("  No events", style="dim"))))

            # Build status bar
            n_accts = len(set(e.get("account", "") for e in self.events if e.get("account")))
            date_label = self._calendar_date_label()
            mode_indicator = {
                "day": "📅 Day",
                "week": "📆 Week",
                "agenda": "📋 Agenda",
            }.get(mode, "")
            status = f"[cyan]{date_label}[/]  {mode_indicator}"
            if not self.events:
                status += "  [dim]No events[/]"
            else:
                status += f"  [dim]{len(self.events)} events[/]"
            if n_accts > 1:
                status += f"  [dim]{n_accts} accounts[/]"
            status += "  [dim]v: view · ←→: nav · g: go to date · e: edit[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "notes":
            for n in self.notes_data:
                lv.append(NoteItem(n))
            status = f"[magenta]{len(self.notes_data)} notes[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "reminders":
            filtered = self.reminders_data
            if self._rem_list_filter:
                filtered = [r for r in filtered if r.get("list_name") == self._rem_list_filter]
            if not filtered:
                lv.append(ListItem(Static(Text("  All caught up! 🎉", style="dim green"))))
            else:
                for r in filtered:
                    lv.append(ReminderItem(r))
            list_tag = f" · {self._rem_list_filter}" if self._rem_list_filter else ""
            status = f"[yellow]{len(filtered)} reminders{list_tag}[/]"
            if self.reminder_lists:
                list_names = ", ".join(
                    rl.get("name", "") for rl in self.reminder_lists if rl.get("name")
                )
                status += f"  [dim]f: filter ({list_names})[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "github":
            if not self.github_data:
                lv.append(ListItem(Static(Text("  All clear! 🔔", style="dim green"))))
            else:
                for n in self.github_data:
                    lv.append(NotificationItem(n))
            unread = sum(1 for n in self.github_data if n.get("unread"))
            status = f"[magenta]{len(self.github_data)} notifications[/]"
            if unread:
                status += f"  [yellow]{unread} unread[/]"
            status += "  [dim]r: mark read · R: read all · o: open[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "drive":
            if not self.drive_data:
                folder_msg = "This folder is empty" if self._drive_folder_id else "No files"
                lv.append(ListItem(Static(Text(f"  {folder_msg}", style="dim green"))))
            else:
                for f in self.drive_data:
                    lv.append(DriveItem(f))
            folder_tag = " (subfolder)" if self._drive_folder_id else ""
            status = f"[blue]{len(self.drive_data)} files{folder_tag}[/]"
            status += "  [dim]d: download · u: upload · n: new folder · x: delete[/]"
            if self._drive_folder_id:
                status += "  [dim]Bksp: back[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "all":
            for thread in self.now_threads:
                lv.append(IndexedThreadItem(thread))
            status = f"[green]{len(self.now_threads)} indexed threads[/]"
            status += "  [dim]Now view · summaries first[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "actionable":
            for thread in self.actionable_threads:
                lv.append(IndexedThreadItem(thread))
            status = f"[green]{len(self.actionable_threads)} actionable threads[/]"
            status += "  [dim]reply / review / track[/]"
            self.query_one("#status", Static).update(status)
            return

        if self._active_filter == "waiting":
            for thread in self.waiting_threads:
                lv.append(IndexedThreadItem(thread))
            status = f"[green]{len(self.waiting_threads)} waiting-on threads[/]"
            status += "  [dim]open loops being tracked[/]"
            self.query_one("#status", Static).update(status)
            return

        shown = [c for c in self.conversations if c.get("source") == self._active_filter]

        # Sort so favorites appear first
        def _is_favorite(c: dict) -> bool:
            cid = c.get("id", "")
            name = c.get("name", "")
            reply_to = c.get("reply_to", "")
            return (
                cid in self._favorites
                or name.lower() in self._favorites
                or reply_to.lower() in self._favorites
            )

        shown = sorted(shown, key=lambda c: 0 if _is_favorite(c) else 1)

        for c in shown:
            # Mark starred conversations for display
            if c.get("source") == "gmail" and c.get("id") in self._gmail_starred:
                c["_starred"] = True
            # Mark favorites for display
            c["_favorite"] = _is_favorite(c)
            lv.append(ConversationItem(c))

        unread = sum(c.get("unread", 0) for c in shown)
        tab_label = {
            "imessage": "iMessage",
            "gmail": "Gmail",
        }.get(self._active_filter, "")
        status = f"[green]{len(shown)} conversations[/]"
        if unread:
            status += f"  [yellow]{unread} unread[/]"
        if self._active_filter == "gmail":
            label_display = self._gmail_label_filter
            status += f"  [cyan]{label_display}[/]"
            status += (
                "  [dim]a:archive d:delete s:star r:read u:unread c:compose l:label D:download[/]"
            )
        else:
            status += f"  [dim]{tab_label}[/]"
        self.query_one("#status", Static).update(status)

    def _restore_sidebar_selection(self) -> None:
        """After _render_sidebar populates the ListView, restore the
        previously selected item (if any) for the active tab."""
        lv = self.query_one("#contact-list", ListView)
        target_index = -1

        if self._active_filter in ("all", "actionable", "waiting") and self.active_index_thread:
            thread_id = self.active_index_thread.get("thread_id")
            for i, child in enumerate(lv.children):
                if (
                    isinstance(child, IndexedThreadItem)
                    and child.data.get("thread_id") == thread_id
                ):
                    target_index = i
                    break
        elif self._active_filter in ("imessage", "gmail") and self.active_conv:
            conv_id = self.active_conv.get("id")
            source = self.active_conv.get("source")
            for i, child in enumerate(lv.children):
                if (
                    isinstance(child, ConversationItem)
                    and child.data.get("id") == conv_id
                    and child.data.get("source") == source
                ):
                    target_index = i
                    break
        elif self._active_filter == "calendar" and self.active_event:
            event_id = self.active_event.get("event_id")
            for i, child in enumerate(lv.children):
                if isinstance(child, EventItem) and child.data.get("event_id") == event_id:
                    target_index = i
                    break
        elif self._active_filter == "reminders" and self.active_reminder:
            rem_id = self.active_reminder.get("id")
            for i, child in enumerate(lv.children):
                if isinstance(child, ReminderItem) and child.data.get("id") == rem_id:
                    target_index = i
                    break
        elif self._active_filter == "notes":
            # Notes restore is handled by _load_note in _restore_tab_state
            pass
        elif self._active_filter == "github" and self.active_notification:
            notif_id = self.active_notification.get("id")
            for i, child in enumerate(lv.children):
                if isinstance(child, NotificationItem) and child.data.get("id") == notif_id:
                    target_index = i
                    break
        elif self._active_filter == "drive" and self.active_drive_file:
            file_id = self.active_drive_file.get("id")
            for i, child in enumerate(lv.children):
                if isinstance(child, DriveItem) and child.data.get("id") == file_id:
                    target_index = i
                    break

        if target_index >= 0:
            lv.index = target_index

    # ── Tab shortcuts ────────────────────────────────────────────────────

    def action_filter_all(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-all"

    def action_filter_imsg(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-imsg"

    def action_filter_gmail(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-gmail"

    def action_filter_cal(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-cal"

    def action_filter_notes(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-notes"

    def action_filter_rem(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-rem"

    def action_filter_gh(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-gh"

    def action_filter_drv(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-drv"

    def action_filter_actionable(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-act"

    def action_filter_waiting(self) -> None:
        self.query_one("#tabs", Tabs).active = "tab-wait"

    # ── Vim mode actions ─────────────────────────────────────────────────

    def _vim_focused_nav_widget(self):
        """Return the currently focused navigable widget (ListView/DataTable), or None."""
        focused = self.focused
        if focused is None:
            return None
        # Walk up the widget tree looking for a navigable
        widget = focused
        while widget is not None:
            if hasattr(widget, "action_cursor_down") and hasattr(widget, "action_cursor_up"):
                return widget
            widget = getattr(widget, "parent", None)
        return None

    def _is_input_focused(self) -> bool:
        """Check if a text input widget currently has focus (should block vim keys)."""
        from textual.widgets import Input, TextArea

        focused = self.focused
        return isinstance(focused, (Input, TextArea))

    def action_vim_down(self) -> None:
        """Vim j — move cursor down in focused list/table."""
        if self._is_input_focused():
            return
        widget = self._vim_focused_nav_widget()
        if widget is not None:
            widget.action_cursor_down()

    def action_vim_up(self) -> None:
        """Vim k — move cursor up in focused list/table."""
        if self._is_input_focused():
            return
        widget = self._vim_focused_nav_widget()
        if widget is not None:
            widget.action_cursor_up()

    def action_vim_top(self) -> None:
        """Vim g — jump to top of focused list/table."""
        if self._is_input_focused():
            return
        widget = self._vim_focused_nav_widget()
        if widget is None:
            return
        if hasattr(widget, "index"):
            with contextlib.suppress(Exception):
                widget.index = 0
        elif hasattr(widget, "action_scroll_top"):
            widget.action_scroll_top()

    def action_vim_bottom(self) -> None:
        """Vim G — jump to bottom of focused list/table."""
        if self._is_input_focused():
            return
        widget = self._vim_focused_nav_widget()
        if widget is None:
            return
        if hasattr(widget, "index"):
            try:
                # Get the length of children/rows if available
                length = 0
                if hasattr(widget, "row_count"):
                    length = widget.row_count
                elif hasattr(widget, "children"):
                    length = len(widget.children)
                if length > 0:
                    widget.index = length - 1
            except Exception:
                pass
        elif hasattr(widget, "action_scroll_bottom"):
            widget.action_scroll_bottom()

    def action_vim_help(self) -> None:
        """Show vim shortcuts help overlay."""
        if self._is_input_focused():
            return
        help_text = (
            "Vim shortcuts:\n"
            "  j  — down\n"
            "  k  — up\n"
            "  g  — top\n"
            "  G  — bottom\n"
            "  /  — search\n"
            "  ?  — this help\n"
            "  Esc — clear/close"
        )
        try:
            self.query_one("#status", Static).update(help_text)
        except Exception:
            self.notify(help_text)

    # ── Command palette ──────────────────────────────────────────────────

    def action_command_palette(self) -> None:
        """Open the command palette (Ctrl+P)."""
        try:
            from services import llm_is_loaded

            llm_avail = llm_is_loaded()
        except Exception:
            llm_avail = False
        commands = build_commands(self)
        self.push_screen(
            CommandPaletteScreen(commands, llm_available=llm_avail), self._on_palette_result
        )

    def _on_palette_result(self, result: CommandDict | None) -> None:
        if result is not None:
            try:
                result["action"]()
            except Exception as exc:
                self.query_one("#status", Static).update(f"[red]Command failed: {exc}[/]")

    # ── Search overlay ───────────────────────────────────────────────────

    def action_search(self) -> None:
        """Open the search overlay (Ctrl+\\)."""
        self.push_screen(SearchScreen(self.client), self._on_search_result)

    def _on_search_result(self, result: dict | None) -> None:
        """Called when search overlay is dismissed with a selected result."""
        if result is None:
            return
        source = result.get("source", "")
        item_id = result.get("id", "")
        metadata = result.get("metadata", {})

        # Switch to the appropriate tab
        tab_map = {
            "imessage": "tab-imsg",
            "gmail": "tab-gmail",
            "notes": "tab-notes",
            "reminders": "tab-rem",
            "calendar": "tab-cal",
        }
        tab_id = tab_map.get(source)
        if not tab_id:
            self.notify(f"Cannot navigate to source: {source}", severity="warning")
            return

        self.query_one("#tabs", Tabs).active = tab_id

        # After tab switch, attempt to select the item
        self.call_after_refresh(self._select_search_result, source, item_id, metadata)

    def _select_search_result(self, source: str, item_id: str, metadata: dict) -> None:
        """Select the jumped-to item in the sidebar after tab switch."""
        lv = self.query_one("#contact-list", ListView)

        if source == "imessage":
            for i, child in enumerate(lv.children):
                if isinstance(child, ConversationItem) and child.data.get("id") == item_id:
                    lv.index = i
                    self.active_conv = child.data
                    self._load_thread(child.data)
                    return
            self.notify("Conversation not found — it may have changed", severity="warning")

        elif source == "gmail":
            for i, child in enumerate(lv.children):
                if isinstance(child, ConversationItem) and child.data.get("id") == item_id:
                    lv.index = i
                    self.active_conv = child.data
                    self._load_thread(child.data)
                    return
            self.notify("Email not found — it may have changed", severity="warning")

        elif source == "notes":
            for i, child in enumerate(lv.children):
                if isinstance(child, NoteItem) and child.data.get("id") == item_id:
                    lv.index = i
                    self._load_note(child.data)
                    return
            self.notify("Note not found — it may have changed", severity="warning")

        elif source == "reminders":
            for i, child in enumerate(lv.children):
                if isinstance(child, ReminderItem) and child.data.get("id") == item_id:
                    lv.index = i
                    self.active_reminder = child.data
                    self.query_one("#detail-view", DetailView).detail = child.data
                    return
            self.notify("Reminder not found — it may have changed", severity="warning")

        elif source == "calendar":
            for i, child in enumerate(lv.children):
                if isinstance(child, EventItem) and child.data.get("event_id") == item_id:
                    lv.index = i
                    self.active_event = child.data
                    self.query_one("#detail-view", DetailView).detail = child.data
                    return
            self.notify("Event not found — it may have changed", severity="warning")

    def action_toggle_ambient(self) -> None:
        """Toggle ambient listening on/off."""
        self._do_toggle_ambient()

    @work(thread=True, exit_on_error=False)
    def _do_toggle_ambient(self) -> None:
        try:
            status = self.client.ambient_status()
            if status.get("ambient"):
                self.client.ambient_stop()
                self.call_from_thread(
                    self.query_one("#status", Static).update,
                    "[yellow]Ambient listening stopped[/]",
                )
            else:
                self.client.ambient_start()
                self.call_from_thread(
                    self.query_one("#status", Static).update,
                    "[green]Ambient listening started[/]",
                )
        except Exception as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Ambient toggle', e)}[/]",
            )

    # ── Boot & refresh ───────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one("#status", Static).update("[dim]Starting server...[/]")
        self.boot()

    @work(thread=True, exit_on_error=False)
    def boot(self) -> None:
        # Load persisted favorites before connecting
        try:
            from services import load_favorites as _load_favs

            self._favorites = _load_favs()
        except Exception:
            pass
        try:
            self.client.ensure_server()
        except RuntimeError as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{e}[/]",
            )
            # Start polling even if server boot fails — polling will keep
            # trying so the TUI recovers automatically when the server
            # comes back.
            self.call_from_thread(self._start_polling)
            return
        self.call_from_thread(
            self.query_one("#status", Static).update,
            "[dim]Server ready — loading...[/]",
        )
        self._do_refresh()
        self.call_from_thread(self._start_polling)

    def _start_polling(self) -> None:
        """Start the auto-refresh timer."""
        self._poll_timer = self.set_interval(self.POLL_INTERVAL, self._poll_refresh)

    def _poll_refresh(self) -> None:
        """Background poll — only refreshes if data changed."""
        self._bg_poll()

    def action_refresh(self) -> None:
        # Manual refresh resets outage counters so the user's explicit
        # retry always tries fresh, regardless of prior poll failures.
        self._consecutive_errors = 0
        self._bg_refresh()

    @work(thread=True, exit_on_error=False)
    def _bg_refresh(self) -> None:
        try:
            self._do_refresh()
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Refresh', exc)}[/]",
            )

    @work(thread=True, exit_on_error=False)
    def _bg_poll(self) -> None:
        """Lightweight poll — updates data without disrupting UI if unchanged."""
        try:
            (
                convos,
                events,
                notes,
                reminders,
                reminder_lists,
                github_data,
                now_threads,
                actionable_threads,
                waiting_threads,
                status_override,
                changed,
            ) = self._collect_poll_data()
        except Exception as exc:
            # Unhandled exception in poll data collection — keep the TUI alive
            self._consecutive_errors += 1
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Auto-refresh', exc)}[/]",
            )
            return

        if status_override:
            self._consecutive_errors += 1
            self._poll_had_error = True
            if self._consecutive_errors >= self._SUSTAINED_OUTAGE_THRESHOLD:
                # Show persistent outage message that reminds user about retry
                status_override = "[red]Server unreachable — press Ctrl+R to retry[/]"
            if changed:
                self.call_from_thread(
                    self._populate,
                    convos,
                    events,
                    notes,
                    reminders,
                    reminder_lists,
                    github_data,
                    now_threads,
                    actionable_threads,
                    waiting_threads,
                    status_override,
                )
                return
            self.conversations = convos
            self.now_threads = now_threads
            self.actionable_threads = actionable_threads
            self.waiting_threads = waiting_threads
            self.call_from_thread(self.query_one("#status", Static).update, status_override)
            return

        # Success path — reset counters
        self._consecutive_errors = 0
        if changed:
            self._poll_had_error = False
            self.call_from_thread(
                self._populate,
                convos,
                events,
                notes,
                reminders,
                reminder_lists,
                github_data,
                now_threads,
                actionable_threads,
                waiting_threads,
                status_override,
            )
            return

        self.conversations = convos
        self.now_threads = now_threads
        self.actionable_threads = actionable_threads
        self.waiting_threads = waiting_threads
        if self._poll_had_error:
            self._poll_had_error = False
            self.call_from_thread(self._render_sidebar)

    def _do_refresh(self) -> None:
        """Fetch all data from the server (runs in worker thread)."""
        (
            convos,
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            status_override,
        ) = self._collect_refresh_data()
        self.call_from_thread(
            self._populate,
            convos,
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            status_override,
        )

    def _populate(
        self,
        convos: list[dict],
        events: list[dict],
        notes: list[dict],
        reminders: list[dict] | None = None,
        reminder_lists: list[dict] | None = None,
        github_data: list[dict] | None = None,
        now_threads: list[dict] | None = None,
        actionable_threads: list[dict] | None = None,
        waiting_threads: list[dict] | None = None,
        status_override: str | None = None,
    ) -> None:
        # Check for notification-worthy changes before updating state
        gh = github_data if github_data is not None else self.github_data
        ev = events if events is not None else self.events
        self._check_and_fire_notifications(convos, gh, ev)

        self.conversations = convos
        self.events = events
        self.notes_data = notes
        if reminders is not None:
            self.reminders_data = reminders
        if reminder_lists is not None:
            self.reminder_lists = reminder_lists
        if github_data is not None:
            self.github_data = github_data
            self._update_github_badge()
            # Clear active_notification if it's stale — either the data is
            # now empty or the previously selected notification is no longer
            # in the current list (e.g. marked-read on server, API refresh).
            if self.active_notification and not self._notification_still_exists(
                self.active_notification
            ):
                self.active_notification = None
                if self._active_filter == "github":
                    self.query_one("#detail-view", DetailView).detail = None
        if now_threads is not None:
            self.now_threads = now_threads
        if actionable_threads is not None:
            self.actionable_threads = actionable_threads
        if waiting_threads is not None:
            self.waiting_threads = waiting_threads
        self._update_bell_indicator()
        self._render_sidebar()
        if status_override:
            self.query_one("#status", Static).update(status_override)
        # Fire-and-forget triage for conversations with messages (non-blocking)
        if self.conversations:
            triage_convos = [
                c
                for c in self.conversations
                if c.get("source") in ("imessage", "gmail") and c.get("id")
            ][:20]  # cap to avoid overwhelming the model
            if triage_convos:
                self._do_triage_conversations(triage_convos)

    def _merge_status_errors(self, errors: list[str]) -> str | None:
        unique_errors = list(dict.fromkeys(errors))
        if not unique_errors:
            return None
        return f"[red]{' · '.join(unique_errors)}[/]"

    def _collect_auxiliary_data(
        self,
    ) -> tuple[
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[str],
    ]:
        events = self.events
        notes = self.notes_data
        reminders = self.reminders_data
        reminder_lists = self.reminder_lists
        github_data = self.github_data
        now_threads = self.now_threads
        actionable_threads = self.actionable_threads
        waiting_threads = self.waiting_threads
        errors: list[str] = []

        try:
            events = self._fetch_calendar_for_view()
        except Exception as exc:
            errors.append(_format_request_error("Calendar refresh", exc))

        try:
            notes = self.client.notes(limit=50)
        except Exception as exc:
            errors.append(_format_request_error("Notes refresh", exc))

        try:
            reminders = self.client.reminders(limit=100)
        except Exception as exc:
            errors.append(_format_request_error("Reminders refresh", exc))

        try:
            reminder_lists = self.client.reminder_lists()
        except Exception as exc:
            errors.append(_format_request_error("Reminder lists refresh", exc))

        try:
            github_data = self.client.github_notifications()
        except Exception as exc:
            errors.append(_format_request_error("GitHub refresh", exc))

        try:
            result = self.client.index_view("recent", limit=20)
            now_threads = result.get("threads", []) if isinstance(result, dict) else []
        except Exception as exc:
            errors.append(_format_request_error("Indexed recent refresh", exc))

        try:
            result = self.client.index_view("actionable", limit=20)
            actionable_threads = result.get("threads", []) if isinstance(result, dict) else []
        except Exception as exc:
            errors.append(_format_request_error("Indexed actionable refresh", exc))

        try:
            result = self.client.index_view("waiting-on", limit=20)
            waiting_threads = result.get("threads", []) if isinstance(result, dict) else []
        except Exception as exc:
            errors.append(_format_request_error("Indexed waiting refresh", exc))

        return (
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            errors,
        )

    def _collect_refresh_data(
        self,
    ) -> tuple[
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        str | None,
    ]:
        convos = self.conversations
        errors: list[str] = []

        try:
            convos = self.client.conversations(limit=100)
        except Exception as exc:
            errors.append(_format_request_error("Conversation refresh", exc))

        (
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            aux_errors,
        ) = self._collect_auxiliary_data()
        errors.extend(aux_errors)
        return (
            convos,
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            self._merge_status_errors(errors),
        )

    def _collect_poll_data(
        self,
    ) -> tuple[
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        str | None,
        bool,
    ]:
        try:
            convos = self.client.conversations(limit=100)
        except Exception as exc:
            return (
                self.conversations,
                self.events,
                self.notes_data,
                self.reminders_data,
                self.reminder_lists,
                self.github_data,
                self.now_threads,
                self.actionable_threads,
                self.waiting_threads,
                self._merge_status_errors([_format_request_error("Auto-refresh", exc)]),
                False,
            )

        # Check if unread counts changed
        old_unread = sum(c.get("unread", 0) for c in self.conversations)
        new_unread = sum(c.get("unread", 0) for c in convos)
        old_ids = {c.get("id") for c in self.conversations}
        new_ids = {c.get("id") for c in convos}

        changed = (
            old_unread != new_unread or old_ids != new_ids or len(self.conversations) != len(convos)
        )
        (
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            errors,
        ) = self._collect_auxiliary_data()
        if not changed:
            old_now_ids = [t.get("thread_id") for t in self.now_threads]
            new_now_ids = [t.get("thread_id") for t in now_threads]
            old_action_ids = [t.get("thread_id") for t in self.actionable_threads]
            new_action_ids = [t.get("thread_id") for t in actionable_threads]
            old_wait_ids = [t.get("thread_id") for t in self.waiting_threads]
            new_wait_ids = [t.get("thread_id") for t in waiting_threads]
            changed = (
                old_now_ids != new_now_ids
                or old_action_ids != new_action_ids
                or old_wait_ids != new_wait_ids
            )
        if not changed:
            return (
                convos,
                events,
                notes,
                reminders,
                reminder_lists,
                github_data,
                now_threads,
                actionable_threads,
                waiting_threads,
                self._merge_status_errors(errors),
                False,
            )

        return (
            convos,
            events,
            notes,
            reminders,
            reminder_lists,
            github_data,
            now_threads,
            actionable_threads,
            waiting_threads,
            self._merge_status_errors(errors),
            True,
        )

    # ── Reminder key handling ─────────────────────────────────────────────

    def on_key(self, event) -> None:
        """Handle single-key shortcuts for the reminders and github tabs.

        Only active when the compose input is NOT focused, so typing
        in compose still works normally.
        """
        compose = self.query_one("#compose", Input)
        if compose.has_focus:
            return

        key = event.key
        # Global: p = open contact profile for selected conversation
        if (
            key == "p"
            and self.active_conv
            and self._active_filter
            in (
                "imessage",
                "gmail",
            )
        ):
            event.prevent_default()
            self.action_show_contact_profile()
            return

        # Global: f = toggle favorite for selected conversation
        if (
            key == "f"
            and self.active_conv
            and self._active_filter
            in (
                "imessage",
                "gmail",
            )
        ):
            event.prevent_default()
            self.action_toggle_favorite()
            return

        if self._active_filter == "calendar":
            key = event.key
            if key == "right":
                event.prevent_default()
                self._calendar_navigate(1)
            elif key == "left":
                event.prevent_default()
                self._calendar_navigate(-1)
            elif key == "v":
                event.prevent_default()
                self._cycle_calendar_view()
            elif key == "g":
                event.prevent_default()
                self._enter_jump_to_date()
            elif key == "e":
                event.prevent_default()
                self._enter_edit_event()

        elif self._active_filter == "reminders":
            key = event.key
            if key == "c":
                event.prevent_default()
                self.action_complete_reminder()
            elif key == "e":
                event.prevent_default()
                self.action_edit_reminder()
            elif key == "d":
                event.prevent_default()
                self.action_delete_reminder()
            elif key == "f":
                event.prevent_default()
                self.action_filter_reminder_list()

        elif self._active_filter == "gmail":
            key = event.key
            if key == "a":
                event.prevent_default()
                self.action_gmail_archive()
            elif key == "d":
                event.prevent_default()
                self.action_gmail_delete()
            elif key == "s":
                event.prevent_default()
                self.action_gmail_toggle_star()
            elif key == "r":
                event.prevent_default()
                self.action_gmail_mark_read()
            elif key == "u":
                event.prevent_default()
                self.action_gmail_mark_unread()
            elif key == "c":
                event.prevent_default()
                self.action_gmail_compose()
            elif key == "l":
                event.prevent_default()
                self.action_gmail_cycle_label()
            elif key == "shift+d":
                event.prevent_default()
                self.action_gmail_download_attachment()

        elif self._active_filter == "github":
            key = event.key
            if key == "r":
                event.prevent_default()
                self.action_mark_notification_read()
            elif key == "shift+r":
                event.prevent_default()
                self.action_mark_all_notifications_read()
            elif key == "o":
                event.prevent_default()
                self.action_open_notification_url()

        elif self._active_filter == "drive":
            key = event.key
            if key == "d":
                event.prevent_default()
                self.action_drive_download()
            elif key == "u":
                event.prevent_default()
                self.action_drive_upload()
            elif key == "n":
                event.prevent_default()
                self.action_drive_new_folder()
            elif key == "x":
                event.prevent_default()
                self.action_drive_delete()
            elif key == "o":
                event.prevent_default()
                self.action_drive_open_url()
            elif key in ("backspace", "escape"):
                if self._drive_folder_id:
                    event.prevent_default()
                    self.action_drive_go_back()

    # ── Selection ────────────────────────────────────────────────────────

    @on(ListView.Selected, "#contact-list")
    def on_item_selected(self, event: ListView.Selected) -> None:
        item = event.item

        if isinstance(item, DriveItem):
            d = item.data
            mime = d.get("mime_type", "")
            # If it's a folder, navigate into it
            if mime == "application/vnd.google-apps.folder":
                self._drive_folder_stack.append(self._drive_folder_id)
                self._drive_folder_id = d.get("id", "")
                self.active_drive_file = None
                self.query_one("#detail-view", DetailView).detail = None
                self._load_drive_files()
                return
            self.active_drive_file = d
            self.active_event = None
            self.active_reminder = None
            self.active_notification = None
            self.active_conv = None
            self.query_one("#detail-view", DetailView).detail = d
            name = d.get("name", "?")
            acct = d.get("account", "")
            tag = f" · {acct.split(chr(64))[0]}" if acct else ""
            self.query_one("#status", Static).update(f"[bold]{name}[/]  [dim]drive{tag}[/]")
            return

        if isinstance(item, NotificationItem):
            self.active_notification = item.data
            self.active_event = None
            self.active_reminder = None
            self.active_conv = None
            self.query_one("#detail-view", DetailView).detail = item.data
            title = item.data.get("title", "?")
            repo = item.data.get("repo", "")
            tag = f" · {repo}" if repo else ""
            self.query_one("#status", Static).update(f"[bold]{title}[/]  [dim]github{tag}[/]")
            return

        if isinstance(item, ReminderItem):
            self.active_reminder = item.data
            self.active_event = None
            self.query_one("#detail-view", DetailView).detail = item.data
            title = item.data.get("title", "?")
            list_name = item.data.get("list_name", "")
            tag = f" · {list_name}" if list_name else ""
            self.query_one("#status", Static).update(f"[bold]{title}[/]  [dim]reminder{tag}[/]")
            return

        if isinstance(item, EventItem):
            self.active_event = item.data
            self.query_one("#detail-view", DetailView).detail = item.data
            return

        if isinstance(item, NoteItem):
            self.active_event = None
            self._load_note(item.data)
            return

        if isinstance(item, IndexedThreadItem):
            self.active_index_thread = item.data
            self.active_conv = None
            self.active_event = None
            self.active_reminder = None
            self.active_notification = None
            self._show_index_thread(item.data)
            return

        if isinstance(item, ConversationItem):
            self.active_conv = item.data
            self.active_index_thread = None
            self.active_event = None
            self._load_thread(item.data)

    @work(thread=True, exit_on_error=False)
    def _load_thread(self, conv: dict) -> None:
        try:
            msgs = self.client.messages(
                source=conv["source"],
                conv_id=conv["id"],
                thread_id=conv.get("thread_id", ""),
            )
        except Exception as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Thread load', e)}[/]",
            )
            return
        self.call_from_thread(self._show_thread, msgs, conv)

    def _show_thread(self, msgs: list[dict], conv: dict) -> None:
        mv = self.query_one("#messages", MessageView)
        mv.messages = msgs
        mv.ai_summary = None  # Clear previous summary
        mv.call_later(mv._scroll_to_bottom)
        name = conv.get("name", "?")
        source = conv.get("source", "")
        acct = conv.get("gmail_account", "")
        acct_tag = f" [{acct}]" if acct else ""
        if conv.get("is_group") and conv.get("members"):
            members = ", ".join(conv["members"][:5])
            if len(conv["members"]) > 5:
                members += f" +{len(conv['members']) - 5}"
            status = f"[bold]{name}[/]  [dim]Group · {members}[/]"
        else:
            status = f"[bold]{name}[/]  [dim]{source}{acct_tag}[/]"
        self.query_one("#status", Static).update(status)
        self.query_one("#compose", Input).focus()
        # Auto-summarize long Gmail threads
        if source == "gmail" and len(msgs) >= 5:
            thread_id = conv.get("thread_id") or conv.get("id", "")
            if thread_id in self._thread_summaries:
                mv.ai_summary = self._thread_summaries[thread_id]
            else:
                self._do_summarize_thread(conv, msgs)
        # Fire-and-forget action extraction on the latest message body
        if msgs:
            last_msg = msgs[-1]
            body = last_msg.get("body", "")
            if len(body) > 100:
                key = conv.get("id", "") or str(hash(body))
                self._do_extract_actions(body, key)

    def _show_index_thread(self, thread: dict) -> None:
        mv = self.query_one("#messages", MessageView)
        lines = []
        if thread.get("summary"):
            lines.append(str(thread["summary"]))
        if thread.get("action_items"):
            lines.append("Action items:")
            lines.extend(f"- {item}" for item in thread["action_items"][:5])
        if thread.get("brief"):
            lines.append("")
            lines.append(str(thread["brief"]))
        body = "\n".join(lines).strip() or thread.get("subject", "")
        mv.ai_summary = None
        mv.messages = [
            {
                "body": body,
                "ts": thread.get("last_message_at", ""),
                "sender": "Inbox Index",
                "is_me": False,
            }
        ]
        subject = thread.get("subject", "Untitled")
        workflow = thread.get("workflow", "")
        status = f"[bold]{subject}[/]  [dim]indexed"
        if workflow:
            status += f" · {workflow}"
        if thread.get("needs_reply"):
            status += " · needs reply"
        status += "[/]"
        self.query_one("#status", Static).update(status)

    @work(thread=True, exit_on_error=False)
    def _load_note(self, note_data: dict) -> None:
        status_override = None
        try:
            full = self.client.note(note_data["id"])
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Note load', exc)}[/]"
            full = note_data
        self.call_from_thread(self._show_note, full, status_override)

    def _show_note(self, note: dict, status_override: str | None = None) -> None:
        self.query_one("#detail-view", DetailView).detail = note
        if status_override:
            self.query_one("#status", Static).update(status_override)
            return
        title = note.get("title", "?")
        self.query_one("#status", Static).update(f"[bold]{title}[/]  [dim]note[/]")

    # ── Send / create ────────────────────────────────────────────────────

    @on(Input.Submitted, "#compose")
    def on_send(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.query_one("#compose", Input).clear()

        # Handle Gmail compose flow
        if self._gmail_compose_mode:
            self._handle_gmail_compose_submit(text)
            return

        if self._active_filter == "calendar":
            # Handle jump-to-date mode
            if self._jump_to_date_mode:
                self._jump_to_date_mode = False
                compose = self.query_one("#compose", Input)
                compose.placeholder = "New event: Title 2pm-3pm @ Location (Enter)"
                parsed = self._parse_user_date(text)
                if parsed:
                    self._calendar_date = parsed
                    self._refresh_calendar_events()
                else:
                    self.query_one("#status", Static).update(
                        f"[red]Invalid date: '{text}' — try YYYY-MM-DD or 'May 1'[/]"
                    )
                return
            # Handle event editing mode
            if self._editing_event is not None:
                edit_target = self._editing_event
                self._editing_event = None
                self._editing_event_field = ""
                compose = self.query_one("#compose", Input)
                compose.placeholder = "New event: Title 2pm-3pm @ Location (Enter)"
                self._do_update_event(edit_target, summary=text)
                return
            self._create_quick_event(text)
            return

        if self._active_filter == "reminders":
            # If we're in edit mode, save the edit instead of creating new
            if hasattr(self, "_editing_reminder") and self._editing_reminder is not None:
                self._do_edit_reminder(self._editing_reminder, new_title=text)
                self._editing_reminder = None
                return
            self._create_reminder(text)
            return

        if self._active_filter == "drive":
            if getattr(self, "_drive_upload_mode", False):
                self._drive_upload_mode = False
                compose = self.query_one("#compose", Input)
                compose.placeholder = "Search Drive files… (Enter)"
                self._do_drive_upload(text)
                return
            if getattr(self, "_drive_new_folder_mode", False):
                self._drive_new_folder_mode = False
                compose = self.query_one("#compose", Input)
                compose.placeholder = "Search Drive files… (Enter)"
                self._do_drive_create_folder(text)
                return
            self._search_drive(text)
            return

        if not self.active_conv:
            return

        # Optimistic UI
        mv = self.query_one("#messages", MessageView)
        optimistic = {
            "sender": "Me",
            "body": text,
            "ts": datetime.now().isoformat(),
            "is_me": True,
            "source": self.active_conv.get("source", ""),
        }
        mv.messages = [*mv.messages, optimistic]
        self._do_send(self.active_conv, text)

    @work(thread=True, exit_on_error=False)
    def _do_send(self, conv: dict, text: str) -> None:
        status = "[red]Failed to send[/]"
        try:
            ok = self.client.send(
                conv_id=conv["id"],
                source=conv["source"],
                text=text,
            )
        except Exception as exc:
            ok = False
            status = f"[red]{_format_request_error('Send', exc)}[/]"
        else:
            if ok:
                status = "[green]Sent[/]"
        self.call_from_thread(self.query_one("#status", Static).update, status)
        if ok and conv["source"] == "imessage":
            self._reload_after_send(conv, text)

    def _reload_after_send(self, conv: dict, sent_text: str) -> None:
        """Retry thread reload until the sent message appears in the DB."""

        for delay in (1.0, 2.0, 3.0):
            time.sleep(delay)
            try:
                msgs = self.client.messages(
                    source=conv["source"],
                    conv_id=conv["id"],
                    thread_id=conv.get("thread_id", ""),
                )
            except Exception:
                continue
            # Check if the DB has our sent message
            if any(
                m.get("is_me") and m.get("body", "").strip() == sent_text.strip() for m in msgs[-5:]
            ):
                self.call_from_thread(self._show_thread, msgs, conv)
                return

        # DB never caught up — keep the optimistic message visible,
        # just update status so the user knows
        self.call_from_thread(
            self.query_one("#status", Static).update,
            "[green]Sent[/] [dim](DB sync pending)[/]",
        )

    @work(thread=True, exit_on_error=False)
    def _create_quick_event(self, text: str) -> None:
        status_override = None
        try:
            result = self.client.create_quick_event(text)
            ok = result.get("ok", False)
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Event creation', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Event created[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to create event[/]",
            )

    # ── Calendar actions ─────────────────────────────────────────────────

    def action_new_event(self) -> None:
        if self._active_filter != "calendar":
            self.query_one("#tabs", Tabs).active = "tab-cal"
        self.query_one("#compose", Input).focus()

    def action_delete_event(self) -> None:
        if not self.active_event or not self.active_event.get("event_id"):
            self.query_one("#status", Static).update("[yellow]No event selected[/]")
            return
        self.query_one("#status", Static).update(
            f"[yellow]Deleting '{self.active_event.get('summary')}'...[/]"
        )
        self._do_delete_event(self.active_event)

    @work(thread=True, exit_on_error=False)
    def _do_delete_event(self, event: dict) -> None:
        status_override = None
        try:
            ok = self.client.delete_event(
                event_id=event["event_id"],
                calendar_id=event.get("calendar_id", "primary"),
                account=event.get("account", ""),
            )
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Delete event', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Deleted '{event.get('summary')}'[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to delete[/]",
            )

    # ── Calendar navigation & views ─────────────────────────────────────

    def _calendar_date_label(self) -> str:
        """Format the calendar date for the status bar."""
        d = self._calendar_date
        label = d.strftime("%a, %b %d, %Y")
        if d == date.today():
            label = f"Today · {label}"
        return label

    def _calendar_navigate(self, delta: int) -> None:
        """Navigate calendar by delta days (or weeks in week mode)."""
        if self._calendar_view_mode == "week":
            self._calendar_date += timedelta(days=7 * delta)
        else:
            self._calendar_date += timedelta(days=delta)
        self._refresh_calendar_events()

    def _cycle_calendar_view(self) -> None:
        """Cycle day → week → agenda → day."""
        modes = ["day", "week", "agenda"]
        idx = modes.index(self._calendar_view_mode)
        self._calendar_view_mode = modes[(idx + 1) % len(modes)]
        self._refresh_calendar_events()

    def _enter_jump_to_date(self) -> None:
        """Activate jump-to-date input mode."""
        if self._active_filter != "calendar":
            return
        self._jump_to_date_mode = True
        compose = self.query_one("#compose", Input)
        compose.placeholder = "Go to date: YYYY-MM-DD or 'May 1' (Enter to go, Esc to cancel)"
        compose.clear()
        compose.focus()
        self.query_one("#status", Static).update("[yellow]Type a date and press Enter[/]")

    def action_jump_to_date(self) -> None:
        """Ctrl+G handler for jump-to-date."""
        if self._active_filter != "calendar":
            return
        self._enter_jump_to_date()

    def _parse_user_date(self, text: str) -> date | None:
        """Try to parse a user-entered date string into a date object."""
        text = text.strip()
        if not text:
            return None
        # Try ISO format first: YYYY-MM-DD
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
        # Try common formats
        for fmt in ("%B %d", "%b %d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=date.today().year)
                return parsed.date()
            except ValueError:
                continue
        return None

    def _enter_edit_event(self) -> None:
        """Start editing the selected calendar event."""
        if self._active_filter != "calendar":
            return
        if not self.active_event or not self.active_event.get("event_id"):
            self.query_one("#status", Static).update("[yellow]No event selected[/]")
            return
        self._editing_event = self.active_event
        self._editing_event_field = "summary"
        compose = self.query_one("#compose", Input)
        compose.value = self.active_event.get("summary", "")
        compose.placeholder = "Edit title (Enter to save, Esc to cancel)"
        compose.focus()
        self.query_one("#status", Static).update(
            "[yellow]Editing event title — Enter to save, Esc to cancel[/]"
        )

    @work(thread=True, exit_on_error=False)
    def _do_update_event(self, event: dict, **fields: str | None) -> None:
        status_override = None
        try:
            ok = self.client.update_event(
                event_id=event["event_id"],
                calendar_id=event.get("calendar_id", "primary"),
                account=event.get("account", ""),
                **fields,
            )
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Update event', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Event updated[/]",
            )
            self._do_refresh_calendar()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to update event[/]",
            )

    @work(thread=True, exit_on_error=False)
    def _refresh_calendar_events(self) -> None:
        """Fetch events for the current calendar view and update sidebar."""
        try:
            events = self._fetch_calendar_for_view()
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Calendar refresh', exc)}[/]",
            )
            return
        self.call_from_thread(self._apply_calendar_events, events)

    def _fetch_calendar_for_view(self) -> list[dict]:
        """Fetch events from the server based on current view mode."""
        if self._calendar_view_mode == "day":
            return self.client.calendar_events(date=self._calendar_date.isoformat())
        elif self._calendar_view_mode == "week":
            # Compute Monday of the current week
            weekday = self._calendar_date.weekday()  # 0=Monday
            monday = self._calendar_date - timedelta(days=weekday)
            sunday = monday + timedelta(days=6)
            return self.client.calendar_events_range(monday.isoformat(), sunday.isoformat())
        else:  # agenda
            end_d = self._calendar_date + timedelta(days=13)
            return self.client.calendar_events_range(
                self._calendar_date.isoformat(), end_d.isoformat()
            )

    def _apply_calendar_events(self, events: list[dict]) -> None:
        """Apply fetched calendar events to the UI."""
        self.events = events
        self._render_sidebar()

    @work(thread=True, exit_on_error=False)
    def _do_refresh_calendar(self) -> None:
        """Refresh calendar data and full sidebar from worker thread."""
        try:
            events = self._fetch_calendar_for_view()
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Calendar refresh', exc)}[/]",
            )
            return
        self.call_from_thread(self._apply_calendar_events, events)

    # ── Reminder actions ────────────────────────────────────────────────

    @work(thread=True, exit_on_error=False)
    def _create_reminder(self, text: str) -> None:
        status_override = None
        try:
            list_name = self._rem_list_filter or "Reminders"
            ok = self.client.reminder_create(title=text, list_name=list_name)
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Reminder creation', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Reminder created[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to create reminder[/]",
            )

    def action_complete_reminder(self) -> None:
        if self._active_filter != "reminders":
            return
        if not self.active_reminder or not self.active_reminder.get("id"):
            self.query_one("#status", Static).update("[yellow]No reminder selected[/]")
            return
        title = self.active_reminder.get("title", "?")
        self.query_one("#status", Static).update(f"[yellow]Completing '{title}'...[/]")
        self._do_complete_reminder(self.active_reminder)

    @work(thread=True, exit_on_error=False)
    def _do_complete_reminder(self, reminder: dict) -> None:
        status_override = None
        try:
            ok = self.client.reminder_complete(reminder_id=reminder["id"])
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Complete reminder', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Completed '{reminder.get('title')}'[/]",
            )
            self.active_reminder = None
            self.query_one("#detail-view", DetailView).detail = None
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to complete reminder[/]",
            )

    def action_edit_reminder(self) -> None:
        if self._active_filter != "reminders":
            return
        if not self.active_reminder or not self.active_reminder.get("id"):
            self.query_one("#status", Static).update("[yellow]No reminder selected[/]")
            return
        # Focus the compose input with current title as value for editing
        compose = self.query_one("#compose", Input)
        compose.value = self.active_reminder.get("title", "")
        compose.focus()
        self.query_one("#status", Static).update(
            "[yellow]Edit title in compose — Enter to save, Escape to cancel[/]"
        )
        self._editing_reminder = self.active_reminder

    def action_delete_reminder(self) -> None:
        if self._active_filter != "reminders":
            return
        if not self.active_reminder or not self.active_reminder.get("id"):
            self.query_one("#status", Static).update("[yellow]No reminder selected[/]")
            return
        title = self.active_reminder.get("title", "?")
        self.query_one("#status", Static).update(f"[yellow]Deleting '{title}'...[/]")
        self._do_delete_reminder(self.active_reminder)

    @work(thread=True, exit_on_error=False)
    def _do_delete_reminder(self, reminder: dict) -> None:
        status_override = None
        try:
            ok = self.client.reminder_delete(reminder_id=reminder["id"])
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Delete reminder', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Deleted '{reminder.get('title')}'[/]",
            )
            self.active_reminder = None
            self.query_one("#detail-view", DetailView).detail = None
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to delete reminder[/]",
            )

    @work(thread=True, exit_on_error=False)
    def _do_edit_reminder(self, reminder: dict, new_title: str) -> None:
        status_override = None
        try:
            ok = self.client.reminder_edit(reminder_id=reminder["id"], title=new_title)
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Edit reminder', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Updated '{new_title}'[/]",
            )
            self.active_reminder = None
            self.query_one("#detail-view", DetailView).detail = None
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to edit reminder[/]",
            )

    def action_filter_reminder_list(self) -> None:
        """Cycle through reminder lists as a filter, or show all."""
        if self._active_filter != "reminders":
            return
        if not self.reminder_lists:
            return
        list_names = [rl.get("name", "") for rl in self.reminder_lists if rl.get("name")]
        if not list_names:
            return
        if self._rem_list_filter == "":
            # No filter → first list
            self._rem_list_filter = list_names[0]
        else:
            # Find current index and advance
            try:
                idx = list_names.index(self._rem_list_filter)
                if idx + 1 < len(list_names):
                    self._rem_list_filter = list_names[idx + 1]
                else:
                    # Wrap around: back to all
                    self._rem_list_filter = ""
            except ValueError:
                self._rem_list_filter = list_names[0]
        # Clear active_reminder and detail view — the selected reminder may
        # no longer be visible after the filter change, and actions on a
        # stale reminder could target the wrong item.
        self.active_reminder = None
        self.query_one("#detail-view", DetailView).detail = None
        self._render_sidebar()

    # ── GitHub actions ────────────────────────────────────────────────────

    def action_mark_notification_read(self) -> None:
        """Mark the selected notification as read."""
        if self._active_filter != "github":
            return
        if not self.active_notification or not self.active_notification.get("id"):
            self.query_one("#status", Static).update("[yellow]No notification selected[/]")
            return
        # Guard against stale selection — the notification may have been
        # removed by a concurrent refresh or mark-all-read.
        if not self._notification_still_exists(self.active_notification):
            self.active_notification = None
            self.query_one("#detail-view", DetailView).detail = None
            self.query_one("#status", Static).update("[yellow]Notification no longer available[/]")
            return
        title = self.active_notification.get("title", "?")
        self.query_one("#status", Static).update(f"[yellow]Marking '{title}' as read...[/]")
        self._do_mark_notification_read(self.active_notification)

    @work(thread=True, exit_on_error=False)
    def _do_mark_notification_read(self, notification: dict) -> None:
        # Re-check in case data refreshed between action handler and worker start
        if not self._notification_still_exists(notification):
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[yellow]Notification no longer available[/]",
            )
            return
        status_override = None
        try:
            ok = self.client.github_mark_read(notification_id=notification["id"])
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Mark read', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Marked '{notification.get('title')}' as read[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to mark as read[/]",
            )

    def action_mark_all_notifications_read(self) -> None:
        """Mark all GitHub notifications as read."""
        if self._active_filter != "github":
            return
        count = sum(1 for n in self.github_data if n.get("unread"))
        if not count:
            self.query_one("#status", Static).update("[dim]All notifications already read[/]")
            return
        self.query_one("#status", Static).update(
            f"[yellow]Marking {count} notifications as read...[/]"
        )
        self._do_mark_all_notifications_read()

    @work(thread=True, exit_on_error=False)
    def _do_mark_all_notifications_read(self) -> None:
        status_override = None
        try:
            ok = self.client.github_mark_all_read()
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Mark all read', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]All notifications marked as read[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to mark all as read[/]",
            )

    def action_open_notification_url(self) -> None:
        """Open the selected notification's URL in the system browser."""
        if self._active_filter != "github":
            return
        if not self.active_notification:
            self.query_one("#status", Static).update("[yellow]No notification selected[/]")
            return
        # Guard against stale selection — the notification may have been
        # removed by a concurrent refresh or mark-all-read.
        if not self._notification_still_exists(self.active_notification):
            self.active_notification = None
            self.query_one("#detail-view", DetailView).detail = None
            self.query_one("#status", Static).update("[yellow]Notification no longer available[/]")
            return
        url = self.active_notification.get("url", "")
        if not url:
            self.query_one("#status", Static).update("[yellow]No URL for this notification[/]")
            return
        webbrowser.open(url)
        self.query_one("#status", Static).update(
            f"[green]Opened {self.active_notification.get('title', 'notification')} in browser[/]"
        )

    # ── Drive actions ────────────────────────────────────────────────────

    @work(thread=True, exit_on_error=False)
    def _load_drive_files(self, query: str = "") -> None:
        """Fetch drive files for the current folder or search query."""
        try:
            files = self.client.drive_files(
                query=query,
                folder_id=self._drive_folder_id,
            )
        except Exception as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Drive load', e)}[/]",
            )
            return
        self.call_from_thread(self._show_drive_files, files)

    def _show_drive_files(self, files: list[dict]) -> None:
        self.drive_data = files
        self._render_sidebar()

    @work(thread=True, exit_on_error=False)
    def _search_drive(self, query: str) -> None:
        """Search Drive files by name query."""
        try:
            files = self.client.drive_files(
                query=query,
                folder_id=self._drive_folder_id,
            )
        except Exception as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Drive search', e)}[/]",
            )
            return
        self.call_from_thread(self._show_drive_files, files)

    def action_drive_go_back(self) -> None:
        """Navigate to parent folder."""
        if self._drive_folder_stack:
            self._drive_folder_id = self._drive_folder_stack.pop()
        else:
            self._drive_folder_id = ""
        self.active_drive_file = None
        self.query_one("#detail-view", DetailView).detail = None
        self._load_drive_files()

    def action_drive_download(self) -> None:
        """Download the selected Drive file to ~/Downloads/."""
        if self._active_filter != "drive":
            return
        if not self.active_drive_file or not self.active_drive_file.get("id"):
            self.query_one("#status", Static).update("[yellow]No file selected[/]")
            return
        name = self.active_drive_file.get("name", "file")
        self.query_one("#status", Static).update(f"[yellow]Downloading '{name}'...[/]")
        self._do_drive_download(self.active_drive_file)

    @work(thread=True, exit_on_error=False)
    def _do_drive_download(self, file_data: dict) -> None:
        from pathlib import Path as P

        try:
            content = self.client.drive_download(
                file_id=file_data["id"],
                account=file_data.get("account", ""),
            )
            downloads = P.home() / "Downloads"
            downloads.mkdir(exist_ok=True)
            dest = downloads / file_data.get("name", "download")
            dest.write_bytes(content)
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Downloaded to {dest}[/]",
            )
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Download', exc)}[/]",
            )

    def action_drive_upload(self) -> None:
        """Upload a file to the current Drive folder."""
        if self._active_filter != "drive":
            return
        compose = self.query_one("#compose", Input)
        compose.placeholder = "Enter file path to upload (Enter to confirm)"
        compose.focus()
        self._drive_upload_mode = True
        self.query_one("#status", Static).update(
            "[yellow]Type file path and press Enter to upload, Escape to cancel[/]"
        )

    def action_drive_new_folder(self) -> None:
        """Create a new folder in the current Drive folder."""
        if self._active_filter != "drive":
            return
        compose = self.query_one("#compose", Input)
        compose.placeholder = "Enter folder name (Enter to create)"
        compose.focus()
        self._drive_new_folder_mode = True
        self.query_one("#status", Static).update(
            "[yellow]Type folder name and press Enter, Escape to cancel[/]"
        )

    def action_drive_delete(self) -> None:
        """Delete (trash) the selected Drive file."""
        if self._active_filter != "drive":
            return
        if not self.active_drive_file or not self.active_drive_file.get("id"):
            self.query_one("#status", Static).update("[yellow]No file selected[/]")
            return
        name = self.active_drive_file.get("name", "?")
        self.query_one("#status", Static).update(f"[yellow]Trashing '{name}'...[/]")
        self._do_drive_delete(self.active_drive_file)

    @work(thread=True, exit_on_error=False)
    def _do_drive_delete(self, file_data: dict) -> None:
        status_override = None
        try:
            ok = self.client.drive_delete(
                file_id=file_data["id"],
                account=file_data.get("account", ""),
            )
        except Exception as exc:
            status_override = f"[red]{_format_request_error('Delete', exc)}[/]"
            ok = False

        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Trashed '{file_data.get('name')}'[/]",
            )
            self.call_from_thread(self._clear_drive_selection)
            self._load_drive_files()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                status_override or "[red]Failed to delete[/]",
            )

    def action_drive_open_url(self) -> None:
        """Open the selected Drive file's web link in the browser."""
        if self._active_filter != "drive":
            return
        if not self.active_drive_file:
            self.query_one("#status", Static).update("[yellow]No file selected[/]")
            return
        url = self.active_drive_file.get("web_link", "")
        if not url:
            self.query_one("#status", Static).update("[yellow]No URL for this file[/]")
            return
        webbrowser.open(url)
        self.query_one("#status", Static).update(
            f"[green]Opened {self.active_drive_file.get('name', 'file')} in browser[/]"
        )

    def _clear_drive_selection(self) -> None:
        self.active_drive_file = None
        self.query_one("#detail-view", DetailView).detail = None

    @work(thread=True, exit_on_error=False)
    def _do_drive_upload(self, file_path: str) -> None:
        try:
            result = self.client.drive_upload(
                file_path=file_path,
                folder_id=self._drive_folder_id,
            )
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Uploaded '{result.get('name', file_path)}'[/]",
            )
            self._load_drive_files()
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Upload', exc)}[/]",
            )

    @work(thread=True, exit_on_error=False)
    def _do_drive_create_folder(self, name: str) -> None:
        try:
            result = self.client.drive_create_folder(
                name=name,
                parent_id=self._drive_folder_id,
            )
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Created folder '{result.get('name', name)}'[/]",
            )
            self._load_drive_files()
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Create folder', exc)}[/]",
            )

    # ── Gmail actions ────────────────────────────────────────────────────

    def _get_active_gmail_conv(self) -> dict | None:
        """Get the currently selected Gmail conversation, or None."""
        if not self.active_conv:
            return None
        if self.active_conv.get("source") != "gmail":
            return None
        return self.active_conv

    def action_gmail_archive(self) -> None:
        conv = self._get_active_gmail_conv()
        if not conv:
            self.query_one("#status", Static).update("[yellow]No Gmail email selected[/]")
            return
        self.query_one("#status", Static).update("[yellow]Archiving...[/]")
        self._do_gmail_archive(conv)

    @work(thread=True, exit_on_error=False)
    def _do_gmail_archive(self, conv: dict) -> None:
        try:
            ok = self.client.gmail_archive(conv["id"])
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Archive', exc)}[/]",
            )
            return
        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Archived[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Failed to archive[/]",
            )

    def action_gmail_delete(self) -> None:
        conv = self._get_active_gmail_conv()
        if not conv:
            self.query_one("#status", Static).update("[yellow]No Gmail email selected[/]")
            return
        self.query_one("#status", Static).update("[yellow]Deleting...[/]")
        self._do_gmail_delete(conv)

    @work(thread=True, exit_on_error=False)
    def _do_gmail_delete(self, conv: dict) -> None:
        try:
            ok = self.client.gmail_delete(conv["id"])
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Delete', exc)}[/]",
            )
            return
        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Deleted[/]",
            )
            self._do_refresh()
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Failed to delete[/]",
            )

    def action_gmail_toggle_star(self) -> None:
        conv = self._get_active_gmail_conv()
        if not conv:
            self.query_one("#status", Static).update("[yellow]No Gmail email selected[/]")
            return
        msg_id = conv["id"]
        is_starred = msg_id in self._gmail_starred
        if is_starred:
            self.query_one("#status", Static).update("[yellow]Unstarring...[/]")
        else:
            self.query_one("#status", Static).update("[yellow]Starring...[/]")
        self._do_gmail_toggle_star(conv, is_starred)

    @work(thread=True, exit_on_error=False)
    def _do_gmail_toggle_star(self, conv: dict, was_starred: bool) -> None:
        try:
            if was_starred:
                ok = self.client.gmail_unstar(conv["id"])
            else:
                ok = self.client.gmail_star(conv["id"])
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Star toggle', exc)}[/]",
            )
            return
        if ok:
            msg_id = conv["id"]
            if was_starred:
                self._gmail_starred.discard(msg_id)
                label = "Unstarred"
            else:
                self._gmail_starred.add(msg_id)
                label = "Starred ★"
            # Update the conversation data for immediate UI feedback
            conv["_starred"] = not was_starred
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]{label}[/]",
            )
            self.call_from_thread(self._render_sidebar)
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Failed to toggle star[/]",
            )

    def action_gmail_mark_read(self) -> None:
        conv = self._get_active_gmail_conv()
        if not conv:
            self.query_one("#status", Static).update("[yellow]No Gmail email selected[/]")
            return
        self.query_one("#status", Static).update("[yellow]Marking as read...[/]")
        self._do_gmail_mark_read(conv)

    @work(thread=True, exit_on_error=False)
    def _do_gmail_mark_read(self, conv: dict) -> None:
        try:
            ok = self.client.gmail_mark_read(conv["id"])
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Mark read', exc)}[/]",
            )
            return
        if ok:
            conv["unread"] = 0
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Marked as read[/]",
            )
            self.call_from_thread(self._render_sidebar)
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Failed to mark as read[/]",
            )

    def action_gmail_mark_unread(self) -> None:
        conv = self._get_active_gmail_conv()
        if not conv:
            self.query_one("#status", Static).update("[yellow]No Gmail email selected[/]")
            return
        self.query_one("#status", Static).update("[yellow]Marking as unread...[/]")
        self._do_gmail_mark_unread(conv)

    @work(thread=True, exit_on_error=False)
    def _do_gmail_mark_unread(self, conv: dict) -> None:
        try:
            ok = self.client.gmail_mark_unread(conv["id"])
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Mark unread', exc)}[/]",
            )
            return
        if ok:
            conv["unread"] = 1
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[green]Marked as unread[/]",
            )
            self.call_from_thread(self._render_sidebar)
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Failed to mark as unread[/]",
            )

    def action_gmail_compose(self) -> None:
        """Start the multi-step compose flow for a new email."""
        self._gmail_compose_mode = "to"
        self._gmail_compose_to = ""
        self._gmail_compose_subject = ""
        compose = self.query_one("#compose", Input)
        compose.placeholder = "To: (email address)"
        compose.clear()
        compose.focus()
        self.query_one("#status", Static).update(
            "[cyan]Compose: Enter recipient email (Escape to cancel)[/]"
        )

    def _handle_gmail_compose_submit(self, text: str) -> None:
        """Handle compose flow steps: to -> subject -> body -> send."""
        compose = self.query_one("#compose", Input)

        if self._gmail_compose_mode == "to":
            if not text or "@" not in text:
                self.query_one("#status", Static).update("[red]Invalid email — must contain @[/]")
                return
            self._gmail_compose_to = text
            self._gmail_compose_mode = "subject"
            compose.placeholder = "Subject:"
            compose.clear()
            self.query_one("#status", Static).update(f"[cyan]To: {text} — Enter subject[/]")
        elif self._gmail_compose_mode == "subject":
            self._gmail_compose_subject = text
            self._gmail_compose_mode = "body"
            compose.placeholder = "Body: (Enter to send)"
            compose.clear()
            self.query_one("#status", Static).update(
                f"[cyan]To: {self._gmail_compose_to} · Subject: {text} — Enter body[/]"
            )
        elif self._gmail_compose_mode == "body":
            self._gmail_compose_mode = ""
            compose.placeholder = "Reply… (Enter to send)"
            compose.clear()
            self.query_one("#status", Static).update("[yellow]Sending...[/]")
            self._do_gmail_compose_send(self._gmail_compose_to, self._gmail_compose_subject, text)

    @work(thread=True, exit_on_error=False)
    def _do_gmail_compose_send(self, to: str, subject: str, body: str) -> None:
        try:
            ok = self.client.gmail_compose(to, subject, body)
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Compose send', exc)}[/]",
            )
            return
        if ok:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Sent to {to}[/]",
            )
        else:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Failed to send email[/]",
            )

    def action_gmail_cycle_label(self) -> None:
        """Cycle through Gmail labels to filter the conversation list."""
        self._do_gmail_cycle_label()

    @work(thread=True, exit_on_error=False)
    def _do_gmail_cycle_label(self) -> None:
        # Fetch labels if we haven't yet
        if not self._gmail_labels:
            try:
                self._gmail_labels = self.client.gmail_labels()
            except Exception:
                self._gmail_labels = []

        # Build label cycle: common labels + custom user labels
        common = ["INBOX", "SENT", "STARRED", "DRAFT"]
        custom = [
            lbl["id"] for lbl in self._gmail_labels if lbl.get("type") == "user" and lbl.get("id")
        ]
        cycle = common + custom

        # Find current and advance
        try:
            idx = cycle.index(self._gmail_label_filter)
            self._gmail_label_filter = cycle[(idx + 1) % len(cycle)]
        except ValueError:
            self._gmail_label_filter = cycle[0]

        label_name = self._gmail_label_filter
        # Resolve label name for display
        for lbl in self._gmail_labels:
            if lbl.get("id") == self._gmail_label_filter:
                label_name = lbl.get("name", label_name)
                break

        # Fetch conversations for this label
        try:
            convos = self.client.gmail_conversations_by_label(
                label=self._gmail_label_filter, limit=50
            )
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Label filter', exc)}[/]",
            )
            return

        # Update conversations with label-filtered results
        # Remove existing gmail convos and add the label-filtered ones
        non_gmail = [c for c in self.conversations if c.get("source") != "gmail"]
        self.conversations = non_gmail + convos
        self.call_from_thread(self._render_sidebar)
        self.call_from_thread(
            self.query_one("#status", Static).update,
            f"[cyan]Gmail label: {label_name}[/]  [dim]l: next label[/]",
        )

    def action_gmail_download_attachment(self) -> None:
        """Download the first attachment from the currently viewed thread."""
        mv = self.query_one("#messages", MessageView)
        if not mv.messages:
            self.query_one("#status", Static).update("[yellow]No messages loaded[/]")
            return
        # Find the first message with attachments
        for msg in mv.messages:
            atts = msg.get("attachments", [])
            if atts:
                att = atts[0]
                self.query_one("#status", Static).update(
                    f"[yellow]Downloading {att.get('filename', 'file')}...[/]"
                )
                self._do_gmail_download_attachment(att)
                return
        self.query_one("#status", Static).update("[yellow]No attachments in this thread[/]")

    @work(thread=True, exit_on_error=False)
    def _do_gmail_download_attachment(self, att: dict) -> None:
        import base64
        from pathlib import Path

        try:
            result = self.client.gmail_attachment(
                att.get("messageId", ""), att.get("attachmentId", "")
            )
            data_b64 = result.get("data", "")
            if not data_b64:
                self.call_from_thread(
                    self.query_one("#status", Static).update,
                    "[red]Empty attachment data[/]",
                )
                return
            raw = base64.urlsafe_b64decode(data_b64)
            filename = att.get("filename", "download")
            dest = Path.home() / "Downloads" / filename
            dest.write_bytes(raw)
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Downloaded to ~/Downloads/{filename}[/]",
            )
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Download', exc)}[/]",
            )

    # ── Account actions ──────────────────────────────────────────────────

    def action_add_account(self) -> None:
        self.query_one("#status", Static).update("[yellow]Opening browser for auth...[/]")
        self._do_add_account()

    @work(thread=True, exit_on_error=False)
    def _do_add_account(self) -> None:
        try:
            result = self.client.add_account()
            email = result.get("email", "")
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Added {email} — refreshing...[/]",
            )
            self._do_refresh()
        except Exception as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Auth', e)}[/]",
            )

    def action_reauth_account(self) -> None:
        email = ""
        if self.active_conv and self.active_conv.get("gmail_account"):
            email = self.active_conv["gmail_account"]
        if not email:
            try:
                accts = self.client.accounts()
                gmail_accts = accts.get("gmail", [])
                if gmail_accts:
                    email = gmail_accts[0]
            except Exception:
                pass
        if not email:
            self.query_one("#status", Static).update(
                "[yellow]No account to re-auth — ctrl+a to add[/]"
            )
            return
        self.query_one("#status", Static).update(f"[yellow]Re-authing {email}...[/]")
        self._do_reauth(email)

    @work(thread=True, exit_on_error=False)
    def _do_reauth(self, email: str) -> None:
        try:
            result = self.client.reauth_account(email)
            new_email = result.get("email", email)
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[green]Re-authed {new_email} — refreshing...[/]",
            )
            self._do_refresh()
        except Exception as e:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Re-auth', e)}[/]",
            )

    # ── Misc ─────────────────────────────────────────────────────────────

    def _update_status_from_thread(self, message: str) -> None:
        """Safely update the status bar from a worker thread."""
        self.call_from_thread(self.query_one("#status", Static).update, message)

    def action_clear_compose(self) -> None:
        self.query_one("#compose", Input).clear()
        # Cancel Gmail compose flow
        if self._gmail_compose_mode:
            self._gmail_compose_mode = ""
            self._gmail_compose_to = ""
            self._gmail_compose_subject = ""
            compose = self.query_one("#compose", Input)
            compose.placeholder = "Reply… (Enter to send)"
            self.query_one("#status", Static).update("[dim]Compose cancelled[/]")
        # Cancel any in-progress reminder edit
        if hasattr(self, "_editing_reminder") and self._editing_reminder is not None:
            self._editing_reminder = None
            if self._active_filter == "reminders":
                self.query_one("#status", Static).update("[dim]Edit cancelled[/]")
        # Cancel drive upload/folder modes
        if getattr(self, "_drive_upload_mode", False):
            self._drive_upload_mode = False
            if self._active_filter == "drive":
                self.query_one("#compose", Input).placeholder = "Search Drive files… (Enter)"
                self.query_one("#status", Static).update("[dim]Upload cancelled[/]")
        if getattr(self, "_drive_new_folder_mode", False):
            self._drive_new_folder_mode = False
            if self._active_filter == "drive":
                self.query_one("#compose", Input).placeholder = "Search Drive files… (Enter)"
                self.query_one("#status", Static).update("[dim]Folder creation cancelled[/]")
        # Cancel calendar editing/jump-to-date modes
        if self._active_filter == "calendar":
            if self._jump_to_date_mode:
                self._jump_to_date_mode = False
                compose = self.query_one("#compose", Input)
                compose.placeholder = "New event: Title 2pm-3pm @ Location (Enter)"
                self.query_one("#status", Static).update("[dim]Jump cancelled[/]")
            if self._editing_event is not None:
                self._editing_event = None
                self._editing_event_field = ""
                compose = self.query_one("#compose", Input)
                compose.placeholder = "New event: Title 2pm-3pm @ Location (Enter)"
                self.query_one("#status", Static).update("[dim]Edit cancelled[/]")

    # ── Contact profile / favorites ──────────────────────────────────────

    def action_show_contact_profile(self) -> None:
        if not self.active_conv:
            return
        self._bg_load_profile(self.active_conv)

    @work(thread=True, exit_on_error=False)
    def _bg_load_profile(self, conv: dict) -> None:
        contact_id = conv.get("reply_to") or conv.get("name", "")
        if not contact_id:
            return
        try:
            profile = self.client.contacts_profile(contact_id)
        except Exception:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Profile load failed[/]",
            )
            return
        self.call_from_thread(self._open_profile_screen, profile)

    def _open_profile_screen(self, profile: dict) -> None:
        self.push_screen(ContactProfileScreen(profile))

    def action_toggle_favorite(self) -> None:
        if not self.active_conv:
            return
        contact_id = (self.active_conv.get("reply_to") or self.active_conv.get("name", "")).lower()
        if not contact_id:
            return
        if contact_id in self._favorites:
            self._favorites.discard(contact_id)
            msg = f"[dim]Removed {contact_id} from favorites[/]"
        else:
            self._favorites.add(contact_id)
            msg = f"[yellow]⭐ Added {contact_id} to favorites[/]"
        try:
            from services import save_favorites as _save_favs

            _save_favs(self._favorites)
        except Exception:
            pass
        self.query_one("#status", Static).update(msg)
        self._render_sidebar()

    # ── AI actions ───────────────────────────────────────────────────────

    def action_morning_briefing(self) -> None:
        """Fetch and show the morning briefing modal."""
        self.query_one("#status", Static).update("[yellow]Loading briefing...[/]")
        self._do_fetch_briefing()

    def action_ask_assistant(self) -> None:
        """Prompt for a readonly assistant query against the current Inbox context."""
        self.push_screen(AssistantPromptModal(), self._on_assistant_prompt)

    def _on_assistant_prompt(self, query: str | None) -> None:
        if not query:
            return
        self.query_one("#status", Static).update("[yellow]Running Inbox assistant...[/]")
        self._do_run_readonly_assistant(query, self._assistant_context())

    def _assistant_context(self) -> str:
        parts = [f"Active tab: {self._active_filter}"]
        selected: dict | None = None

        if self._active_filter in ("all", "imessage", "gmail"):
            selected = self.active_conv
        elif self._active_filter == "calendar":
            selected = self.active_event
        elif self._active_filter == "reminders":
            selected = self.active_reminder
        elif self._active_filter == "github":
            selected = self.active_notification
        elif self._active_filter == "drive":
            selected = self.active_drive_file

        if not selected:
            return "\n".join(parts)

        summary_lines = []
        for key in (
            "source",
            "name",
            "id",
            "thread_id",
            "snippet",
            "summary",
            "title",
            "body",
            "location",
            "start",
            "end",
            "gmail_account",
            "email",
            "url",
        ):
            value = selected.get(key)
            if value:
                summary_lines.append(f"{key}: {value}")
        if summary_lines:
            parts.append("Selected item:")
            parts.extend(summary_lines[:12])
        return "\n".join(parts)

    @work(thread=True, exit_on_error=False)
    def _do_run_readonly_assistant(self, query: str, context_text: str) -> None:
        try:
            from agents.runner import Supervisor

            result = Supervisor().run_codex_exec(
                profile_name="readonly",
                goal=query,
                context_text=context_text,
            )
        except subprocess.TimeoutExpired:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Inbox assistant timed out[/]",
            )
            return
        except FileNotFoundError:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                "[red]Codex CLI not found on PATH[/]",
            )
            return
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]Inbox assistant failed: {exc}[/]",
            )
            return

        self.call_from_thread(self._show_assistant_result, query, result)

    def _show_assistant_result(self, query: str, result) -> None:
        status = "succeeded" if result.success else "failed"
        self.query_one("#status", Static).update(f"[dim]Inbox assistant {status}[/]")
        status_line = f"Session {result.session_id} · {status}"
        self.push_screen(AssistantResultModal(query, result.message, status_line))

    @work(thread=True, exit_on_error=False)
    def _do_fetch_briefing(self) -> None:
        try:
            briefing = self.client.ai_briefing()
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{_format_request_error('Briefing', exc)}[/]",
            )
            return
        self.call_from_thread(self._show_briefing_modal, briefing)

    def _show_briefing_modal(self, briefing: dict) -> None:
        self.query_one("#status", Static).update("[dim]Press Escape to close briefing[/]")
        self.push_screen(BriefingModal(briefing))

    @work(thread=True, exit_on_error=False)
    def _do_triage_conversations(self, conversations: list) -> None:  # type: ignore[type-arg]
        """Fire-and-forget triage — updates priorities without blocking UI."""
        try:
            priorities = self.client.ai_triage(conversations)
        except Exception:
            return
        if not priorities:
            return
        # Merge into local triage state
        self._triage_priorities.update(priorities)
        # Annotate current conversations with priority for display
        changed = False
        for conv in self.conversations:
            conv_id = conv.get("id", "")
            new_p = priorities.get(conv_id)
            if new_p and conv.get("_priority") != new_p:
                conv["_priority"] = new_p
                changed = True
        if changed:
            self.call_from_thread(self._render_sidebar)

    @work(thread=True, exit_on_error=False)
    def _do_summarize_thread(self, conv: dict, messages: list) -> None:  # type: ignore[type-arg]
        """Summarize a Gmail thread and store in cache."""
        thread_id = conv.get("thread_id") or conv.get("id", "")
        if not thread_id:
            return
        if thread_id in self._thread_summaries:
            return
        try:
            result = self.client.ai_summarize(thread_id=thread_id, messages=messages)
        except Exception:
            return
        if result.get("skipped"):
            return
        self._thread_summaries[thread_id] = result
        # If still viewing the same conversation, update the message view
        if self.active_conv and (
            self.active_conv.get("thread_id") == thread_id
            or self.active_conv.get("id") == thread_id
        ):
            self.call_from_thread(self._show_thread_summary, result)

    def _show_thread_summary(self, summary_data: dict) -> None:
        """Prepend summary panel to the messages view."""
        mv = self.query_one("#messages", MessageView)
        if summary_data.get("summary"):
            mv.ai_summary = summary_data
        else:
            mv.ai_summary = None

    @work(thread=True, exit_on_error=False)
    def _do_extract_actions(self, text: str, key: str) -> None:
        """Extract actions from a message body and store in cache."""
        if key in self._message_actions:
            return
        try:
            result = self.client.ai_extract_actions(text=text)
            actions = result.get("actions", [])
            if actions:
                self._message_actions[key] = actions
        except Exception:
            pass

    def action_view_message_actions(self) -> None:
        """View extracted action items for the current message."""
        if not self.active_conv:
            return
        conv_id = self.active_conv.get("id", "")
        actions = self._message_actions.get(conv_id, [])
        if not actions:
            self.query_one("#status", Static).update("[dim]No action items detected[/]")
            return
        # Show actions in status bar (brief) and detail view if visible
        action_texts = "; ".join(a.get("text", "?") for a in actions[:3])
        self.query_one("#status", Static).update(f"[cyan]Actions: {action_texts}[/]")

    # ── Cleanup ──────────────────────────────────────────────────────────

    def _cleanup_resources(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if not self._client_closed:
            self.client.close()
            self._client_closed = True

    def action_quit(self) -> None:
        self._cleanup_resources()
        self.exit()

    def on_unmount(self) -> None:
        self._cleanup_resources()


if __name__ == "__main__":
    app = InboxApp()
    app.run()
