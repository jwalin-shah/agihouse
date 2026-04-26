"""
Data access layer for Inbox — iMessage, Gmail, Calendar, Notes, Audio, LLM.
All data fetching, auth, mutation, audio, and LLM logic lives here.
"""

from __future__ import annotations

import base64
import fcntl
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel as PydanticBaseModel

import contextlib

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from loguru import logger

from contacts import ContactBook

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
CREDS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"  # legacy single-account path
TOKENS_DIR = BASE_DIR / "tokens"
IMSG_DB = Path.home() / "Library/Messages/chat.db"
NOTES_DB = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"
REMINDERS_DIR = (
    Path.home() / "Library/Group Containers/group.com.apple.reminders/Container_v1/Stores"
)
APPLE_EPOCH = datetime(2001, 1, 1)

GITHUB_TOKEN_FILE = BASE_DIR / "github_token.txt"
GOOGLE_MAPS_KEY_FILE = BASE_DIR / "google_maps_key.txt"
GEMINI_API_KEY_FILE = BASE_DIR / "gemini_api_key.txt"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/tasks",
]

ATTACHMENT_PLACEHOLDER = "\ufffc"
SQLITE_LOCK_RETRIES = 2
SQLITE_LOCK_RETRY_DELAY = 0.05
SQLITE_CONNECT_TIMEOUT = 0.2
GMAIL_METADATA_BATCH_SIZE = 10

# Global contact book
_contacts = ContactBook()


def init_contacts() -> int:
    """Load the contact book. Returns number of contacts loaded."""
    return _contacts.load()


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class Contact:
    id: str
    name: str
    source: str  # "imessage" | "gmail"
    snippet: str = ""
    unread: int = 0
    last_ts: datetime = field(default_factory=datetime.now)
    guid: str = ""
    is_group: bool = False
    members: list[str] = field(default_factory=list)
    reply_to: str = ""
    thread_id: str = ""
    message_id_header: str = ""
    gmail_account: str = ""


@dataclass
class Msg:
    sender: str
    body: str
    ts: datetime
    is_me: bool
    source: str
    attachments: list[dict[str, str | int]] = field(default_factory=list)
    message_id: str = ""  # Gmail message ID, empty for iMessage


@dataclass
class CalendarEvent:
    summary: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    account: str = ""
    all_day: bool = False
    event_id: str = ""
    calendar_id: str = ""
    attendees: list[dict[str, str]] = field(default_factory=list)
    recurrence: list[str] = field(default_factory=list)
    reminders: dict = field(default_factory=dict)
    recurring_event_id: str = ""


@dataclass
class Note:
    id: str
    title: str
    snippet: str
    modified: datetime
    folder: str = ""


@dataclass
class Reminder:
    id: str
    title: str
    completed: bool
    list_name: str = ""
    due_date: datetime | None = None
    notes: str = ""
    priority: int = 0
    flagged: bool = False
    creation_date: datetime | None = None


@dataclass
class GoogleTask:
    id: str
    title: str
    status: str  # "needsAction" | "completed"
    list_id: str
    list_title: str
    due: datetime | None = None
    notes: str = ""
    completed: datetime | None = None


@dataclass
class GitHubNotification:
    id: str
    title: str
    repo: str
    type: str  # "PullRequest", "Issue", "Release", etc.
    reason: str  # "review_requested", "mention", "subscribed", etc.
    unread: bool
    updated_at: datetime
    url: str = ""


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified: datetime
    size: int = 0
    shared: bool = False
    web_link: str = ""
    parents: list[str] = field(default_factory=list)
    account: str = ""


@dataclass
class SheetTab:
    sheet_id: int
    title: str
    index: int
    row_count: int
    col_count: int


@dataclass
class Spreadsheet:
    id: str
    title: str
    url: str
    sheets: list[SheetTab] = field(default_factory=list)
    account: str = ""


@dataclass
class Document:
    id: str
    title: str
    url: str
    mime_type: str = "application/vnd.google-apps.document"
    account: str = ""


@dataclass
class ThreadSummary:
    thread_id: str
    owning_account: str
    participants: list[str]
    subject: str
    last_message_at: datetime
    label_ids: list[str]
    body_text: str
    last_message_body: str
    last_sender_is_me: bool
    message_count: int


# ── Helpers ──────────────────────────────────────────────────────────────────


def _clean_body(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace(ATTACHMENT_PLACEHOLDER, "(attachment)")
    return text.strip()


def _parse_email_address(raw: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s*<([^>]+)>\s*$", raw.strip())
    if m:
        return m.group(1).strip().strip('"') or m.group(2), m.group(2).strip()
    return raw.strip(), raw.strip()


def _format_log_context(**context: object) -> str:
    formatted: list[str] = []
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, str) and len(value) > 120:
            value = f"{value[:117]}..."
        formatted.append(f"{key}={value!r}")
    return ", ".join(formatted)


def _log_service_failure(function_name: str, **context: object) -> None:
    context_str = _format_log_context(**context)
    message = f"{function_name} failed"
    if context_str:
        message = f"{message} ({context_str})"
    logger.exception(message)


def _escape_applescript(text: str) -> str:
    if not text:
        return '""'

    replacements = {
        '"': "quote",
        "\\": "ASCII character 92",
        "{": "ASCII character 123",
        "}": "ASCII character 125",
        "\n": "ASCII character 10",
        "\r": "ASCII character 13",
        "\t": "ASCII character 9",
    }
    parts: list[str] = []
    literal: list[str] = []

    def flush_literal() -> None:
        if literal:
            parts.append(f'"{"".join(literal)}"')
            literal.clear()

    for char in text:
        replacement = replacements.get(char)
        if replacement is not None:
            flush_literal()
            parts.append(replacement)
        elif ord(char) < 32:
            flush_literal()
            parts.append(f"ASCII character {ord(char)}")
        else:
            literal.append(char)

    flush_literal()
    return " & ".join(parts) if parts else '""'


def _token_lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def _write_text_with_lock(path: Path, payload: str) -> None:
    path.parent.mkdir(exist_ok=True)
    lock_path = _token_lock_path(path)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            with temp_path.open("w", encoding="utf-8") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class SQLiteConnectionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[tuple[str, int], sqlite3.Connection] = {}

    def get_connection(self, db_path: Path) -> sqlite3.Connection:
        key = (str(db_path), threading.get_ident())
        with self._lock:
            conn = self._connections.get(key)
            if conn is None:
                conn = sqlite3.connect(
                    f"file:{db_path}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                    timeout=SQLITE_CONNECT_TIMEOUT,
                )
                self._connections[key] = conn
            return conn

    def close_all(self) -> None:
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception:
                _log_service_failure("SQLiteConnectionManager.close_all")

    def cached_connection_count(self) -> int:
        with self._lock:
            return len(self._connections)


_sqlite_connections = SQLiteConnectionManager()


def close_sqlite_connections() -> None:
    _sqlite_connections.close_all()


def _is_sqlite_locked_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def _log_sqlite_locked_warning(
    function_name: str,
    db_path: Path,
    attempt: int,
    retries: int,
    exc: sqlite3.OperationalError,
    **context: object,
) -> None:
    context_str = _format_log_context(db_path=str(db_path), **context)
    message = f"{function_name} encountered locked SQLite database: {exc}"
    if context_str:
        message = f"{message} ({context_str})"
    logger.warning(f"{message} [attempt {attempt}/{retries + 1}]")


def _run_sqlite_read[T](
    db_path: Path,
    function_name: str,
    operation: Callable[[sqlite3.Connection], T],
    *,
    empty_result: T,
    retries: int = SQLITE_LOCK_RETRIES,
    retry_delay: float = SQLITE_LOCK_RETRY_DELAY,
    **context: object,
) -> T:
    for attempt in range(retries + 1):
        try:
            conn = _sqlite_connections.get_connection(db_path)
            return operation(conn)
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked_error(exc):
                _log_sqlite_locked_warning(
                    function_name,
                    db_path,
                    attempt + 1,
                    retries,
                    exc,
                    **context,
                )
                if attempt < retries:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                return empty_result
            _log_service_failure(function_name, db_path=str(db_path), **context)
            return empty_result
        except Exception:
            _log_service_failure(function_name, db_path=str(db_path), **context)
            return empty_result
    return empty_result


# ── Credential helpers ───────────────────────────────────────────────────────


def _load_creds(token_path: Path) -> Credentials | None:
    try:
        creds = Credentials.from_authorized_user_file(str(token_path))
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _write_text_with_lock(token_path, creds.to_json())
            else:
                return None
        return creds
    except Exception:  # logged below
        _log_service_failure("_load_creds", token_path=str(token_path))
        return None


def google_auth_all() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    """Auth all accounts from tokens/ dir. Returns (gmail_svcs, cal_svcs, drive_svcs, sheets_svcs, docs_svcs, tasks_svcs)."""
    TOKENS_DIR.mkdir(exist_ok=True)

    # Migrate legacy token.json — if it's missing scopes, re-auth
    if TOKEN_FILE.exists() and not any(TOKENS_DIR.glob("*.json")):
        creds = _load_creds(TOKEN_FILE)
        if creds:
            token_scopes = set(creds.scopes or [])
            needed = set(GOOGLE_SCOPES)
            if needed.issubset(token_scopes):
                # Has all scopes, just migrate
                _write_text_with_lock(TOKENS_DIR / "migrated.json", TOKEN_FILE.read_text())
            else:
                # Missing scopes — re-auth automatically
                if CREDS_FILE.exists():
                    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), GOOGLE_SCOPES)
                    new_creds = flow.run_local_server(port=0)
                    svc = build("gmail", "v1", credentials=new_creds)
                    email = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
                    dest = TOKENS_DIR / f"{email}.json"
                    _write_text_with_lock(dest, new_creds.to_json())

    gmail_svcs: dict[str, object] = {}
    cal_svcs: dict[str, object] = {}
    drive_svcs: dict[str, object] = {}
    sheets_svcs: dict[str, object] = {}
    docs_svcs: dict[str, object] = {}
    tasks_svcs: dict[str, object] = {}

    for token_path in sorted(TOKENS_DIR.glob("*.json")):
        creds = _load_creds(token_path)
        if not creds:
            continue

        try:
            gmail_svc = build("gmail", "v1", credentials=creds)
            profile = gmail_svc.users().getProfile(userId="me").execute()
            email = profile.get("emailAddress", token_path.stem)
            gmail_svcs[email] = gmail_svc

            expected = TOKENS_DIR / f"{email}.json"
            if token_path != expected and not expected.exists():
                token_path.rename(expected)
        except Exception:  # logged below
            _log_service_failure("google_auth_all.gmail_service", token_path=str(token_path))
            continue

        try:
            cal_svc = build("calendar", "v3", credentials=creds)
            cal_svc.calendarList().list(maxResults=1).execute()
            cal_svcs[email] = cal_svc
        except Exception:  # logged below
            _log_service_failure("google_auth_all.calendar_service", email=email)

        try:
            drive_svc = build("drive", "v3", credentials=creds)
            drive_svcs[email] = drive_svc
        except Exception:  # logged below
            _log_service_failure("google_auth_all.drive_service", email=email)

        try:
            sheets_svc = build("sheets", "v4", credentials=creds)
            sheets_svcs[email] = sheets_svc
        except Exception:  # logged below
            _log_service_failure("google_auth_all.sheets_service", email=email)

        try:
            docs_svc = build("docs", "v1", credentials=creds)
            docs_svcs[email] = docs_svc
        except Exception:  # logged below
            _log_service_failure("google_auth_all.docs_service", email=email)

        try:
            tasks_svc = build("tasks", "v1", credentials=creds)
            tasks_svcs[email] = tasks_svc
        except Exception:  # logged below
            _log_service_failure("google_auth_all.tasks_service", email=email)

    return gmail_svcs, cal_svcs, drive_svcs, sheets_svcs, docs_svcs, tasks_svcs


def add_google_account() -> str | None:
    TOKENS_DIR.mkdir(exist_ok=True)
    if not CREDS_FILE.exists():
        return None
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), GOOGLE_SCOPES)
    creds = flow.run_local_server(port=0)
    svc = build("gmail", "v1", credentials=creds)
    email = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
    token_path = TOKENS_DIR / f"{email}.json"
    _write_text_with_lock(token_path, creds.to_json())
    return email


def reauth_google_account(email: str) -> str | None:
    token_path = TOKENS_DIR / f"{email}.json"
    if token_path.exists():
        token_path.unlink()
    return add_google_account()


# ── iMessage ─────────────────────────────────────────────────────────────────


def imsg_contacts(limit: int = 30) -> list[Contact]:
    if not IMSG_DB.exists():
        return []

    def _load_contacts(conn: sqlite3.Connection) -> list[Contact]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.rowid, c.guid, c.display_name,
                last_msg.text, last_msg.ts, last_msg.unread
            FROM chat c
            JOIN (
                SELECT
                    cmj.chat_id, m.text,
                    m.date / 1000000000 + 978307200 as ts,
                    SUM(CASE WHEN m.is_read=0 AND m.is_from_me=0
                        THEN 1 ELSE 0 END) as unread
                FROM chat_message_join cmj
                JOIN message m ON cmj.message_id = m.rowid
                WHERE m.rowid IN (
                    SELECT MAX(m2.rowid) FROM message m2
                    JOIN chat_message_join cmj2 ON m2.rowid = cmj2.message_id
                    GROUP BY cmj2.chat_id
                )
                GROUP BY cmj.chat_id
            ) last_msg ON c.rowid = last_msg.chat_id
            ORDER BY last_msg.ts DESC LIMIT ?
        """,
            (limit,),
        )
        chat_rows = cur.fetchall()

        contacts = []
        for chat_id, guid, display_name, text, ts, unread in chat_rows:
            cur.execute(
                """
                SELECT h.id FROM handle h
                JOIN chat_handle_join chj ON h.rowid = chj.handle_id
                WHERE chj.chat_id = ?
            """,
                (chat_id,),
            )
            member_ids = [r[0] for r in cur.fetchall()]
            member_names = [_contacts.resolve(m) for m in member_ids]
            is_group = len(member_ids) > 1

            if display_name and display_name.strip():
                name = display_name.strip()
            elif is_group:
                shown = member_names[:3]
                name = ", ".join(shown)
                if len(member_names) > 3:
                    name += f" +{len(member_names) - 3}"
            elif member_names:
                name = member_names[0]
            else:
                name = guid.split(";")[-1]

            contacts.append(
                Contact(
                    id=str(chat_id),
                    name=name,
                    source="imessage",
                    snippet=_clean_body(text)[:60],
                    unread=unread or 0,
                    last_ts=datetime.fromtimestamp(ts) if ts else datetime.now(),
                    guid=guid,
                    is_group=is_group,
                    members=member_names,
                )
            )

        return contacts

    return _run_sqlite_read(
        IMSG_DB,
        "imsg_contacts",
        _load_contacts,
        empty_result=[],
        limit=limit,
    )


def imsg_thread(chat_id: str, limit: int = 50) -> list[Msg]:
    if not IMSG_DB.exists():
        return []

    def _load_thread(conn: sqlite3.Connection) -> list[Msg]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT m.text, m.is_from_me,
                   m.date / 1000000000 + 978307200 as ts,
                   h.id as sender_id
            FROM message m
            JOIN chat_message_join cmj ON m.rowid = cmj.message_id
            LEFT JOIN handle h ON m.handle_id = h.rowid
            WHERE cmj.chat_id = ?
            ORDER BY m.rowid DESC LIMIT ?
        """,
            (int(chat_id), limit),
        )
        rows = cur.fetchall()

        msgs = []
        for text, is_me, ts, sender_id in reversed(rows):
            body = _clean_body(text)
            if not body:
                continue
            sender = "Me" if is_me else (_contacts.resolve(sender_id or "") or sender_id or "?")
            msgs.append(
                Msg(
                    sender=sender,
                    body=body,
                    ts=datetime.fromtimestamp(ts) if ts else datetime.now(),
                    is_me=bool(is_me),
                    source="imessage",
                )
            )
        return msgs

    return _run_sqlite_read(
        IMSG_DB,
        "imsg_thread",
        _load_thread,
        empty_result=[],
        chat_id=chat_id,
        limit=limit,
    )


def imsg_send(contact: Contact, text: str) -> bool:
    safe_text = _escape_applescript(text)

    if contact.is_group:
        safe_guid = _escape_applescript(contact.guid)
        script = f"""
        tell application "Messages"
            set targetChat to (first chat whose id is ({safe_guid}))
            send ({safe_text}) to targetChat
        end tell
        """
    else:

        def _lookup_recipient(conn: sqlite3.Connection) -> str | None:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT h.id FROM handle h
                JOIN chat_handle_join chj ON h.rowid = chj.handle_id
                WHERE chj.chat_id = ? LIMIT 1
            """,
                (int(contact.id),),
            )
            row = cur.fetchone()
            return row[0] if row else None

        recipient = _run_sqlite_read(
            IMSG_DB,
            "imsg_send.lookup_recipient",
            _lookup_recipient,
            empty_result=None,
            contact_id=contact.id,
            guid=contact.guid,
            is_group=contact.is_group,
        )
        recipient = recipient or contact.guid.split(";")[-1]
        safe_recipient = _escape_applescript(recipient)

        script = f"""
        tell application "Messages"
            set targetService to (1st service whose service type = iMessage)
            send ({safe_text}) to buddy ({safe_recipient}) of targetService
        end tell
        """

    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    return result.returncode == 0


# ── Gmail ────────────────────────────────────────────────────────────────────


def _extract_parts(payload: dict) -> tuple[str, str]:
    plain = ""
    html = ""

    def _walk(part: dict) -> None:
        nonlocal plain, html
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if mime == "text/plain" and data and not plain:
            plain = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif mime == "text/html" and data and not html:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            _walk(sub)

    _walk(payload)
    return plain, html


def _html_to_text(html: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<(p|div|tr|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "  • ", text, flags=re.IGNORECASE)
    text = re.sub(
        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        r"\2 (\1)",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"&\w+;", "", text)
    return text


def _decode_body(payload: dict) -> str:
    plain, html = _extract_parts(payload)
    if plain:
        return _clean_email_body(plain.strip())
    if html:
        return _clean_email_body(_html_to_text(html).strip())
    return ""


def _clean_email_body(body: str) -> str:
    lines = body.split("\n")
    cleaned: list[str] = []
    for line in lines:
        if re.match(r"^On .+ wrote:\s*$", line):
            break
        if line.strip().startswith(">"):
            continue
        if line.strip() == "--":
            break
        if line.strip() == "---":
            break
        if re.match(
            r"^\s*(unsubscribe|view in browser|view this email)",
            line,
            re.IGNORECASE,
        ):
            continue
        if re.match(
            r"^\s*https?://\S*(track|click|unsubscribe|list-manage)",
            line,
            re.IGNORECASE,
        ):
            continue
        cleaned.append(line)

    result = "\n".join(cleaned).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"[ \t]{3,}", "  ", result)
    return result


def _gmail_metadata_headers() -> list[str]:
    return ["From", "Subject", "Date", "Message-ID"]


def _chunked(values: list[str], chunk_size: int) -> list[list[str]]:
    return [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]


def _fetch_gmail_metadata_batch(service, message_ids: list[str]) -> dict[str, dict]:
    if not message_ids:
        return {}

    responses: dict[str, dict] = {}

    def _make_batch_callback(
        errors: dict[str, Exception],
    ) -> Callable[[str, dict | None, Exception | None], None]:
        def _collect_batch_result(
            request_id: str,
            response: dict | None,
            exception: Exception | None,
        ) -> None:
            if exception is not None:
                errors[request_id] = exception
                return
            if response is not None:
                responses[request_id] = response

        return _collect_batch_result

    if not hasattr(service, "new_batch_http_request"):
        for message_id in message_ids:
            responses[message_id] = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=_gmail_metadata_headers(),
                )
                .execute()
            )
        return responses

    for batch_ids in _chunked(message_ids, GMAIL_METADATA_BATCH_SIZE):
        batch_errors: dict[str, Exception] = {}
        batch = service.new_batch_http_request(callback=_make_batch_callback(batch_errors))
        for message_id in batch_ids:
            request = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=_gmail_metadata_headers(),
                )
            )
            batch.add(request, request_id=message_id)

        batch.execute()

        for message_id, exception in batch_errors.items():
            logger.error(
                "gmail_contacts metadata fetch failed "
                f"(message_id={message_id!r}, error={exception!r})"
            )

    return responses


def gmail_contacts(service, account_email: str, limit: int = 20) -> list[Contact]:
    try:
        result = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX"], maxResults=limit)
            .execute()
        )
        messages = result.get("messages", [])
        contacts = []
        seen_threads: set[str] = set()
        thread_messages: list[dict[str, str]] = []
        for m in messages:
            thread_id = m.get("threadId", m["id"])
            if thread_id in seen_threads:
                continue
            thread_messages.append(m)
            seen_threads.add(thread_id)

        metadata_by_id = _fetch_gmail_metadata_batch(
            service, [message["id"] for message in thread_messages]
        )

        for m in thread_messages:
            msg = metadata_by_id.get(m["id"])
            if not msg:
                continue

            thread_id = m.get("threadId", m["id"])
            payload = msg.get("payload", {})
            raw_headers = payload.get("headers", [])
            headers = {h["name"]: h["value"] for h in raw_headers}
            raw_from = headers.get("From", "Unknown")
            display_name, email_addr = _parse_email_address(raw_from)
            subject = headers.get("Subject", "(no subject)")
            msg_id_header = headers.get("Message-ID", "")
            unread = "UNREAD" in msg.get("labelIds", [])
            ts_ms = int(msg.get("internalDate", 0))
            msg_ts = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime.now()
            contacts.append(
                Contact(
                    id=m["id"],
                    name=display_name,
                    source="gmail",
                    snippet=subject[:60],
                    unread=1 if unread else 0,
                    last_ts=msg_ts,
                    reply_to=email_addr,
                    thread_id=thread_id,
                    message_id_header=msg_id_header,
                    gmail_account=account_email,
                )
            )
        return contacts
    except Exception:  # logged below
        _log_service_failure("gmail_contacts", account_email=account_email, limit=limit)
        return []


def _extract_attachments(payload: dict, msg_id: str) -> list[dict[str, str | int]]:
    """Extract attachment metadata from a Gmail message payload."""
    attachments: list[dict[str, str | int]] = []

    def _walk(part: dict) -> None:
        filename = part.get("filename", "")
        body = part.get("body", {})
        att_id = body.get("attachmentId", "")
        if filename and att_id:
            attachments.append(
                {
                    "filename": filename,
                    "mimeType": part.get("mimeType", "application/octet-stream"),
                    "size": body.get("size", 0),
                    "attachmentId": att_id,
                    "messageId": msg_id,
                }
            )
        for sub in part.get("parts", []):
            _walk(sub)

    _walk(payload)
    return attachments


def gmail_thread(service, msg_id: str, thread_id: str = "") -> list[Msg]:
    try:
        tid = thread_id or msg_id
        thread = service.users().threads().get(userId="me", id=tid, format="full").execute()
        msgs = []
        me_email = service.users().getProfile(userId="me").execute().get("emailAddress", "")
        for i, m in enumerate(thread.get("messages", [])):
            headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            raw_from = headers.get("From", "Unknown")
            display_name, email_addr = _parse_email_address(raw_from)
            subject = headers.get("Subject", "")
            to_raw = headers.get("To", "")
            body = _decode_body(m["payload"])
            ts_ms = int(m.get("internalDate", 0))
            ts = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime.now()
            is_me = me_email.lower() in email_addr.lower()
            sender = "Me" if is_me else display_name
            msg_id_from_thread = m.get("id", msg_id)
            atts = _extract_attachments(m["payload"], msg_id_from_thread)
            if body:
                if i == 0 and subject:
                    body = f"Subject: {subject}\nTo: {to_raw}\n{'─' * 30}\n\n{body}"
                msgs.append(
                    Msg(
                        sender=sender,
                        body=body,
                        ts=ts,
                        is_me=is_me,
                        source="gmail",
                        attachments=atts,
                        message_id=msg_id_from_thread,
                    )
                )
        return msgs
    except Exception:  # logged below
        _log_service_failure("gmail_thread", msg_id=msg_id, thread_id=thread_id or msg_id)
        return []


def gmail_thread_summary(service, thread_id: str, account_email: str) -> ThreadSummary | None:
    """Fetch normalized thread metadata for analysis — includes labels, participants, body."""
    try:
        me_email = service.users().getProfile(userId="me").execute().get("emailAddress", "")
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        messages = thread.get("messages", [])
        if not messages:
            return None

        subject = ""
        participants: list[str] = []
        seen_participants: set[str] = set()
        label_ids: set[str] = set()
        body_chunks: list[str] = []
        last_ts = datetime.now()
        last_sender_is_me = False
        last_message_body = ""

        for i, m in enumerate(messages):
            headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            raw_from = headers.get("From", "Unknown")
            display_name, email_addr = _parse_email_address(raw_from)
            if i == 0:
                subject = headers.get("Subject", "(no subject)")

            if email_addr not in seen_participants:
                seen_participants.add(email_addr)
                participants.append(display_name or email_addr)

            for lbl in m.get("labelIds", []):
                label_ids.add(lbl)

            ts_ms = int(m.get("internalDate", 0))
            if ts_ms:
                last_ts = datetime.fromtimestamp(ts_ms / 1000)

            body = _decode_body(m["payload"])
            if body:
                body_chunks.append(body)
                last_message_body = body

            if i == len(messages) - 1:
                last_sender_is_me = me_email.lower() in email_addr.lower()

        return ThreadSummary(
            thread_id=thread_id,
            owning_account=account_email,
            participants=participants,
            subject=subject,
            last_message_at=last_ts,
            label_ids=sorted(label_ids),
            body_text="\n\n".join(body_chunks)[:4000],
            last_message_body=last_message_body[:1000],
            last_sender_is_me=last_sender_is_me,
            message_count=len(messages),
        )
    except Exception:
        _log_service_failure("gmail_thread_summary", thread_id=thread_id)
        return None


def gmail_send(service, contact: Contact, body: str) -> bool:
    msg = MIMEText(body)
    msg["to"] = contact.reply_to
    msg["subject"] = f"Re: {contact.snippet}"
    if contact.message_id_header:
        msg["In-Reply-To"] = contact.message_id_header
        msg["References"] = contact.message_id_header
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    send_body: dict = {"raw": raw}
    if contact.thread_id:
        send_body["threadId"] = contact.thread_id
    try:
        service.users().messages().send(userId="me", body=send_body).execute()
        return True
    except Exception:  # logged below
        _log_service_failure(
            "gmail_send",
            reply_to=contact.reply_to,
            thread_id=contact.thread_id,
            body_length=len(body),
        )
        return False


# ── Gmail actions ────────────────────────────────────────────────────────────


def gmail_archive(service, msg_id: str) -> bool:
    """Archive a Gmail message by removing the INBOX label."""
    try:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["INBOX"]}
        ).execute()
        return True
    except Exception:
        _log_service_failure("gmail_archive", msg_id=msg_id)
        return False


def gmail_get_unsubscribe_info(service, msg_id: str) -> dict[str, str]:
    """Return {'url': ..., 'mailto': ..., 'one_click': bool} from List-Unsubscribe headers."""
    try:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        raw = headers.get("List-Unsubscribe", "")
        one_click = "List-Unsubscribe-Post" in headers

        url = None
        mailto = None
        for part in raw.split(","):
            part = part.strip().strip("<>")
            if part.startswith("http"):
                url = part
            elif part.startswith("mailto:"):
                mailto = part

        return {"url": url, "mailto": mailto, "one_click": one_click, "raw": raw}
    except Exception as e:
        logger.warning(f"Failed to get unsubscribe info for {msg_id}: {e}")
        return {"url": None, "mailto": None, "one_click": False, "raw": ""}


def gmail_unsubscribe(service, msg_id: str) -> dict[str, str]:
    """
    Execute unsubscribe via List-Unsubscribe header, then archive the message.
    Returns {"method": "url|mailto|none", "ok": bool}.
    """
    import urllib.parse

    import requests

    info = gmail_get_unsubscribe_info(service, msg_id)

    method = "none"
    ok = False

    if info["url"]:
        method = "url"
        try:
            if info["one_click"]:
                resp = requests.post(
                    info["url"], data={"List-Unsubscribe": "One-Click"}, timeout=10
                )
            else:
                resp = requests.get(info["url"], timeout=10)
            ok = resp.status_code < 400
        except Exception as e:
            logger.warning(f"Unsubscribe URL failed: {e}")

    elif info["mailto"]:
        method = "mailto"
        try:
            parsed = urllib.parse.urlparse(info["mailto"])
            to = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            subject = qs.get("subject", ["unsubscribe"])[0]
            ok = gmail_compose_send(service, to, subject, "")
        except Exception as e:
            logger.warning(f"Unsubscribe mailto failed: {e}")

    gmail_archive(service, msg_id)
    return {"method": method, "ok": ok, "raw": info["raw"]}


def gmail_delete(service, msg_id: str) -> bool:
    """Move a Gmail message to trash."""
    try:
        service.users().messages().trash(userId="me", id=msg_id).execute()
        return True
    except Exception:
        _log_service_failure("gmail_delete", msg_id=msg_id)
        return False


def gmail_star(service, msg_id: str) -> bool:
    """Add STARRED label to a Gmail message."""
    try:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"addLabelIds": ["STARRED"]}
        ).execute()
        return True
    except Exception:
        _log_service_failure("gmail_star", msg_id=msg_id)
        return False


def gmail_unstar(service, msg_id: str) -> bool:
    """Remove STARRED label from a Gmail message."""
    try:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["STARRED"]}
        ).execute()
        return True
    except Exception:
        _log_service_failure("gmail_unstar", msg_id=msg_id)
        return False


def gmail_mark_read(service, msg_id: str) -> bool:
    """Mark a Gmail message as read by removing UNREAD label."""
    try:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except Exception:
        _log_service_failure("gmail_mark_read", msg_id=msg_id)
        return False


def gmail_mark_unread(service, msg_id: str) -> bool:
    """Mark a Gmail message as unread by adding UNREAD label."""
    try:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"addLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except Exception:
        _log_service_failure("gmail_mark_unread", msg_id=msg_id)
        return False


def gmail_labels(service) -> list[dict[str, str]]:
    """List all Gmail labels for the account."""
    try:
        result = service.users().labels().list(userId="me").execute()
        labels = []
        for lbl in result.get("labels", []):
            labels.append(
                {
                    "id": lbl.get("id", ""),
                    "name": lbl.get("name", ""),
                    "type": lbl.get("type", ""),
                }
            )
        return labels
    except Exception:
        _log_service_failure("gmail_labels")
        return []


def gmail_attachment_download(service, msg_id: str, att_id: str) -> bytes | None:
    """Download a Gmail attachment and return the binary data."""
    try:
        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=msg_id, id=att_id)
            .execute()
        )
        data = att.get("data", "")
        if data:
            return base64.urlsafe_b64decode(data)
        return None
    except Exception:
        _log_service_failure("gmail_attachment_download", msg_id=msg_id, att_id=att_id)
        return None


def gmail_compose_send(service, to: str, subject: str, body: str) -> bool:
    """Send a new email (not a reply)."""
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True
    except Exception:
        _log_service_failure("gmail_compose_send", to=to, subject=subject, body_length=len(body))
        return False


def gmail_reply(
    service,
    msg_id: str,
    body: str,
    thread_id: str = "",
    to: str = "",
    subject: str = "",
    message_id_header: str = "",
) -> bool:
    """Reply to an existing Gmail thread."""
    contact = Contact(
        id=msg_id,
        name="",
        source="gmail",
        snippet=subject or "(no subject)",
        reply_to=to,
        thread_id=thread_id,
        message_id_header=message_id_header,
    )
    if not contact.reply_to or not contact.message_id_header:
        try:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=_gmail_metadata_headers() + ["To"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            _, email_addr = _parse_email_address(headers.get("From", ""))
            contact.reply_to = contact.reply_to or email_addr
            contact.thread_id = contact.thread_id or msg.get("threadId", "")
            contact.message_id_header = contact.message_id_header or headers.get("Message-ID", "")
            contact.snippet = contact.snippet if subject else headers.get("Subject", "(no subject)")
        except Exception:
            _log_service_failure("gmail_reply.lookup", msg_id=msg_id, thread_id=thread_id)
            return False
    return gmail_send(service, contact, body)


def gmail_search(
    service,
    account_email: str,
    q: str = "",
    limit: int = 20,
    label: str = "",
    from_filter: str = "",
    subject_filter: str = "",
    after: str = "",
    before: str = "",
) -> list[Contact]:
    """Search Gmail conversations using Gmail's native query syntax."""
    query_parts = [q.strip()]
    if from_filter.strip():
        query_parts.append(f"from:{from_filter.strip()}")
    if subject_filter.strip():
        query_parts.append(f"subject:({subject_filter.strip()})")
    if after.strip():
        query_parts.append(f"after:{after.strip()}")
    if before.strip():
        query_parts.append(f"before:{before.strip()}")
    query = " ".join(part for part in query_parts if part)

    try:
        req = service.users().messages().list(userId="me", q=query, maxResults=limit)
        if label.strip():
            req = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    labelIds=[label.strip()],
                    maxResults=limit,
                )
            )
        result = req.execute()
        messages = result.get("messages", [])
        contacts: list[Contact] = []
        seen_threads: set[str] = set()
        thread_messages: list[dict[str, str]] = []
        for m in messages:
            thread_id = m.get("threadId", m["id"])
            if thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)
            thread_messages.append(m)

        metadata_by_id = _fetch_gmail_metadata_batch(service, [m["id"] for m in thread_messages])
        for m in thread_messages:
            msg = metadata_by_id.get(m["id"])
            if not msg:
                continue
            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            raw_from = headers.get("From", "Unknown")
            display_name, email_addr = _parse_email_address(raw_from)
            subject = headers.get("Subject", "(no subject)")
            msg_id_header = headers.get("Message-ID", "")
            unread = "UNREAD" in msg.get("labelIds", [])
            ts_ms = int(msg.get("internalDate", 0))
            msg_ts = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime.now()
            contacts.append(
                Contact(
                    id=m["id"],
                    name=display_name,
                    source="gmail",
                    snippet=subject[:60],
                    unread=1 if unread else 0,
                    last_ts=msg_ts,
                    reply_to=email_addr,
                    thread_id=m.get("threadId", m["id"]),
                    message_id_header=msg_id_header,
                    gmail_account=account_email,
                )
            )
        contacts.sort(key=lambda c: c.last_ts, reverse=True)
        return contacts
    except Exception:
        _log_service_failure(
            "gmail_search",
            account_email=account_email,
            q=query,
            limit=limit,
            label=label,
        )
        return []


def gmail_batch_modify(
    service,
    msg_ids: list[str],
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> bool:
    """Apply Gmail labels to multiple messages at once."""
    try:
        service.users().messages().batchModify(
            userId="me",
            body={
                "ids": msg_ids,
                "addLabelIds": add_label_ids or [],
                "removeLabelIds": remove_label_ids or [],
            },
        ).execute()
        return True
    except Exception:
        _log_service_failure(
            "gmail_batch_modify",
            msg_count=len(msg_ids),
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        return False


def gmail_create_filter(
    service,
    criteria: dict[str, str],
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
    forward: str = "",
) -> dict | None:
    """Create a Gmail filter. Requires gmail.settings.basic scope."""
    try:
        action: dict[str, object] = {
            "addLabelIds": add_label_ids or [],
            "removeLabelIds": remove_label_ids or [],
        }
        if forward:
            action["forward"] = forward
        result = (
            service.users()
            .settings()
            .filters()
            .create(
                userId="me",
                body={
                    "criteria": {k: v for k, v in criteria.items() if v},
                    "action": action,
                },
            )
            .execute()
        )
        return result
    except Exception:
        _log_service_failure(
            "gmail_create_filter",
            criteria=criteria,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
            forward=forward,
        )
        return None


def gmail_contacts_by_label(
    service, account_email: str, label_id: str = "INBOX", limit: int = 20
) -> list[Contact]:
    """List Gmail conversations filtered by label."""
    try:
        result = (
            service.users()
            .messages()
            .list(userId="me", labelIds=[label_id], maxResults=limit)
            .execute()
        )
        messages = result.get("messages", [])
        contacts: list[Contact] = []
        seen_threads: set[str] = set()
        thread_messages: list[dict[str, str]] = []
        for m in messages:
            thread_id = m.get("threadId", m["id"])
            if thread_id in seen_threads:
                continue
            thread_messages.append(m)
            seen_threads.add(thread_id)

        metadata_by_id = _fetch_gmail_metadata_batch(
            service, [message["id"] for message in thread_messages]
        )

        for m in thread_messages:
            msg = metadata_by_id.get(m["id"])
            if not msg:
                continue

            thread_id = m.get("threadId", m["id"])
            payload = msg.get("payload", {})
            raw_headers = payload.get("headers", [])
            headers = {h["name"]: h["value"] for h in raw_headers}
            raw_from = headers.get("From", "Unknown")
            display_name, email_addr = _parse_email_address(raw_from)
            subject = headers.get("Subject", "(no subject)")
            msg_id_header = headers.get("Message-ID", "")
            unread = "UNREAD" in msg.get("labelIds", [])
            ts_ms = int(msg.get("internalDate", 0))
            msg_ts = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime.now()
            contacts.append(
                Contact(
                    id=m["id"],
                    name=display_name,
                    source="gmail",
                    snippet=subject[:60],
                    unread=1 if unread else 0,
                    last_ts=msg_ts,
                    reply_to=email_addr,
                    thread_id=thread_id,
                    message_id_header=msg_id_header,
                    gmail_account=account_email,
                )
            )
        return contacts
    except Exception:
        _log_service_failure(
            "gmail_contacts_by_label",
            account_email=account_email,
            label_id=label_id,
            limit=limit,
        )
        return []


# ── Calendar ─────────────────────────────────────────────────────────────────


def calendar_events(
    cal_services: dict[str, object],
    date: datetime | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[CalendarEvent]:
    """Fetch events from all accounts.

    Supports two modes:
    - Single day: pass ``date`` (defaults to today).
    - Date range: pass both ``start_date`` and ``end_date``.
    """
    if start_date and end_date:
        range_start = start_date.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        range_end = end_date.astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
    else:
        now = (date or datetime.now()).astimezone()
        range_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        range_end = range_start + timedelta(days=1)

    events: list[CalendarEvent] = []
    for email, svc in cal_services.items():
        try:
            cal_list = svc.calendarList().list().execute()  # type: ignore[attr-defined]
            for cal_entry in cal_list.get("items", []):
                cal_id = cal_entry["id"]
                result = (
                    svc.events()  # type: ignore[attr-defined]
                    .list(
                        calendarId=cal_id,
                        timeMin=range_start.isoformat(),
                        timeMax=range_end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

                for item in result.get("items", []):
                    start_raw = item.get("start", {})
                    end_raw = item.get("end", {})

                    all_day = "date" in start_raw
                    if all_day:
                        start_dt = datetime.strptime(start_raw["date"], "%Y-%m-%d")
                        end_dt = datetime.strptime(end_raw["date"], "%Y-%m-%d")
                    else:
                        start_dt = datetime.fromisoformat(start_raw.get("dateTime", ""))
                        end_dt = datetime.fromisoformat(end_raw.get("dateTime", ""))

                    # Extract attendee data
                    raw_attendees = item.get("attendees", [])
                    attendee_list = [
                        {
                            "name": a.get("displayName", ""),
                            "email": a.get("email", ""),
                            "responseStatus": a.get("responseStatus", ""),
                        }
                        for a in raw_attendees
                    ]

                    events.append(
                        CalendarEvent(
                            summary=item.get("summary", "(No title)"),
                            start=start_dt,
                            end=end_dt,
                            location=item.get("location", ""),
                            description=item.get("description", ""),
                            account=email,
                            all_day=all_day,
                            event_id=item.get("id", ""),
                            calendar_id=cal_id,
                            attendees=attendee_list,
                        )
                    )
        except Exception:  # logged below
            _log_service_failure(
                "calendar_events.account",
                account=email,
                date=range_start.date().isoformat(),
            )
            continue

    events.sort(key=lambda e: (not e.all_day, e.start))
    return events


def _build_event_body(
    summary: str,
    start: datetime,
    end: datetime,
    location: str = "",
    description: str = "",
    all_day: bool = False,
    attendees: list[dict[str, str]] | None = None,
    recurrence: list[str] | None = None,
    reminders: dict | None = None,
) -> dict:
    body: dict = {"summary": summary}
    if all_day:
        body["start"] = {"date": start.strftime("%Y-%m-%d")}
        body["end"] = {"date": end.strftime("%Y-%m-%d")}
    else:
        tz = start.astimezone().tzinfo
        tz_name = str(tz) if tz else "America/Los_Angeles"
        body["start"] = {
            "dateTime": start.astimezone().isoformat(),
            "timeZone": tz_name,
        }
        body["end"] = {
            "dateTime": end.astimezone().isoformat(),
            "timeZone": tz_name,
        }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [
            {k: v for k, v in attendee.items() if v}
            for attendee in attendees
            if attendee.get("email")
        ]
    if recurrence:
        body["recurrence"] = recurrence
    if reminders:
        body["reminders"] = reminders
    return body


def calendar_create_event(
    cal_service,
    summary: str,
    start: datetime,
    end: datetime,
    location: str = "",
    description: str = "",
    all_day: bool = False,
    attendees: list[dict[str, str]] | None = None,
    calendar_id: str = "primary",
    recurrence: list[str] | None = None,
    reminders: dict | None = None,
) -> str | None:
    try:
        body = _build_event_body(
            summary,
            start,
            end,
            location,
            description,
            all_day,
            attendees=attendees,
            recurrence=recurrence,
            reminders=reminders,
        )
        result = cal_service.events().insert(calendarId=calendar_id, body=body).execute()
        event_id = result.get("id")
        # Send notification
        send_notification(
            title=f"Calendar: {summary}",
            body=f"Created for {start.strftime('%Y-%m-%d %H:%M') if hasattr(start, 'strftime') else start}",
            source="calendar",
        )
        return event_id
    except Exception as e:
        _log_service_failure(
            "calendar_create_event",
            error=str(e),
            summary=summary,
            calendar_id=calendar_id,
            all_day=all_day,
        )
        raise


def calendar_update_event(
    cal_service,
    event_id: str,
    summary: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    location: str | None = None,
    description: str | None = None,
    calendar_id: str = "primary",
    reminders: dict | None = None,
) -> bool:
    try:
        existing = cal_service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        if summary is not None:
            existing["summary"] = summary
        if start is not None:
            all_day = "date" in existing.get("start", {})
            if all_day:
                existing["start"] = {"date": start.strftime("%Y-%m-%d")}
            else:
                existing["start"] = {"dateTime": start.astimezone().isoformat()}
        if end is not None:
            all_day = "date" in existing.get("end", {})
            if all_day:
                existing["end"] = {"date": end.strftime("%Y-%m-%d")}
            else:
                existing["end"] = {"dateTime": end.astimezone().isoformat()}
        if location is not None:
            existing["location"] = location
        if description is not None:
            existing["description"] = description
        if reminders is not None:
            existing["reminders"] = reminders
        cal_service.events().update(
            calendarId=calendar_id, eventId=event_id, body=existing
        ).execute()
        # Send notification
        send_notification(
            title=f"Calendar: {summary or existing.get('summary', 'Event')} updated",
            body="Modified event on calendar",
            source="calendar",
        )
        return True
    except Exception:  # logged below
        _log_service_failure(
            "calendar_update_event",
            event_id=event_id,
            calendar_id=calendar_id,
            summary=summary,
        )
        return False


def calendar_delete_event(cal_service, event_id: str, calendar_id: str = "primary") -> bool:
    try:
        cal_service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return True
    except Exception:  # logged below
        _log_service_failure(
            "calendar_delete_event",
            event_id=event_id,
            calendar_id=calendar_id,
        )
        return False


def calendar_event_to_reminder(
    event: CalendarEvent,
    list_name: str = "Reminders",
    minutes_before: int = 30,
) -> bool:
    """Create an Apple Reminder from a calendar event.

    Due time = event start - ``minutes_before``. Notes are populated from the
    event's location, attendees, and description.
    """
    if not event or not event.start:
        return False

    # CalendarEvent.start is a datetime; tolerate a string fallback in case a
    # caller hands us a raw API value.
    try:
        if isinstance(event.start, datetime):
            start_dt = event.start
        else:
            start_dt = datetime.fromisoformat(str(event.start).replace("Z", "+00:00"))
        due_dt: datetime | None = start_dt - timedelta(minutes=minutes_before)
    except Exception:
        due_dt = None

    # Build notes from event details
    notes_parts: list[str] = []
    if event.location:
        notes_parts.append(f"Location: {event.location}")
    if event.attendees:
        attendee_list = [(a.get("displayName") or a.get("email") or "") for a in event.attendees]
        attendee_list = [a for a in attendee_list if a]
        if attendee_list:
            notes_parts.append(f"Attendees: {', '.join(attendee_list)}")
    if event.description:
        notes_parts.append(event.description)
    notes_text = "\n".join(notes_parts)

    # AppleScript's `date` coercion expects a human-readable string, not ISO.
    due_str = due_dt.strftime("%B %d, %Y %I:%M:%S %p") if due_dt else ""

    return reminder_create(
        title=event.summary or "Calendar Event",
        list_name=list_name,
        due_date=due_str,
        notes=notes_text,
    )


def gmail_label_create(service, name: str, visibility: str = "labelShow") -> dict[str, str]:
    """Create a Gmail label and return its id and name."""
    try:
        body = {
            "name": name,
            "labelListVisibility": visibility,
            "messageListVisibility": "show",
        }
        result = service.users().labels().create(userId="me", body=body).execute()
        return {"id": result["id"], "name": result["name"]}
    except Exception:
        _log_service_failure("gmail_label_create", name=name)
        raise


def calendar_find_conflicts(
    cal_services: dict[str, object], start: datetime, end: datetime
) -> list[CalendarEvent]:
    """Find calendar events that conflict with time range [start, end] across all accounts."""
    conflicts = []
    for account, service in cal_services.items():
        try:
            # Get actual events in that time range
            events = calendar_events(
                cal_services={account: service}, start_date=start, end_date=end
            )

            # Filter to only overlapping events
            for event in events:
                # event.start and event.end are already datetime objects
                try:
                    event_start = (
                        datetime.fromisoformat(event.start)
                        if isinstance(event.start, str)
                        else event.start
                    )
                    event_end = (
                        datetime.fromisoformat(event.end)
                        if isinstance(event.end, str)
                        else event.end
                    )
                except (TypeError, ValueError):
                    continue
                # Overlap test: start_A < end_B and start_B < end_A
                if start < event_end and event_start < end:
                    conflicts.append(event)
        except Exception:
            _log_service_failure("calendar_find_conflicts", account=account)
    return conflicts


def ai_extract_memory(text: str) -> dict[str, object]:
    """Extract people, projects, commitments from text using LLM."""
    from pydantic import BaseModel, Field

    class PersonExtract(BaseModel):
        name: str
        context: str = ""
        relationship: str = ""

    class ProjectExtract(BaseModel):
        name: str
        description: str = ""
        status: str = "active"

    class CommitmentExtract(BaseModel):
        text: str
        deadline: str = ""
        owner: str = ""

    class MemoryExtractionResult(BaseModel):
        people: list[PersonExtract] = Field(default_factory=list)
        projects: list[ProjectExtract] = Field(default_factory=list)
        commitments: list[CommitmentExtract] = Field(default_factory=list)
        action_items: list[str] = Field(default_factory=list)

    try:
        result = generate_json_large(
            prompt=f"Extract people (with name, context, relationship), projects (name, description, status), commitments (text, deadline, owner), and action items from:\n{text[:2000]}",
            schema=MemoryExtractionResult,
        )
        if result is None:
            # Fallback to small model
            result = generate_json(
                prompt=f"Extract people, projects, commitments, and action items from:\n{text[:1500]}",
                schema=MemoryExtractionResult,
            )
        if result:
            return {
                "people": [p.dict() for p in result.people],
                "projects": [p.dict() for p in result.projects],
                "commitments": [c.dict() for c in result.commitments],
                "action_items": result.action_items,
            }
    except Exception:
        _log_service_failure("ai_extract_memory")

    return {"people": [], "projects": [], "commitments": [], "action_items": []}


# ── Notes ────────────────────────────────────────────────────────────────────


def notes_list(limit: int = 50) -> list[Note]:
    """List recent Apple Notes from SQLite."""
    if not NOTES_DB.exists():
        return []

    def _load_notes(conn: sqlite3.Connection) -> list[Note]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                n.Z_PK,
                n.ZTITLE1,
                n.ZSNIPPET,
                n.ZMODIFICATIONDATE1,
                COALESCE(f.ZTITLE2, '') as folder
            FROM ZICCLOUDSYNCINGOBJECT n
            LEFT JOIN ZICCLOUDSYNCINGOBJECT f
                ON n.ZFOLDER = f.Z_PK
            WHERE n.ZTITLE1 IS NOT NULL
            AND (n.ZMARKEDFORDELETION IS NULL OR n.ZMARKEDFORDELETION = 0)
            ORDER BY n.ZMODIFICATIONDATE1 DESC
            LIMIT ?
        """,
            (limit,),
        )
        rows = cur.fetchall()

        notes = []
        for pk, title, snippet, mod_date, folder in rows:
            ts = APPLE_EPOCH + timedelta(seconds=mod_date) if mod_date else datetime.now()
            notes.append(
                Note(
                    id=str(pk),
                    title=title or "(Untitled)",
                    snippet=(snippet or "")[:100],
                    modified=ts,
                    folder=folder or "",
                )
            )
        return notes

    return _run_sqlite_read(
        NOTES_DB,
        "notes_list",
        _load_notes,
        empty_result=[],
        limit=limit,
    )


def note_body(title: str) -> str:
    """Get full note body via AppleScript."""
    safe_title = _escape_applescript(title)
    script = f"""
    tell application "Notes"
        try
            set theNote to first note whose name is ({safe_title})
            return plaintext of theNote
        on error
            return ""
        end try
    end tell
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=10,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:  # logged below
        _log_service_failure("note_body", title=title)
        return ""


# ── Quick event parser ───────────────────────────────────────────────────────


def _parse_time(s: str) -> datetime | None:
    s = s.strip().lower()
    now = datetime.now()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        return now.replace(
            hour=int(m.group(1)),
            minute=int(m.group(2)),
            second=0,
            microsecond=0,
        )
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm" and hour != 12:
            hour += 12
        elif m.group(3) == "am" and hour == 12:
            hour = 0
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return None


def parse_quick_event(text: str) -> dict:
    """Parse quick event format: Title HH:MM-HH:MM @ Location"""
    location = ""
    if " @ " in text:
        text, location = text.rsplit(" @ ", 1)

    if text.lower().startswith("all day:"):
        summary = text[8:].strip()
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return {
            "summary": summary,
            "start": start,
            "end": end,
            "location": location,
            "all_day": True,
        }

    time_pattern = (
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*[-–]\s*"
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$"
    )
    m = re.search(time_pattern, text, re.IGNORECASE)
    if m:
        summary = text[: m.start()].strip()
        start = _parse_time(m.group(1))
        end = _parse_time(m.group(2))
        if start and end:
            return {
                "summary": summary,
                "start": start,
                "end": end,
                "location": location,
                "all_day": False,
            }

    now = datetime.now().replace(second=0, microsecond=0)
    return {
        "summary": text,
        "start": now,
        "end": now + timedelta(hours=1),
        "location": location,
        "all_day": False,
    }


# ── WhatsApp (via macOS Accessibility API) ───────────────────────────────────


def _whatsapp_pid() -> int | None:
    """Find WhatsApp.app PID via NSRunningApplication.
    Returns None if WhatsApp is not running or Objective-C imports fail.
    """
    try:
        from Foundation import NSWorkspace

        ws = NSWorkspace.sharedWorkspace()
        apps = ws.runningApplications()
        for app in apps:
            if app.bundleIdentifier() == "net.whatsapp.WhatsApp":
                return app.processIdentifier()
    except Exception:
        pass
    return None


def _ax_attr(el: Any, name: str) -> Any:
    """Safe AX attribute getter. Returns None on failure."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue

        err, val = AXUIElementCopyAttributeValue(el, name, None)
        if err != 0:
            return None
        return val
    except Exception:
        return None


def _ax_children(el: Any) -> list[Any]:
    kids = _ax_attr(el, "AXChildren")
    return list(kids) if kids else []


def _ax_walk(
    el: Any, predicate, depth: int = 0, max_depth: int = 12, out: list | None = None
) -> list[Any]:
    """Pre-order walk collecting elements matching predicate."""
    if out is None:
        out = []
    if depth > max_depth or el is None:
        return out
    try:
        if predicate(el):
            out.append(el)
        for c in _ax_children(el):
            _ax_walk(c, predicate, depth + 1, max_depth, out)
    except Exception:
        pass
    return out


def whatsapp_check_accessibility(prompt: bool = False) -> bool:
    """Check if this process has Accessibility permission. If prompt=True, shows system dialog."""
    try:
        if prompt:
            from ApplicationServices import AXIsProcessTrustedWithOptions
            from CoreFoundation import (
                CFDictionaryCreate,
                kCFTypeDictionaryKeyCallBacks,
                kCFTypeDictionaryValueCallBacks,
            )

            key = "AXTrustedCheckOptionPrompt"
            opts = CFDictionaryCreate(
                None,
                [key],
                [True],
                1,
                kCFTypeDictionaryKeyCallBacks,
                kCFTypeDictionaryValueCallBacks,
            )
            return bool(AXIsProcessTrustedWithOptions(opts))
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def whatsapp_launch(wait_seconds: float = 3.0) -> bool:
    """Launch WhatsApp.app if not running. Returns True if app is running after call."""
    try:
        import subprocess
        import time

        if _whatsapp_pid():
            return True
        subprocess.run(["open", "-a", "WhatsApp"], check=False, timeout=10)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if _whatsapp_pid():
                return True
            time.sleep(0.25)
        return _whatsapp_pid() is not None
    except Exception:
        _log_service_failure("whatsapp_launch")
        return False


def _whatsapp_app_ref() -> Any:
    """Return the AX application ref for WhatsApp, or None."""
    try:
        pid = _whatsapp_pid()
        if not pid:
            return None
        from ApplicationServices import AXUIElementCreateApplication

        return AXUIElementCreateApplication(pid)
    except Exception:
        return None


def _whatsapp_main_window() -> Any:
    app_ref = _whatsapp_app_ref()
    if not app_ref:
        return None
    windows = _ax_attr(app_ref, "AXWindows") or []
    # Prefer focused window, else first
    for w in windows:
        if _ax_attr(w, "AXMain"):
            return w
    return windows[0] if windows else None


def _collect_text(el: Any, limit: int = 5) -> list[str]:
    """Gather AXValue/AXTitle text from descendants (breadth-first, capped)."""
    out: list[str] = []
    stack = [(el, 0)]
    while stack and len(out) < limit:
        cur, d = stack.pop(0)
        if d > 4:
            continue
        for attr in ("AXValue", "AXTitle", "AXDescription"):
            v = _ax_attr(cur, attr)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
                break
        for c in _ax_children(cur):
            stack.append((c, d + 1))
    return out


_WA_LRM = "\u200e"  # Left-to-right mark WhatsApp prefixes to most strings


def _wa_strip(s: str) -> str:
    return (s or "").replace(_WA_LRM, "").strip()


def _wa_find_chat_list(window: Any) -> Any:
    """Find the AXGroup whose AXDescription is 'List of chats'."""

    def pred(el: Any) -> bool:
        return _wa_strip(_ax_attr(el, "AXDescription") or "") == "List of chats"

    hits = _ax_walk(window, pred, max_depth=15)
    return hits[0] if hits else None


def _wa_parse_row(row: Any) -> tuple[str, int, str] | None:
    """Parse a sidebar chat button.
    AXDescription: '<Name>' or '<Name>, N unread messages' (+ optional ', Announcement group')
    AXValue: preview like 'Message from X, ...' or 'Your message, ...'
    Returns (name, unread_count, preview) or None.
    """
    desc = _wa_strip(_ax_attr(row, "AXDescription") or "")
    val = _wa_strip(_ax_attr(row, "AXValue") or "")
    if not desc:
        return None
    name = desc
    unread = 0
    import re

    m = re.search(r",\s*(\d+)\s+unread\s+messages?", desc)
    if m:
        unread = int(m.group(1))
        name = desc[: m.start()].rstrip(", ").strip()
    # Strip other trailing qualifiers (e.g. "Announcement group")
    name = name.split(", ")[0].strip()
    return name, unread, val


def _whatsapp_accessibility_tree() -> list[Contact]:
    """Walk WhatsApp AX tree and extract sidebar conversations.
    Only rows currently rendered in the sidebar are returned (WhatsApp lazily
    loads rows as you scroll).
    """
    try:
        if not whatsapp_check_accessibility(prompt=False):
            return []
        window = _whatsapp_main_window()
        if not window:
            return []
        chat_list = _wa_find_chat_list(window)
        if not chat_list:
            return []

        contacts: list[Contact] = []
        seen: set[str] = set()
        for row in _ax_children(chat_list):
            if _ax_attr(row, "AXRole") != "AXButton":
                continue
            parsed = _wa_parse_row(row)
            if not parsed:
                continue
            name, unread, preview = parsed
            if not name or name in seen:
                continue
            seen.add(name)
            contacts.append(
                Contact(
                    id=name,
                    name=name,
                    source="whatsapp",
                    snippet=preview,
                    unread=unread,
                )
            )
        return contacts
    except Exception:
        _log_service_failure("_whatsapp_accessibility_tree")
        return []


def whatsapp_contacts(limit: int = 20) -> list[Contact]:
    """List WhatsApp conversations via macOS Accessibility API (read-only).
    WhatsApp app must be running and Accessibility permission must be granted.
    """
    contacts = _whatsapp_accessibility_tree()
    return contacts[:limit]


def _whatsapp_select_chat(chat_name: str) -> bool:
    """Click the sidebar AXButton whose parsed name matches chat_name."""
    try:
        from ApplicationServices import AXUIElementPerformAction

        window = _whatsapp_main_window()
        if not window:
            return False
        chat_list = _wa_find_chat_list(window)
        if not chat_list:
            return False
        target_lower = chat_name.strip().lower()
        for row in _ax_children(chat_list):
            role = _ax_attr(row, "AXRole")
            if role not in ("AXButton", "AXStaticText"):
                continue
            parsed = _wa_parse_row(row)
            if not parsed:
                continue
            if parsed[0].lower() == target_lower:
                # AXStaticText = already-selected row, no press needed
                if role == "AXButton":
                    AXUIElementPerformAction(row, "AXPress")
                return True
        return False
    except Exception:
        _log_service_failure("_whatsapp_select_chat", chat_name=chat_name)
        return False


def _wa_activate_app() -> bool:
    """Bring WhatsApp to front so synthesized keystrokes hit its window.
    Uses osascript because NSRunningApplication.activateWithOptions_ is unreliable
    from background Python processes on recent macOS.
    """
    try:
        import subprocess

        if not _whatsapp_pid():
            return False
        subprocess.run(
            ["osascript", "-e", 'tell application "WhatsApp" to activate'],
            check=False,
            timeout=3,
            capture_output=True,
        )
        return True
    except Exception:
        return False


def _cg_type_text(text: str) -> None:
    """Type a unicode string via synthesized keyboard events at HID level."""
    import Quartz

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    # Chunk into pieces CGEventKeyboardSetUnicodeString can carry (<= 20 chars per event)
    for i in range(0, len(text), 20):
        chunk = text[i : i + 20]
        down = Quartz.CGEventCreateKeyboardEvent(src, 0, True)
        Quartz.CGEventKeyboardSetUnicodeString(down, len(chunk), chunk)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        up = Quartz.CGEventCreateKeyboardEvent(src, 0, False)
        Quartz.CGEventKeyboardSetUnicodeString(up, len(chunk), chunk)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def _cg_press_key(keycode: int) -> None:
    import Quartz

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(src, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, keycode, False)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def whatsapp_send(chat_name: str, text: str) -> bool:
    """Send a WhatsApp message via AX + CGEvent.
    Activates app, selects chat, focuses compose field, types text via
    synthesized keystrokes (AXValue set is ignored by Catalyst), presses Return.
    """
    try:
        if not whatsapp_check_accessibility(prompt=False):
            return False
        if not text.strip():
            return False
        from ApplicationServices import AXUIElementSetAttributeValue

        if not _wa_activate_app():
            return False
        import time

        time.sleep(0.25)
        if not _whatsapp_select_chat(chat_name):
            return False
        time.sleep(0.6)
        window = _whatsapp_main_window()
        if not window:
            return False

        def is_compose(el: Any) -> bool:
            if _ax_attr(el, "AXRole") != "AXTextArea":
                return False
            return _wa_strip(_ax_attr(el, "AXDescription") or "") == "Compose message"

        areas = _ax_walk(window, is_compose, max_depth=25)
        if not areas:
            return False
        AXUIElementSetAttributeValue(areas[0], "AXFocused", True)
        time.sleep(0.2)

        _cg_type_text(text)
        time.sleep(0.25)
        _cg_press_key(36)  # Return
        return True
    except Exception:
        _log_service_failure("whatsapp_send", chat_name=chat_name)
        return False


def whatsapp_scroll_sidebar(pages: int = 1) -> int:
    """Scroll the conversation sidebar down N pages so more chats render.
    Returns number of pages actually scrolled (0 if unavailable)."""
    try:
        if not whatsapp_check_accessibility(prompt=False):
            return 0
        from ApplicationServices import AXUIElementPerformAction

        window = _whatsapp_main_window()
        if not window:
            return 0
        chat_list = _wa_find_chat_list(window)
        if not chat_list:
            return 0
        done = 0
        import time

        for _ in range(max(1, pages)):
            err = AXUIElementPerformAction(chat_list, "AXScrollDownByPage")
            if err != 0:
                break
            done += 1
            time.sleep(0.2)
        return done
    except Exception:
        _log_service_failure("whatsapp_scroll_sidebar")
        return 0


def whatsapp_contacts_all(max_pages: int = 10) -> list[Contact]:
    """Scroll the sidebar to the top, then page down collecting every visible chat.
    Deduplicates by name. Stops when a scroll yields no new chats or page cap hit.
    """
    try:
        if not whatsapp_check_accessibility(prompt=False):
            return []
        from ApplicationServices import AXUIElementPerformAction

        window = _whatsapp_main_window()
        if not window:
            return []
        chat_list = _wa_find_chat_list(window)
        if not chat_list:
            return []
        import time

        # Rewind to top
        for _ in range(max_pages):
            err = AXUIElementPerformAction(chat_list, "AXScrollUpByPage")
            if err != 0:
                break
            time.sleep(0.1)

        seen: dict[str, Contact] = {}
        stagnant = 0
        for _ in range(max_pages):
            for c in whatsapp_contacts(limit=200):
                if c.name and c.name not in seen:
                    seen[c.name] = c
            before = len(seen)
            err = AXUIElementPerformAction(chat_list, "AXScrollDownByPage")
            if err != 0:
                break
            time.sleep(0.3)
            for c in whatsapp_contacts(limit=200):
                if c.name and c.name not in seen:
                    seen[c.name] = c
            if len(seen) == before:
                stagnant += 1
                if stagnant >= 2:
                    break
            else:
                stagnant = 0
        return list(seen.values())
    except Exception:
        _log_service_failure("whatsapp_contacts_all")
        return []


def whatsapp_thread_full(chat_name: str, max_loads: int = 10, limit: int = 500) -> list[Msg]:
    """Fetch chat messages, repeatedly pressing 'Load more messages' for history.
    max_loads: cap on how many times to press the load-more button.
    """
    try:
        if not whatsapp_check_accessibility(prompt=False):
            return []
        from ApplicationServices import AXUIElementPerformAction

        if not _wa_activate_app():
            return []
        import time

        if not _whatsapp_select_chat(chat_name):
            return []
        time.sleep(0.6)

        for _ in range(max_loads):
            window = _whatsapp_main_window()
            if not window:
                break

            def is_load_more(el: Any) -> bool:
                if _ax_attr(el, "AXRole") != "AXStaticText":
                    return False
                return "Load more messages" in (_ax_attr(el, "AXDescription") or "")

            hits = _ax_walk(window, is_load_more, max_depth=25)
            if not hits:
                break
            # The clickable is typically a parent AXButton of the static text; press static too
            err = AXUIElementPerformAction(hits[0], "AXPress")
            if err != 0:
                # Try parent
                from ApplicationServices import AXUIElementCopyAttributeValue

                p_err, parent = AXUIElementCopyAttributeValue(hits[0], "AXParent", None)
                if parent is not None:
                    AXUIElementPerformAction(parent, "AXPress")
            time.sleep(0.8)

        return whatsapp_thread(chat_name, limit=limit)
    except Exception:
        _log_service_failure("whatsapp_thread_full", chat_name=chat_name)
        return []


def _wa_parse_message(desc: str, chat_name: str) -> tuple[str, str, datetime, bool] | None:
    """Parse an AXDescription for a message row.
    Format examples:
      'Message from <sender>, <body...>, <Month><DD>,at<HH:MM>, Received in <chat>'
      'Your message, <body...>, <Month><DD>,at<HH:MM>, Sent to <chat>'
    Returns (sender, body, ts, is_me) or None if not parseable.
    """
    import re
    from datetime import datetime as _dt

    s = _wa_strip(desc)
    if not s:
        return None
    # Anchor on the date+time token ", Month<DD>,at<HH>:<MM>"
    m = re.search(r",\s*([A-Za-z]+)\s*(\d{1,2}),at(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    head = s[: m.start()].strip()
    month_s, day_s, hh, mm = m.group(1), m.group(2), m.group(3), m.group(4)
    # Strip "Replying to X.\n" preamble (quoted-reply context)
    if head.startswith("Replying to "):
        nl = head.find("\n")
        if nl >= 0:
            head = head[nl + 1 :].strip()
    if head.startswith("Message from "):
        rest = head[len("Message from ") :]
        parts = rest.split(", ", 1)
        sender = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
        is_me = False
    elif head.startswith("Your message"):
        sender = "me"
        body = head[len("Your message") :].lstrip(", ").strip()
        is_me = True
    elif head.startswith("message, "):
        # Direct-chat received msg: "message, <body>"
        sender = chat_name
        body = head[len("message, ") :].strip()
        is_me = False
    else:
        return None
    try:
        now = _dt.now()
        dt = _dt.strptime(f"{month_s} {day_s} {hh}:{mm} {now.year}", "%B %d %H:%M %Y")
        if dt > now:
            dt = dt.replace(year=now.year - 1)
    except Exception:
        dt = _dt.now()
    return sender, body, dt, is_me


def whatsapp_thread(chat_name: str, limit: int = 50) -> list[Msg]:
    """Fetch visible WhatsApp messages for a conversation.
    Selects the chat in the sidebar, then scrapes AXStaticText rows whose
    AXDescription matches the WhatsApp message-row format. Only returns
    currently visible messages (no history scrollback).
    """
    try:
        if not whatsapp_check_accessibility(prompt=False):
            return []
        window = _whatsapp_main_window()
        if not window:
            return []

        _whatsapp_select_chat(chat_name)
        import time

        time.sleep(0.8)

        # Refresh window (selection may change tree)
        window = _whatsapp_main_window()
        if not window:
            return []

        def is_msg_text(el: Any) -> bool:
            if _ax_attr(el, "AXRole") != "AXStaticText":
                return False
            desc = _ax_attr(el, "AXDescription") or ""
            s = _wa_strip(desc)
            # Strip optional 'Replying to X.\n' preamble before matching
            if s.startswith("Replying to "):
                nl = s.find("\n")
                if nl >= 0:
                    s = s[nl + 1 :]
            return (
                s.startswith("Message from ")
                or s.startswith("Your message")
                or s.startswith("message, ")
            )

        nodes = _ax_walk(window, is_msg_text, max_depth=20)
        msgs: list[Msg] = []
        seen: set[str] = set()
        for n in nodes:
            desc = _ax_attr(n, "AXDescription") or ""
            parsed = _wa_parse_message(desc, chat_name)
            if not parsed:
                continue
            sender, body, ts, is_me = parsed
            key = f"{sender}|{body}|{ts.isoformat()}"
            if key in seen:
                continue
            seen.add(key)
            msgs.append(Msg(sender=sender, body=body, ts=ts, is_me=is_me, source="whatsapp"))
        msgs.sort(key=lambda m: m.ts)
        return msgs[-limit:]
    except Exception:
        _log_service_failure("whatsapp_thread", chat_name=chat_name)
        return []


# ── Apple Reminders ─────────────────────────────────────────────────────────


def _reminders_dbs() -> list[Path]:
    """Find all non-empty Reminders SQLite databases."""
    if not REMINDERS_DIR.exists():
        return []
    dbs = []
    for p in sorted(REMINDERS_DIR.glob("Data-*.sqlite")):
        count = _run_sqlite_read(
            p,
            "_reminders_dbs",
            lambda conn: conn.execute("SELECT COUNT(*) FROM ZREMCDREMINDER").fetchone()[0],
            empty_result=0,
        )
        if count > 0:
            dbs.append(p)
    return dbs


def reminders_lists() -> list[dict[str, str | int]]:
    """List all reminder lists with item counts."""
    lists: list[dict[str, str | int]] = []
    seen_names: set[str] = set()
    for db_path in _reminders_dbs():

        def _load_lists(conn: sqlite3.Connection) -> list[tuple[str, int]]:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT l.ZNAME, COUNT(r.Z_PK)
                FROM ZREMCDBASELIST l
                LEFT JOIN ZREMCDREMINDER r ON r.ZLIST = l.Z_PK
                    AND (r.ZMARKEDFORDELETION IS NULL OR r.ZMARKEDFORDELETION = 0)
                    AND r.ZCOMPLETED = 0
                WHERE (l.ZMARKEDFORDELETION IS NULL OR l.ZMARKEDFORDELETION = 0)
                    AND l.ZNAME IS NOT NULL
                GROUP BY l.ZNAME
                ORDER BY l.ZNAME
            """
            )
            return cur.fetchall()

        rows = _run_sqlite_read(
            db_path,
            "reminders_lists",
            _load_lists,
            empty_result=[],
        )
        for name, count in rows:
            if name and name not in seen_names:
                seen_names.add(name)
                lists.append({"name": name, "incomplete_count": count})
    return lists


def reminders_list(
    list_name: str | None = None,
    show_completed: bool = False,
    limit: int = 100,
) -> list[Reminder]:
    """List reminders, optionally filtered by list name."""
    reminders: list[Reminder] = []
    for db_path in _reminders_dbs():

        def _load_reminders(conn: sqlite3.Connection) -> list[Reminder]:
            cur = conn.cursor()
            query = """
                SELECT
                    r.Z_PK, r.ZTITLE, r.ZCOMPLETED, r.ZFLAGGED, r.ZPRIORITY,
                    r.ZDUEDATE, r.ZNOTES, r.ZCREATIONDATE,
                    COALESCE(l.ZNAME, '') as list_name
                FROM ZREMCDREMINDER r
                LEFT JOIN ZREMCDBASELIST l ON r.ZLIST = l.Z_PK
                WHERE (r.ZMARKEDFORDELETION IS NULL OR r.ZMARKEDFORDELETION = 0)
            """
            params: list = []
            if not show_completed:
                query += " AND r.ZCOMPLETED = 0"
            if list_name:
                query += " AND l.ZNAME = ?"
                params.append(list_name)
            query += " ORDER BY r.ZDUEDATE IS NULL, r.ZDUEDATE ASC, r.ZCREATIONDATE DESC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()
            db_reminders = []
            for (
                pk,
                title,
                completed,
                flagged,
                priority,
                due,
                notes,
                created,
                lname,
            ) in rows:
                due_dt = (APPLE_EPOCH + timedelta(seconds=due)) if due else None
                created_dt = (APPLE_EPOCH + timedelta(seconds=created)) if created else None
                db_reminders.append(
                    Reminder(
                        id=str(pk),
                        title=title or "(Untitled)",
                        completed=bool(completed),
                        list_name=lname,
                        due_date=due_dt,
                        notes=notes or "",
                        priority=priority or 0,
                        flagged=bool(flagged),
                        creation_date=created_dt,
                    )
                )
            return db_reminders

        reminders.extend(
            _run_sqlite_read(
                db_path,
                "reminders_list",
                _load_reminders,
                empty_result=[],
                list_name=list_name,
                show_completed=show_completed,
                limit=limit,
            )
        )
    return reminders


def reminder_by_id(reminder_id: str) -> Reminder | None:
    """Look up a single reminder by its SQLite Z_PK (id) across all reminder DBs.

    This avoids loading all reminders with a limit when we only need one.
    Returns None if the reminder is not found.
    """
    for db_path in _reminders_dbs():

        def _lookup(conn: sqlite3.Connection) -> Reminder | None:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    r.Z_PK, r.ZTITLE, r.ZCOMPLETED, r.ZFLAGGED, r.ZPRIORITY,
                    r.ZDUEDATE, r.ZNOTES, r.ZCREATIONDATE,
                    COALESCE(l.ZNAME, '') as list_name
                FROM ZREMCDREMINDER r
                LEFT JOIN ZREMCDBASELIST l ON r.ZLIST = l.Z_PK
                WHERE r.Z_PK = ?
                    AND (r.ZMARKEDFORDELETION IS NULL OR r.ZMARKEDFORDELETION = 0)
            """,
                (int(reminder_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            pk, title, completed, flagged, priority, due, notes, created, lname = row
            due_dt = (APPLE_EPOCH + timedelta(seconds=due)) if due else None
            created_dt = (APPLE_EPOCH + timedelta(seconds=created)) if created else None
            return Reminder(
                id=str(pk),
                title=title or "(Untitled)",
                completed=bool(completed),
                list_name=lname,
                due_date=due_dt,
                notes=notes or "",
                priority=priority or 0,
                flagged=bool(flagged),
                creation_date=created_dt,
            )

        result = _run_sqlite_read(
            db_path,
            "reminder_by_id",
            _lookup,
            empty_result=None,
            reminder_id=reminder_id,
        )
        if result is not None:
            return result
    return None


APPLESCRIPT_RETRIES = 2
APPLESCRIPT_RETRY_DELAY = 1.0  # seconds


def _run_applescript_with_retry(script: str, function_name: str, **log_context: object) -> bool:
    """Run an AppleScript command with retry logic for timing resilience.

    AppleScript operations on Reminders can fail due to timing issues
    (e.g., the Reminders database hasn't synced yet, or the app is busy).
    This helper retries up to APPLESCRIPT_RETRIES times with a short delay.

    Args:
        script: The AppleScript to execute.
        function_name: Name of the calling function (for logging).
        **log_context: Additional context for error logging.

    Returns:
        True if the script succeeded, False after all retries exhausted.
    """
    for attempt in range(APPLESCRIPT_RETRIES + 1):
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, timeout=10, text=True
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return True
            # AppleScript returned "fail" — retry if attempts remain
            if attempt < APPLESCRIPT_RETRIES:
                logger.debug(
                    f"{function_name} attempt {attempt + 1} failed, "
                    f"retrying in {APPLESCRIPT_RETRY_DELAY}s"
                )
                time.sleep(APPLESCRIPT_RETRY_DELAY)
                continue
        except Exception:
            if attempt < APPLESCRIPT_RETRIES:
                logger.debug(
                    f"{function_name} attempt {attempt + 1} raised exception, "
                    f"retrying in {APPLESCRIPT_RETRY_DELAY}s"
                )
                time.sleep(APPLESCRIPT_RETRY_DELAY)
                continue
            _log_service_failure(function_name, **log_context)
            return False
    _log_service_failure(function_name, **log_context)
    return False


def _applescript_find_reminder(title: str, list_name: str = "") -> str:
    """Build the AppleScript 'set theReminder to ...' clause with list_name disambiguation.

    When list_name is provided, the AppleScript matches on both name and
    container list name, which prevents matching the wrong reminder when
    duplicate titles exist across different lists.
    """
    safe_title = _escape_applescript(title)
    if list_name:
        safe_list = _escape_applescript(list_name)
        return (
            f"set theReminder to (first reminder whose name is ({safe_title}) "
            f"and completed is false and name of container is ({safe_list}))"
        )
    return (
        f"set theReminder to (first reminder whose name is ({safe_title}) and completed is false)"
    )


def reminder_complete(title: str, list_name: str = "") -> bool:
    """Mark a reminder as complete via AppleScript.

    Args:
        title: The title of the reminder to complete.
        list_name: Optional list name for disambiguation when duplicate titles exist.

    Returns:
        True if the completion succeeded, False otherwise.
    """
    find_clause = _applescript_find_reminder(title, list_name)
    script = f"""
    tell application "Reminders"
        try
            {find_clause}
            set completed of theReminder to true
            return "ok"
        on error
            return "fail"
        end try
    end tell
    """
    return _run_applescript_with_retry(
        script, "reminder_complete", title=title, list_name=list_name
    )


def reminder_uncomplete(title: str, list_name: str = "") -> bool:
    """Mark a reminder as incomplete via AppleScript.

    Args:
        title: The title of the reminder to uncomplete.
        list_name: Optional list name for disambiguation when duplicate titles exist.

    Returns:
        True if the operation succeeded, False otherwise.
    """
    find_clause = _applescript_find_reminder(title, list_name)
    script = f"""
    tell application "Reminders"
        try
            {find_clause}
            set completed of theReminder to false
            return "ok"
        on error
            return "fail"
        end try
    end tell
    """
    return _run_applescript_with_retry(
        script, "reminder_uncomplete", title=title, list_name=list_name
    )


def reminder_create(
    title: str,
    list_name: str = "Reminders",
    due_date: str = "",
    notes: str = "",
    priority: int = 0,
    flagged: bool = False,
) -> bool:
    """Create a new reminder via AppleScript.

    Args:
        title: The reminder title.
        list_name: The list name (defaults to "Reminders").
        due_date: Human-readable due date string (e.g., "April 15, 2025 02:00:00 PM").
        notes: Reminder notes/body text.
        priority: Priority level (0=none, 1=low, 5=medium, 9=high).
        flagged: Whether the reminder is flagged.

    Returns:
        True if creation succeeded, False otherwise.
    """
    safe_title = _escape_applescript(title)
    safe_notes = _escape_applescript(notes)
    safe_list = _escape_applescript(list_name)

    props = f"name:({safe_title})"
    if notes:
        props += f", body:({safe_notes})"

    # Build due date clause
    due_clause = ""
    if due_date:
        safe_due_date = _escape_applescript(due_date)
        due_clause = f"""
            set dStr to ({safe_due_date})
            set due date of theReminder to date dStr
        """

    # Build priority and flagged clauses
    priority_clause = ""
    flagged_clause = ""
    if priority != 0:
        priority_clause = f"set priority of theReminder to {priority}"
    if flagged:
        flagged_clause = "set flagged of theReminder to true"

    post_create = "\n        ".join(
        filter(None, [due_clause.strip(), priority_clause, flagged_clause])
    )

    script = f"""
    tell application "Reminders"
        try
            set targetList to list ({safe_list})
        on error
            set targetList to default list
        end try
        set theReminder to make new reminder in targetList with properties {{{props}}}
        {post_create}
        return "ok"
    end tell
    """
    return _run_applescript_with_retry(
        script,
        "reminder_create",
        title=title,
        list_name=list_name,
        due_date=due_date,
        notes_present=bool(notes),
        priority=priority,
        flagged=flagged,
    )


def reminder_edit(
    current_title: str,
    title: str | None = None,
    due_date: str | None = None,
    notes: str | None = None,
    list_name: str = "",
    priority: int | None = None,
    flagged: bool | None = None,
) -> bool:
    """Edit an existing reminder's properties via AppleScript.

    Args:
        current_title: The current title of the reminder to find.
        title: New title to set (or None to keep current).
        due_date: New due date string (or None to keep current).
        notes: New notes/body text (or None to keep current).
        list_name: Optional list name for disambiguation when duplicate titles exist.
        priority: New priority level (or None to keep current). 0=none, 1=low, 5=medium, 9=high.
        flagged: New flagged state (or None to keep current).

    Returns:
        True if the edit succeeded, False otherwise.
    """
    find_clause = _applescript_find_reminder(current_title, list_name)
    set_clauses: list[str] = []

    if title is not None:
        safe_new_title = _escape_applescript(title)
        set_clauses.append(f"set name of theReminder to ({safe_new_title})")

    if due_date is not None:
        safe_due_date = _escape_applescript(due_date)
        set_clauses.append(
            f"set dStr to ({safe_due_date})\n            set due date of theReminder to date dStr"
        )

    if notes is not None:
        safe_notes = _escape_applescript(notes)
        set_clauses.append(f"set body of theReminder to ({safe_notes})")

    if priority is not None:
        set_clauses.append(f"set priority of theReminder to {priority}")

    if flagged is not None:
        flagged_str = "true" if flagged else "false"
        set_clauses.append(f"set flagged of theReminder to {flagged_str}")

    set_block = "\n            ".join(set_clauses) if set_clauses else ""

    script = f"""
    tell application "Reminders"
        try
            {find_clause}
            {set_block}
            return "ok"
        on error
            return "fail"
        end try
    end tell
    """
    return _run_applescript_with_retry(
        script,
        "reminder_edit",
        current_title=current_title,
        new_title=title,
        due_date=due_date,
        notes_present=notes is not None,
        list_name=list_name,
        priority=priority,
        flagged=flagged,
    )


def reminder_delete(title: str, list_name: str = "") -> bool:
    """Delete a reminder via AppleScript.

    Args:
        title: The title of the reminder to delete.
        list_name: Optional list name for disambiguation when duplicate titles exist.

    Returns:
        True if the deletion succeeded, False otherwise.
    """
    find_clause = _applescript_find_reminder(title, list_name)
    script = f"""
    tell application "Reminders"
        try
            {find_clause}
            delete theReminder
            return "ok"
        on error
            return "fail"
        end try
    end tell
    """
    return _run_applescript_with_retry(script, "reminder_delete", title=title, list_name=list_name)


# ── Google Tasks ────────────────────────────────────────────────────────────


def tasks_lists(service) -> list[dict]:
    """List all Google Task lists."""
    try:
        result = service.tasklists().list(maxResults=50).execute()
        return result.get("items", [])
    except Exception:
        _log_service_failure("tasks_lists")
        return []


def tasks_list(
    service, list_id: str = "@default", show_completed: bool = False, limit: int = 100
) -> list[GoogleTask]:
    """List tasks in a Google Task list."""
    try:
        result = (
            service.tasks()
            .list(
                tasklist=list_id,
                showCompleted=show_completed,
                maxResults=limit,
            )
            .execute()
        )
        tasks = []
        list_title = ""

        # Get list title
        try:
            list_meta = service.tasklists().get(tasklist=list_id).execute()
            list_title = list_meta.get("title", list_id)
        except Exception:
            list_title = list_id

        for item in result.get("items", []):
            due = None
            if item.get("due"):
                with contextlib.suppress(Exception):
                    due = datetime.fromisoformat(item["due"].replace("Z", "+00:00"))

            completed = None
            if item.get("completed"):
                with contextlib.suppress(Exception):
                    completed = datetime.fromisoformat(item["completed"].replace("Z", "+00:00"))

            tasks.append(
                GoogleTask(
                    id=item["id"],
                    title=item.get("title", ""),
                    status=item.get("status", "needsAction"),
                    list_id=list_id,
                    list_title=list_title,
                    due=due,
                    notes=item.get("notes", ""),
                    completed=completed,
                )
            )
        return tasks
    except Exception:
        _log_service_failure("tasks_list", list_id=list_id)
        return []


def task_create(
    service,
    title: str,
    list_id: str = "@default",
    due: str = "",
    notes: str = "",
) -> bool:
    """Create a Google Task."""
    try:
        body = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = due
        service.tasks().insert(tasklist=list_id, body=body).execute()
        return True
    except Exception:
        _log_service_failure("task_create", title=title, list_id=list_id)
        return False


def task_complete(service, task_id: str, list_id: str = "@default") -> bool:
    """Mark a Google Task as complete."""
    try:
        service.tasks().patch(
            tasklist=list_id,
            task=task_id,
            body={"status": "completed"},
        ).execute()
        return True
    except Exception:
        _log_service_failure("task_complete", task_id=task_id, list_id=list_id)
        return False


def task_delete(service, task_id: str, list_id: str = "@default") -> bool:
    """Delete a Google Task."""
    try:
        service.tasks().delete(tasklist=list_id, task=task_id).execute()
        return True
    except Exception:
        _log_service_failure("task_delete", task_id=task_id, list_id=list_id)
        return False


def task_update(
    service,
    task_id: str,
    list_id: str = "@default",
    title: str | None = None,
    due: str | None = None,
    notes: str | None = None,
) -> bool:
    """Update a Google Task."""
    try:
        body = {}
        if title is not None:
            body["title"] = title
        if due is not None:
            body["due"] = due
        if notes is not None:
            body["notes"] = notes
        if not body:
            return True
        service.tasks().patch(
            tasklist=list_id,
            task=task_id,
            body=body,
        ).execute()
        return True
    except Exception:
        _log_service_failure("task_update", task_id=task_id, list_id=list_id)
        return False


# ── Gemini AI ──────────────────────────────────────────────────────────────


_gemini_model = None


def _get_gemini_model(model_name: str = "gemini-2.5-flash"):
    """Get or create a Gemini GenerativeModel instance (cached singleton)."""
    global _gemini_model
    if _gemini_model is not None:
        return _gemini_model
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key and GEMINI_API_KEY_FILE.exists():
        api_key = GEMINI_API_KEY_FILE.read_text().strip()
    if not api_key:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        _gemini_model = genai.GenerativeModel(model_name)
        return _gemini_model
    except Exception:
        _log_service_failure("_get_gemini_model")
        return None


def gemini_summarize(messages: list[dict], max_tokens: int = 300) -> str | None:
    """Summarize a conversation thread using Gemini.

    Args:
        messages: List of message dicts with 'sender', 'body', 'ts' keys.
        max_tokens: Max output length.

    Returns:
        Summary string, or None on failure.
    """
    model = _get_gemini_model()
    if not model:
        return None
    try:
        thread_text = "\n".join(
            f"[{m.get('sender', '?')}]: {m.get('body', '')[:500]}"
            for m in messages[-20:]  # Last 20 messages max
        )
        response = model.generate_content(
            f"Summarize this conversation thread concisely. Focus on key decisions, action items, and important information:\n\n{thread_text}",
            generation_config={"max_output_tokens": max_tokens, "temperature": 0.3},
        )
        return response.text.strip() if response.text else None
    except Exception:
        _log_service_failure("gemini_summarize", message_count=len(messages))
        return None


def gemini_smart_reply(messages: list[dict], num_replies: int = 3) -> list[str]:
    """Generate smart reply suggestions for a conversation.

    Args:
        messages: List of message dicts with 'sender', 'body', 'ts' keys.
        num_replies: Number of reply suggestions to generate.

    Returns:
        List of reply suggestion strings.
    """
    model = _get_gemini_model()
    if not model:
        return []
    try:
        thread_text = "\n".join(
            f"[{m.get('sender', '?')}]: {m.get('body', '')[:500]}" for m in messages[-10:]
        )
        response = model.generate_content(
            f"Based on this conversation, suggest {num_replies} brief, natural reply options. "
            f"Return ONLY the replies, one per line, numbered 1-{num_replies}. "
            f"Keep each reply under 2 sentences.\n\n{thread_text}",
            generation_config={"max_output_tokens": 200, "temperature": 0.7},
        )
        if not response.text:
            return []
        lines = [
            line.strip().lstrip("0123456789.-) ")
            for line in response.text.strip().split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        return [ln for ln in lines if ln][:num_replies]
    except Exception:
        _log_service_failure("gemini_smart_reply", message_count=len(messages))
        return []


def gemini_categorize(emails: list[dict]) -> list[dict]:
    """Categorize emails by priority and topic using Gemini.

    Args:
        emails: List of email dicts with 'name', 'snippet', 'id' keys.

    Returns:
        List of dicts: [{"id": str, "priority": "high"|"medium"|"low", "category": str}]
    """
    model = _get_gemini_model()
    if not model:
        return []
    try:
        email_text = "\n".join(
            f"ID:{e.get('id', '')} FROM:{e.get('name', '')} SNIPPET:{e.get('snippet', '')[:200]}"
            for e in emails[:30]  # Max 30 emails per batch
        )
        response = model.generate_content(
            "Categorize each email by priority (high/medium/low) and topic category "
            "(e.g., Finance, Work, Personal, Newsletter, Promotion, Urgent, Social). "
            "Return one line per email: ID|priority|category\n\n" + email_text,
            generation_config={"max_output_tokens": 500, "temperature": 0.2},
        )
        if not response.text:
            return []
        results = []
        for line in response.text.strip().split("\n"):
            parts = line.strip().split("|")
            if len(parts) >= 3:
                results.append(
                    {
                        "id": parts[0].strip(),
                        "priority": parts[1].strip().lower(),
                        "category": parts[2].strip(),
                    }
                )
        return results
    except Exception:
        _log_service_failure("gemini_categorize", email_count=len(emails))
        return []


def gemini_digest(
    emails: list[dict] | None = None,
    events: list[dict] | None = None,
    tasks: list[dict] | None = None,
    reminders: list[dict] | None = None,
    notifications: list[dict] | None = None,
) -> str | None:
    """Generate a morning digest/briefing across all sources using Gemini.

    Args:
        emails: Recent unread emails (name, snippet).
        events: Today's calendar events (summary, start, location).
        tasks: Pending Google Tasks (title, due).
        reminders: Active Apple Reminders (title, due_date).
        notifications: GitHub notifications (title, repo, type).

    Returns:
        Formatted briefing string, or None on failure.
    """
    model = _get_gemini_model()
    if not model:
        return None
    try:
        sections = []
        if emails:
            email_text = "\n".join(
                f"- {e.get('name', '?')}: {e.get('snippet', '')[:150]}" for e in emails[:15]
            )
            sections.append(f"UNREAD EMAILS ({len(emails)}):\n{email_text}")
        if events:
            event_text = "\n".join(
                f"- {e.get('start', '')[:16]} {e.get('summary', '')} @ {e.get('location', '')}"
                for e in events[:10]
            )
            sections.append(f"TODAY'S EVENTS ({len(events)}):\n{event_text}")
        if tasks:
            task_text = "\n".join(
                f"- {t.get('title', '')} (due: {t.get('due', '')})" for t in tasks[:10]
            )
            sections.append(f"PENDING TASKS ({len(tasks)}):\n{task_text}")
        if reminders:
            rem_text = "\n".join(
                f"- {r.get('title', '')} (due: {r.get('due_date', '')})" for r in reminders[:10]
            )
            sections.append(f"REMINDERS ({len(reminders)}):\n{rem_text}")
        if notifications:
            notif_text = "\n".join(
                f"- [{n.get('repo', '')}] {n.get('title', '')}" for n in notifications[:10]
            )
            sections.append(f"GITHUB ({len(notifications)}):\n{notif_text}")

        if not sections:
            return "Nothing pending — inbox zero! 🎉"

        combined = "\n\n".join(sections)
        response = model.generate_content(
            "You are a personal assistant. Generate a concise morning briefing from these items. "
            "Highlight what needs immediate attention, group related items, mention any conflicts or deadlines. "
            "Be direct and actionable. Use bullet points.\n\n" + combined,
            generation_config={"max_output_tokens": 500, "temperature": 0.4},
        )
        return response.text.strip() if response.text else None
    except Exception:
        _log_service_failure("gemini_digest")
        return None


def gemini_extract_action_items(messages: list[dict]) -> list[str]:
    """Extract action items from a conversation thread.

    Args:
        messages: List of message dicts.

    Returns:
        List of action item strings.
    """
    model = _get_gemini_model()
    if not model:
        return []
    try:
        thread_text = "\n".join(
            f"[{m.get('sender', '?')}]: {m.get('body', '')[:500]}" for m in messages[-15:]
        )
        response = model.generate_content(
            "Extract all action items, todos, and commitments from this conversation. "
            "Return one per line, starting with a dash. Only include concrete, actionable items.\n\n"
            + thread_text,
            generation_config={"max_output_tokens": 300, "temperature": 0.2},
        )
        if not response.text:
            return []
        return [
            line.strip().lstrip("- •")
            for line in response.text.strip().split("\n")
            if line.strip() and line.strip()[0] in "-•"
        ]
    except Exception:
        _log_service_failure("gemini_extract_action_items")
        return []


# ── Location ───────────────────────────────────────────────────────────────


def get_current_location() -> str | None:
    """Get current location via macOS Core Location.

    Returns lat,lng string (e.g. '37.5485,-121.9886') or None if unavailable.
    Requires Location Services permission for Terminal/Python.
    Falls back to INBOX_HOME_ADDRESS env var.
    """
    try:
        import CoreLocation  # type: ignore[import-untyped]

        manager = CoreLocation.CLLocationManager.alloc().init()
        manager.startUpdatingLocation()
        import time

        time.sleep(1)  # Give it a moment to get a fix
        location = manager.location()
        if location:
            coord = location.coordinate()
            manager.stopUpdatingLocation()
            return f"{coord.latitude},{coord.longitude}"
        manager.stopUpdatingLocation()
    except Exception:
        pass
    # Fallback to env var
    return os.environ.get("INBOX_HOME_ADDRESS", "").strip() or None


# ── Google Maps / Departure Times ──────────────────────────────────────────


def _google_cloud_api_key() -> str | None:
    """Get Google Cloud API key from env var or file. Single key for Maps, Translate, Vision, etc."""
    key = os.environ.get("GOOGLE_CLOUD_API_KEY", "").strip()
    if key:
        return key
    # Fallback to Maps-specific env var or file
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if key:
        return key
    if GOOGLE_MAPS_KEY_FILE.exists():
        key = GOOGLE_MAPS_KEY_FILE.read_text().strip()
        return key if key else None
    return None


@dataclass
class DepartureInfo:
    event_summary: str
    event_start: datetime
    event_location: str
    travel_minutes: int
    departure_time: datetime
    distance_text: str = ""
    duration_text: str = ""
    mode: str = "driving"


def maps_travel_time(
    origin: str,
    destination: str,
    mode: str = "driving",
    arrival_time: datetime | None = None,
    avoid: str | None = None,
    units: str = "imperial",
) -> dict | None:
    """Get travel time between two locations via Google Maps Distance Matrix API.

    Args:
        origin: Start address or lat,lng.
        destination: End address or lat,lng.
        mode: "driving" | "transit" | "walking" | "bicycling".
        arrival_time: Desired arrival time (for traffic estimates).
        avoid: "tolls" | "highways" | "ferries" | "indoor" or combo like "tolls|highways".
        units: "imperial" (miles) or "metric" (km). Default imperial.

    Returns:
        Dict with keys: duration_seconds, duration_text, distance_text, or None on failure.
    """
    api_key = _google_cloud_api_key()
    if not api_key:
        return None
    try:
        params = {
            "origins": origin,
            "destinations": destination,
            "mode": mode,
            "units": units,
            "key": api_key,
        }
        if avoid:
            params["avoid"] = avoid
        if arrival_time:
            # Google uses "arrival_time" param for transit, "departure_time" for driving
            if mode == "transit":
                params["arrival_time"] = str(int(arrival_time.timestamp()))
            else:
                # For driving: request departure_time=now to get traffic-aware estimate
                params["departure_time"] = "now"
        resp = httpx.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK":
            return None
        row = data.get("rows", [{}])[0]
        element = row.get("elements", [{}])[0]
        if element.get("status") != "OK":
            return None
        duration = element.get("duration_in_traffic") or element.get("duration", {})
        return {
            "duration_seconds": duration.get("value", 0),
            "duration_text": duration.get("text", ""),
            "distance_text": element.get("distance", {}).get("text", ""),
        }
    except Exception:
        _log_service_failure("maps_travel_time", origin=origin, destination=destination)
        return None


def departure_times_for_events(
    events: list,
    origin: str,
    mode: str = "driving",
    buffer_minutes: int = 10,
    lookahead_hours: int = 24,
) -> list[DepartureInfo]:
    """Calculate departure times for upcoming calendar events with locations.

    Args:
        events: List of CalendarEvent objects.
        origin: User's starting location (home address or lat,lng).
        mode: Travel mode for Distance Matrix API.
        buffer_minutes: Extra buffer to add to travel time.
        lookahead_hours: Only check events within this many hours.

    Returns:
        List of DepartureInfo for events that have locations.
    """
    now = datetime.now(UTC).astimezone()
    cutoff = now + timedelta(hours=lookahead_hours)
    results = []

    for event in events:
        location = getattr(event, "location", "") or ""
        if not location.strip():
            continue
        start = event.start if isinstance(event.start, datetime) else now
        if start.tzinfo is None:
            start = start.astimezone()
        if start < now or start > cutoff:
            continue

        travel = maps_travel_time(origin, location, mode=mode, arrival_time=start)
        if not travel:
            continue

        travel_minutes = travel["duration_seconds"] // 60
        departure = start - timedelta(minutes=travel_minutes + buffer_minutes)

        results.append(
            DepartureInfo(
                event_summary=event.summary,
                event_start=start,
                event_location=location,
                travel_minutes=travel_minutes,
                departure_time=departure,
                distance_text=travel.get("distance_text", ""),
                duration_text=travel.get("duration_text", ""),
                mode=mode,
            )
        )

    results.sort(key=lambda d: d.departure_time)
    return results


# ── GitHub ──────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"


def _github_token() -> str | None:
    """Get GitHub token — tries gh CLI first, then falls back to file."""
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:  # logged below
        _log_service_failure("_github_token", token_file=str(GITHUB_TOKEN_FILE))
    if GITHUB_TOKEN_FILE.exists():
        return GITHUB_TOKEN_FILE.read_text().strip()
    return None


def _github_headers() -> dict[str, str]:
    token = _github_token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_notifications(all_notifs: bool = False) -> list[GitHubNotification]:
    """Fetch GitHub notifications."""
    headers = _github_headers()
    if not headers:
        return []
    try:
        params = {"all": "true"} if all_notifs else {}
        resp = httpx.get(f"{_GITHUB_API}/notifications", headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        notifs = []
        for n in resp.json():
            subject = n.get("subject", {})
            repo = n.get("repository", {}).get("full_name", "")
            updated = n.get("updated_at", "")
            ts = (
                datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if updated
                else datetime.now()
            )
            # Build a web URL from the API URL
            api_url = subject.get("url", "")
            web_url = ""
            if api_url:
                web_url = api_url.replace("api.github.com/repos", "github.com").replace(
                    "/pulls/", "/pull/"
                )
            notifs.append(
                GitHubNotification(
                    id=n.get("id", ""),
                    title=subject.get("title", ""),
                    repo=repo,
                    type=subject.get("type", ""),
                    reason=n.get("reason", ""),
                    unread=n.get("unread", False),
                    updated_at=ts,
                    url=web_url,
                )
            )
        return notifs
    except Exception:  # logged below
        _log_service_failure("github_notifications", all_notifs=all_notifs)
        return []


def github_mark_read(notification_id: str) -> bool:
    """Mark a single GitHub notification as read."""
    headers = _github_headers()
    if not headers:
        return False
    try:
        resp = httpx.patch(
            f"{_GITHUB_API}/notifications/threads/{notification_id}",
            headers=headers,
            timeout=10,
        )
        return resp.status_code in (200, 205)
    except Exception:  # logged below
        _log_service_failure("github_mark_read", notification_id=notification_id)
        return False


def github_mark_all_read() -> bool:
    """Mark all GitHub notifications as read."""
    headers = _github_headers()
    if not headers:
        return False
    try:
        resp = httpx.put(
            f"{_GITHUB_API}/notifications",
            headers=headers,
            json={"last_read_at": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")},
            timeout=10,
        )
        return resp.status_code in (200, 202, 205)
    except Exception:  # logged below
        _log_service_failure("github_mark_all_read")
        return False


def github_pulls(repo: str | None = None) -> list[dict]:
    """Fetch pull requests assigned to or requesting review from the user."""
    headers = _github_headers()
    if not headers:
        return []
    try:
        # Search for PRs involving the authenticated user
        query = "is:pr is:open review-requested:@me"
        if repo:
            query += f" repo:{repo}"
        resp = httpx.get(
            f"{_GITHUB_API}/search/issues",
            headers=headers,
            params={"q": query, "sort": "updated", "per_page": "30"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "id": item["id"],
                "number": item["number"],
                "title": item["title"],
                "repo": item.get("repository_url", "").split("/repos/")[-1],
                "user": item.get("user", {}).get("login", ""),
                "state": item["state"],
                "url": item["html_url"],
                "updated_at": item["updated_at"],
            }
            for item in items
        ]
    except Exception:  # logged below
        _log_service_failure("github_pulls", repo=repo)
        return []


# ── Google Drive ────────────────────────────────────────────────────────────


def drive_files(
    drive_service,
    query: str = "",
    limit: int = 20,
    shared_with_me: bool = False,
    folder_id: str = "",
) -> list[DriveFile]:
    """List files from Google Drive."""
    try:
        q_parts = []
        if shared_with_me:
            q_parts.append("sharedWithMe = true")
        if query:
            q_parts.append(f"name contains '{query}'")
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        q_parts.append("trashed = false")
        q = " and ".join(q_parts)

        result = (
            drive_service.files()
            .list(
                q=q,
                pageSize=limit,
                fields="files(id, name, mimeType, modifiedTime, size, shared, webViewLink, parents)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = []
        for f in result.get("files", []):
            mod = f.get("modifiedTime", "")
            ts = datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else datetime.now()
            files.append(
                DriveFile(
                    id=f["id"],
                    name=f.get("name", ""),
                    mime_type=f.get("mimeType", ""),
                    modified=ts,
                    size=int(f.get("size", 0)),
                    shared=f.get("shared", False),
                    web_link=f.get("webViewLink", ""),
                    parents=f.get("parents", []),
                )
            )
        return files
    except Exception:  # logged below
        _log_service_failure(
            "drive_files",
            query=query,
            limit=limit,
            shared_with_me=shared_with_me,
        )
        return []


def drive_upload(
    drive_service,
    file_path: str,
    folder_id: str = "",
    name: str = "",
) -> DriveFile | None:
    """Upload a file to Google Drive."""
    from googleapiclient.http import MediaFileUpload

    p = Path(file_path)
    if not p.exists():
        return None
    try:
        mime_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        metadata: dict = {"name": name or p.name}
        if folder_id:
            metadata["parents"] = [folder_id]

        media = MediaFileUpload(str(p), mimetype=mime_type, resumable=True)
        result = (
            drive_service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, name, mimeType, modifiedTime, size, webViewLink",
            )
            .execute()
        )
        mod = result.get("modifiedTime", "")
        ts = datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else datetime.now()
        return DriveFile(
            id=result["id"],
            name=result.get("name", ""),
            mime_type=result.get("mimeType", ""),
            modified=ts,
            size=int(result.get("size", 0)),
            web_link=result.get("webViewLink", ""),
        )
    except Exception:  # logged below
        _log_service_failure(
            "drive_upload",
            file_path=file_path,
            folder_id=folder_id,
            name=name or p.name,
        )
        return None


def drive_create_folder(drive_service, name: str, parent_id: str = "") -> DriveFile | None:
    """Create a folder in Google Drive."""
    try:
        metadata: dict = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]
        result = (
            drive_service.files()
            .create(body=metadata, fields="id, name, mimeType, modifiedTime, webViewLink")
            .execute()
        )
        mod = result.get("modifiedTime", "")
        ts = datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else datetime.now()
        return DriveFile(
            id=result["id"],
            name=result.get("name", ""),
            mime_type=result.get("mimeType", ""),
            modified=ts,
            web_link=result.get("webViewLink", ""),
        )
    except Exception:  # logged below
        _log_service_failure("drive_create_folder", name=name, parent_id=parent_id)
        return None


def drive_delete(drive_service, file_id: str) -> bool:
    """Delete (trash) a file from Google Drive."""
    try:
        drive_service.files().update(fileId=file_id, body={"trashed": True}).execute()
        return True
    except Exception:  # logged below
        _log_service_failure("drive_delete", file_id=file_id)
        return False


def drive_get(drive_service, file_id: str) -> DriveFile | None:
    """Get metadata for a single Drive file."""
    try:
        f = (
            drive_service.files()
            .get(
                fileId=file_id,
                fields="id, name, mimeType, modifiedTime, size, shared, webViewLink, parents",
            )
            .execute()
        )
        mod = f.get("modifiedTime", "")
        ts = datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else datetime.now()
        return DriveFile(
            id=f["id"],
            name=f.get("name", ""),
            mime_type=f.get("mimeType", ""),
            modified=ts,
            size=int(f.get("size", 0)),
            shared=f.get("shared", False),
            web_link=f.get("webViewLink", ""),
            parents=f.get("parents", []),
        )
    except Exception:  # logged below
        _log_service_failure("drive_get", file_id=file_id)
        return None


def drive_download(drive_service, file_id: str) -> tuple[bytes, str] | None:
    """Download file content from Google Drive.

    Returns a (content_bytes, mime_type) tuple, or None on error.
    For Google Workspace files (Docs, Sheets, etc.) that cannot be
    downloaded directly, exports as PDF.
    """
    from io import BytesIO

    from googleapiclient.http import MediaIoBaseDownload

    try:
        # First get metadata to know the mime type
        meta = drive_service.files().get(fileId=file_id, fields="mimeType, name").execute()
        mime_type = meta.get("mimeType", "application/octet-stream")

        # Google Workspace files need export, not direct download
        google_export_map = {
            "application/vnd.google-apps.document": "application/pdf",
            "application/vnd.google-apps.spreadsheet": "application/pdf",
            "application/vnd.google-apps.presentation": "application/pdf",
            "application/vnd.google-apps.drawing": "application/pdf",
        }

        buf = BytesIO()
        if mime_type in google_export_map:
            export_mime = google_export_map[mime_type]
            request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime)
            mime_type = export_mime
        else:
            request = drive_service.files().get_media(fileId=file_id)

        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        return buf.getvalue(), mime_type
    except Exception:  # logged below
        _log_service_failure("drive_download", file_id=file_id)
        return None


# ── Sheets ──────────────────────────────────────────────────────────────────


def sheets_list(
    drive_service: object, query: str = "", limit: int = 20, account: str = ""
) -> list[Spreadsheet]:
    """List spreadsheets from Drive. Returns list of Spreadsheet, empty on error."""
    try:
        q = "mimeType='application/vnd.google-apps.spreadsheet'"
        if query:
            q += f" and name contains '{query}'"
        result = (
            drive_service.files()
            .list(q=q, pageSize=limit, fields="files(id, name, modifiedTime, webViewLink, owners)")
            .execute()
        )
        spreadsheets = []
        for file in result.get("files", []):
            spreadsheets.append(
                Spreadsheet(
                    id=file["id"],
                    title=file.get("name", ""),
                    url=file.get("webViewLink", ""),
                    sheets=[],
                    account=account,
                )
            )
        return spreadsheets
    except Exception:  # logged below
        _log_service_failure("sheets_list", query=query)
        return []


def sheets_get(sheets_service: object, spreadsheet_id: str) -> Spreadsheet | None:
    """Get spreadsheet metadata including sheet tabs."""
    try:
        result = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets_list = []
        for sheet in result.get("sheets", []):
            props = sheet.get("properties", {})
            sheets_list.append(
                SheetTab(
                    sheet_id=props.get("sheetId", 0),
                    title=props.get("title", ""),
                    index=props.get("index", 0),
                    row_count=props.get("gridProperties", {}).get("rowCount", 0),
                    col_count=props.get("gridProperties", {}).get("columnCount", 0),
                )
            )
        return Spreadsheet(
            id=result.get("spreadsheetId", spreadsheet_id),
            title=result.get("properties", {}).get("title", ""),
            url=result.get("spreadsheetUrl", ""),
            sheets=sheets_list,
        )
    except Exception:  # logged below
        _log_service_failure("sheets_get", spreadsheet_id=spreadsheet_id)
        return None


def sheets_create(
    sheets_service: object, title: str, sheets: list[str] | None = None
) -> Spreadsheet | None:
    """Create a new spreadsheet with optional sheet tabs."""
    try:
        requests = []
        body = {
            "properties": {"title": title},
            "sheets": [{"properties": {"title": sheets[0] if sheets else "Sheet1"}}],
        }
        if sheets and len(sheets) > 1:
            for i, sheet_title in enumerate(sheets[1:], start=1):
                requests.append({"addSheet": {"properties": {"title": sheet_title, "index": i}}})

        result = sheets_service.spreadsheets().create(body=body).execute()
        spreadsheet_id = result.get("spreadsheetId", "")

        if requests:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": requests}
            ).execute()

        return sheets_get(sheets_service, spreadsheet_id)
    except Exception:  # logged below
        _log_service_failure("sheets_create", title=title)
        return None


def sheets_delete(drive_service: object, spreadsheet_id: str) -> bool:
    """Soft-delete (trash) a spreadsheet."""
    try:
        drive_service.files().update(fileId=spreadsheet_id, body={"trashed": True}).execute()
        return True
    except Exception:  # logged below
        _log_service_failure("sheets_delete", spreadsheet_id=spreadsheet_id)
        return False


def sheets_values_get(
    sheets_service: object, spreadsheet_id: str, range_: str
) -> list[list] | None:
    """Read a range from a spreadsheet. Returns list[list] or None on error."""
    try:
        result = (
            sheets_service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_)
            .execute()
        )
        return result.get("values", [])
    except Exception:  # logged below
        _log_service_failure("sheets_values_get", spreadsheet_id=spreadsheet_id, range_=range_)
        return None


def sheets_values_batch_get(
    sheets_service: object, spreadsheet_id: str, ranges: list[str]
) -> dict[str, list[list]] | None:
    """Read multiple ranges. Returns dict[range: list[list]] or None on error."""
    try:
        result = (
            sheets_service.spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
            .execute()
        )
        output = {}
        for value_range in result.get("valueRanges", []):
            range_key = value_range.get("range", "")
            output[range_key] = value_range.get("values", [])
        return output
    except Exception:  # logged below
        _log_service_failure("sheets_values_batch_get", spreadsheet_id=spreadsheet_id)
        return None


def sheets_values_update(
    sheets_service: object,
    spreadsheet_id: str,
    range_: str,
    values: list[list],
    value_input: str = "USER_ENTERED",
) -> dict | None:
    """Update a range with values. Returns update stats or None on error."""
    try:
        result = (
            sheets_service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_,
                valueInputOption=value_input,
                body={"values": values},
            )
            .execute()
        )
        return result
    except Exception:  # logged below
        _log_service_failure("sheets_values_update", spreadsheet_id=spreadsheet_id, range_=range_)
        return None


def sheets_values_batch_update(
    sheets_service: object,
    spreadsheet_id: str,
    data: list[dict],
    value_input: str = "USER_ENTERED",
) -> dict | None:
    """Update multiple ranges. data = [{"range": "...", "values": [...]}, ...]."""
    try:
        result = (
            sheets_service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "valueInputOption": value_input,
                    "data": data,
                },
            )
            .execute()
        )
        return result
    except Exception:  # logged below
        _log_service_failure("sheets_values_batch_update", spreadsheet_id=spreadsheet_id)
        return None


def sheets_values_append(
    sheets_service: object,
    spreadsheet_id: str,
    range_: str,
    values: list[list],
    value_input: str = "USER_ENTERED",
) -> dict | None:
    """Append rows to a range. Returns append stats or None on error."""
    try:
        result = (
            sheets_service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_,
                valueInputOption=value_input,
                body={"values": values},
            )
            .execute()
        )
        return result
    except Exception:  # logged below
        _log_service_failure("sheets_values_append", spreadsheet_id=spreadsheet_id, range_=range_)
        return None


def sheets_values_clear(sheets_service: object, spreadsheet_id: str, range_: str) -> bool:
    """Clear a range."""
    try:
        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range_
        ).execute()
        return True
    except Exception:  # logged below
        _log_service_failure("sheets_values_clear", spreadsheet_id=spreadsheet_id, range_=range_)
        return False


def sheets_add_sheet(
    sheets_service: object,
    spreadsheet_id: str,
    title: str,
    rows: int = 1000,
    cols: int = 26,
) -> SheetTab | None:
    """Add a new sheet tab to a spreadsheet."""
    try:
        result = (
            sheets_service.spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": title,
                                    "gridProperties": {"rowCount": rows, "columnCount": cols},
                                }
                            }
                        }
                    ]
                },
            )
            .execute()
        )
        # Extract new sheet info from reply
        reply = result.get("replies", [{}])[0]
        props = reply.get("addSheet", {}).get("properties", {})
        return SheetTab(
            sheet_id=props.get("sheetId", 0),
            title=props.get("title", ""),
            index=props.get("index", 0),
            row_count=props.get("gridProperties", {}).get("rowCount", 0),
            col_count=props.get("gridProperties", {}).get("columnCount", 0),
        )
    except Exception:  # logged below
        _log_service_failure("sheets_add_sheet", spreadsheet_id=spreadsheet_id, title=title)
        return None


def sheets_delete_sheet(sheets_service: object, spreadsheet_id: str, sheet_id: int) -> bool:
    """Delete a sheet tab by sheet_id."""
    try:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
        ).execute()
        return True
    except Exception:  # logged below
        _log_service_failure(
            "sheets_delete_sheet", spreadsheet_id=spreadsheet_id, sheet_id=sheet_id
        )
        return False


def sheets_rename_sheet(
    sheets_service: object, spreadsheet_id: str, sheet_id: int, new_title: str
) -> bool:
    """Rename a sheet tab."""
    try:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "title": new_title},
                            "fields": "title",
                        }
                    }
                ]
            },
        ).execute()
        return True
    except Exception:  # logged below
        _log_service_failure(
            "sheets_rename_sheet", spreadsheet_id=spreadsheet_id, sheet_id=sheet_id
        )
        return False


def sheets_format(sheets_service: object, spreadsheet_id: str, requests: list[dict]) -> dict | None:
    """Apply formatting via raw batchUpdate requests. For max flexibility."""
    try:
        result = (
            sheets_service.spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            )
            .execute()
        )
        return result
    except Exception:  # logged below
        _log_service_failure("sheets_format", spreadsheet_id=spreadsheet_id)
        return None


def sheets_copy_to(
    sheets_service: object, spreadsheet_id: str, sheet_id: int, dest_spreadsheet_id: str
) -> SheetTab | None:
    """Copy a sheet to another spreadsheet."""
    try:
        result = (
            sheets_service.spreadsheets()
            .sheets()
            .copyTo(
                spreadsheetId=spreadsheet_id,
                sheetId=sheet_id,
                body={"destinationSpreadsheetId": dest_spreadsheet_id},
            )
            .execute()
        )
        props = result.get("properties", {})
        return SheetTab(
            sheet_id=props.get("sheetId", 0),
            title=props.get("title", ""),
            index=props.get("index", 0),
            row_count=props.get("gridProperties", {}).get("rowCount", 0),
            col_count=props.get("gridProperties", {}).get("columnCount", 0),
        )
    except Exception:  # logged below
        _log_service_failure(
            "sheets_copy_to",
            spreadsheet_id=spreadsheet_id,
            sheet_id=sheet_id,
            dest_id=dest_spreadsheet_id,
        )
        return None


# ── Docs ─────────────────────────────────────────────────────────────────────


def docs_list(
    drive_service: object, query: str = "", limit: int = 20, account: str = ""
) -> list[Document]:
    """List documents from Drive. Returns list of Document, empty on error."""
    try:
        q = "mimeType='application/vnd.google-apps.document'"
        if query:
            q += f" and name contains '{query}'"
        result = (
            drive_service.files()
            .list(q=q, pageSize=limit, fields="files(id, name, modifiedTime, webViewLink, owners)")
            .execute()
        )
        documents = []
        for file in result.get("files", []):
            documents.append(
                Document(
                    id=file["id"],
                    title=file.get("name", ""),
                    url=file.get("webViewLink", ""),
                    account=account,
                )
            )
        return documents
    except Exception:  # logged below
        _log_service_failure("docs_list", query=query)
        return []


def docs_get(docs_service: object, document_id: str) -> Document | None:
    """Get document metadata and content."""
    try:
        result = docs_service.documents().get(documentId=document_id).execute()
        return Document(
            id=result.get("documentId", document_id),
            title=result.get("title", ""),
            url=f"https://docs.google.com/document/d/{document_id}/edit",
        )
    except Exception:  # logged below
        _log_service_failure("docs_get", document_id=document_id)
        return None


def docs_create(docs_service: object, title: str) -> Document | None:
    """Create a new Google Doc."""
    try:
        result = docs_service.documents().create(body={"title": title}).execute()
        document_id = result.get("documentId", "")
        return Document(
            id=document_id,
            title=result.get("title", title),
            url=f"https://docs.google.com/document/d/{document_id}/edit",
        )
    except Exception:  # logged below
        _log_service_failure("docs_create", title=title)
        return None


def docs_delete(drive_service: object, document_id: str) -> bool:
    """Soft-delete (trash) a document."""
    try:
        drive_service.files().update(fileId=document_id, body={"trashed": True}).execute()
        return True
    except Exception:  # logged below
        _log_service_failure("docs_delete", document_id=document_id)
        return False


def docs_export(
    drive_service: object, document_id: str, mime_type: str = "text/plain"
) -> bytes | None:
    """Export document content. Supports: text/plain, application/pdf, text/html."""
    try:
        response = drive_service.files().export(fileId=document_id, mimeType=mime_type).execute()
        return response
    except Exception:  # logged below
        _log_service_failure("docs_export", document_id=document_id, mime_type=mime_type)
        return None


def docs_insert_text(docs_service: object, document_id: str, text: str, index: int = 1) -> bool:
    """Insert text into a document at specified index."""
    try:
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "text": text,
                            "location": {"index": index},
                        }
                    }
                ]
            },
        ).execute()
        return True
    except Exception:  # logged below
        _log_service_failure("docs_insert_text", document_id=document_id)
        return False


def docs_get_text(docs_service: object, document_id: str) -> str | None:
    """Get plain text content of a document."""
    try:
        result = docs_service.documents().get(documentId=document_id).execute()
        text_parts = []
        for elem in result.get("body", {}).get("content", []):
            if "paragraph" in elem:
                for run in elem["paragraph"].get("elements", []):
                    if "textRun" in run:
                        text_parts.append(run["textRun"].get("content", ""))
            elif "table" in elem:
                # Basic table extraction
                for row in elem["table"].get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        for content in cell.get("content", []):
                            if "paragraph" in content:
                                for run in content["paragraph"].get("elements", []):
                                    if "textRun" in run:
                                        text_parts.append(run["textRun"].get("content", ""))
        return "".join(text_parts)
    except Exception:  # logged below
        _log_service_failure("docs_get_text", document_id=document_id)
        return None


# ── Whisper / Audio Config ──────────────────────────────────────────────────

# MLX Whisper for chunk-based ambient transcription
MLX_WHISPER_MODEL = "mlx-community/whisper-base.en-mlx"

# whisper-stream C++ binary for real-time dictation
WHISPER_STREAM_BIN = "/opt/homebrew/bin/whisper-stream"
WHISPER_STREAM_MODEL = (
    "/opt/homebrew/Cellar/whisper-cpp/1.8.4/share/whisper-cpp/ggml-base.en-q8_0.bin"
)

# Audio settings
SAMPLE_RATE = 16000
CHUNK_SECS = 5  # ambient: transcribe every N seconds
SILENCE_RMS_THRESHOLD = 0.01  # skip chunks below this RMS

# Vocabulary prompt — biases whisper toward these technical terms
VOCAB_PROMPT = (
    "Claude Code, mlx-lm, mlx-whisper, Outlines, Qwen, Textual, "
    "Ghostty, Raycast, AeroSpace, sketchybar, FastAPI, inbox"
)


def whisper_stream_available() -> bool:
    """Check if the whisper-stream binary and model are available."""
    return Path(WHISPER_STREAM_BIN).exists() and Path(WHISPER_STREAM_MODEL).exists()


def mlx_whisper_available() -> bool:
    """Check if mlx_whisper is importable."""
    try:
        import importlib.util

        return importlib.util.find_spec("mlx_whisper") is not None
    except Exception:
        return False


def sounddevice_available() -> bool:
    """Check if sounddevice is importable."""
    try:
        import importlib.util

        return importlib.util.find_spec("sounddevice") is not None
    except Exception:
        return False


def ambient_available() -> tuple[bool, str]:
    """Return (available, reason). Reason is empty when available=True."""
    if not sounddevice_available():
        return False, "sounddevice not installed"
    if not mlx_whisper_available():
        return False, "mlx_whisper not installed"
    return True, ""


# ── Ambient Service ─────────────────────────────────────────────────────────

MIN_CHUNK_WORDS = 10
FLUSH_INTERVAL = 60  # seconds between extraction passes
TRANSCRIPT_MAXLEN = 200  # max segments kept in rolling transcript


class AmbientService:
    """Background ambient transcription service."""

    def __init__(self, on_note: Callable[[str, str | None], None]):
        self._on_note = on_note
        self._buffer: list[str] = []
        self._buffer_lock = threading.Lock()
        self._transcript: list[str] = []  # rolling transcript segments
        self._transcript_lock = threading.Lock()
        self._running = False
        self._capture_thread: threading.Thread | None = None
        self._flush_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @is_running.setter
    def is_running(self, value: bool) -> None:
        self._running = value

    def get_transcript(self, max_segments: int = 50) -> list[str]:
        """Return recent transcript segments (newest last)."""
        with self._transcript_lock:
            return list(self._transcript[-max_segments:])

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._capture_thread.start()
        self._flush_thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._process_buffer()

    def _capture_loop(self) -> None:
        import mlx_whisper
        import numpy as np
        import sounddevice as sd

        while self._running:
            try:
                audio = sd.rec(
                    int(CHUNK_SECS * SAMPLE_RATE),
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                )
                sd.wait()

                if not self._running:
                    break

                audio_flat = audio.flatten()
                rms = float(np.sqrt(np.mean(audio_flat**2)))
                if rms < SILENCE_RMS_THRESHOLD:
                    continue

                result = mlx_whisper.transcribe(
                    audio_flat, path_or_hf_repo=MLX_WHISPER_MODEL, language="en"
                )
                text = result.get("text", "").strip()  # type: ignore[union-attr]
                if text:
                    with self._buffer_lock:
                        self._buffer.append(text)
                    with self._transcript_lock:
                        self._transcript.append(text)
                        if len(self._transcript) > TRANSCRIPT_MAXLEN:
                            self._transcript = self._transcript[-TRANSCRIPT_MAXLEN:]

            except Exception:  # logged below
                _log_service_failure(
                    "AmbientService._capture_loop",
                    chunk_secs=CHUNK_SECS,
                    sample_rate=SAMPLE_RATE,
                )
                time.sleep(1)

    def _flush_loop(self) -> None:
        while self._running:
            time.sleep(FLUSH_INTERVAL)
            if self._running:
                self._process_buffer()

    def _process_buffer(self) -> None:
        with self._buffer_lock:
            if not self._buffer:
                return
            chunk = " ".join(self._buffer)
            self._buffer.clear()

        if len(chunk.split()) < MIN_CHUNK_WORDS:
            return

        summary = None
        try:
            summary = extract_summary(chunk)
        except Exception:  # logged below
            _log_service_failure(
                "AmbientService._process_buffer",
                chunk_words=len(chunk.split()),
            )

        self._on_note(chunk, summary)


# ── Dictation Service ───────────────────────────────────────────────────────


def _type_text(text: str) -> None:
    """Inject text at current cursor position via macOS CGEvent."""
    import Quartz

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)  # type: ignore[attr-defined]
    for char in text:
        down = Quartz.CGEventCreateKeyboardEvent(src, 0, True)  # type: ignore[attr-defined]
        up = Quartz.CGEventCreateKeyboardEvent(src, 0, False)  # type: ignore[attr-defined]
        Quartz.CGEventKeyboardSetUnicodeString(down, 1, char)  # type: ignore[attr-defined]
        Quartz.CGEventKeyboardSetUnicodeString(up, 1, char)  # type: ignore[attr-defined]
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)  # type: ignore[attr-defined]
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)  # type: ignore[attr-defined]
        time.sleep(0.001)


def _clean_line(line: str) -> str:
    """Strip whisper-stream ANSI codes and timestamp markers."""
    line = re.sub(r"\x1b\[[0-9;]*m", "", line)
    line = re.sub(r"\[\d+:\d+:\d+\.\d+ --> \d+:\d+:\d+\.\d+\s*\]", "", line)
    return line.strip()


class DictationService:
    """Background dictation service — streams ASR to keyboard."""

    def __init__(self) -> None:
        self._running = False
        self._available: bool | None = None
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._last_text = ""

    @property
    def is_running(self) -> bool:
        return self._running

    @is_running.setter
    def is_running(self, value: bool) -> None:
        self._running = value

    @property
    def available(self) -> bool:
        return whisper_stream_available() if self._available is None else self._available

    @available.setter
    def available(self, value: bool) -> None:
        self._available = value

    def start(self) -> None:
        if self._running:
            return
        if not self.available:
            raise RuntimeError(f"whisper-stream not found at {WHISPER_STREAM_BIN}")
        self._running = True
        self._last_text = ""
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._proc:
            self._proc.terminate()
            self._proc = None

    def _stream_loop(self) -> None:
        self._proc = subprocess.Popen(
            [
                WHISPER_STREAM_BIN,
                "-m",
                WHISPER_STREAM_MODEL,
                "--language",
                "en",
                "--step",
                "500",
                "--length",
                "5000",
                "--keep",
                "200",
                "--prompt",
                VOCAB_PROMPT,
                "--no-timestamps",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        try:
            assert self._proc.stdout is not None
            for raw_line in self._proc.stdout:
                if not self._running:
                    break

                text = _clean_line(raw_line)
                if not text:
                    continue

                if text.startswith(self._last_text):
                    new_part = text[len(self._last_text) :].lstrip()
                elif self._last_text and self._last_text in text:
                    new_part = text[text.index(self._last_text) + len(self._last_text) :].lstrip()
                else:
                    new_part = text

                if new_part:
                    _type_text(new_part + " ")

                self._last_text = text

        except Exception:  # logged below
            _log_service_failure(
                "DictationService._stream_loop",
                binary_path=WHISPER_STREAM_BIN,
                model_path=WHISPER_STREAM_MODEL,
            )
        finally:
            if self._proc:
                self._proc.terminate()
                self._proc = None
            self._running = False


# ── LLM Engine ──────────────────────────────────────────────────────────────

MLX_MODEL = "mlx-community/Qwen3.5-0.8B-MLX-4bit"

# Large model: configurable via env var, defaults to a compact 3B MLX model
MLX_LARGE_MODEL = os.environ.get(
    "INBOX_LLM_LARGE",
    "mlx-community/Qwen2.5-3B-Instruct-4bit",
)

_llm_lock = threading.Lock()
_llm_model: object | None = None
_llm_tokenizer: object | None = None

_llm_large_lock = threading.Lock()
_llm_large_model: object | None = None
_llm_large_tokenizer: object | None = None
_llm_large_loading: bool = False


def _ensure_llm_loaded() -> None:
    """Load small model + tokenizer once. Thread-safe."""
    global _llm_model, _llm_tokenizer
    if _llm_model is not None:
        return
    with _llm_lock:
        if _llm_model is not None:
            return
        import mlx_lm

        _llm_model, _llm_tokenizer = mlx_lm.load(MLX_MODEL)[:2]  # type: ignore[assignment]


def _ensure_large_llm_loaded() -> bool:
    """Load large model lazily. Returns True if loaded, False if unavailable."""
    global _llm_large_model, _llm_large_tokenizer, _llm_large_loading
    if _llm_large_model is not None:
        return True
    with _llm_large_lock:
        if _llm_large_model is not None:
            return True
        _llm_large_loading = True
        try:
            import mlx_lm

            _llm_large_model, _llm_large_tokenizer = mlx_lm.load(MLX_LARGE_MODEL)[:2]  # type: ignore[assignment]
            return True
        except Exception:
            return False
        finally:
            _llm_large_loading = False


def get_outlines_model() -> object:
    """Return an Outlines-wrapped model for constrained generation."""
    _ensure_llm_loaded()
    import outlines

    return outlines.models.mlxlm(MLX_MODEL)  # type: ignore[attr-defined]


def get_large_outlines_model() -> object | None:
    """Return an Outlines-wrapped large model, or None if unavailable."""
    if not _ensure_large_llm_loaded():
        return None
    import outlines

    try:
        return outlines.models.mlxlm(MLX_LARGE_MODEL)  # type: ignore[attr-defined]
    except Exception:
        return None


def llm_complete(prompt: str, max_tokens: int = 64, temperature: float = 0.7) -> str:
    """Free-form text completion using the small model."""
    _ensure_llm_loaded()
    import mlx_lm
    from mlx_lm.sample_utils import make_sampler

    return mlx_lm.generate(
        _llm_model,  # type: ignore[arg-type]
        _llm_tokenizer,  # type: ignore[arg-type]
        prompt=prompt,
        max_tokens=max_tokens,
        sampler=make_sampler(temp=temperature),
    )


def llm_large_complete(prompt: str, max_tokens: int = 256, temperature: float = 0.3) -> str | None:
    """Free-form text completion using the large model. Returns None if unavailable."""
    if not _ensure_large_llm_loaded():
        return None
    import mlx_lm
    from mlx_lm.sample_utils import make_sampler

    return mlx_lm.generate(
        _llm_large_model,  # type: ignore[arg-type]
        _llm_large_tokenizer,  # type: ignore[arg-type]
        prompt=prompt,
        max_tokens=max_tokens,
        sampler=make_sampler(temp=temperature),
    )


def generate_json(prompt: str, schema: type[PydanticBaseModel]) -> PydanticBaseModel:
    """Generate constrained JSON matching a Pydantic schema using small model."""
    import outlines

    model = get_outlines_model()
    generator = outlines.generate.json(model, schema)  # type: ignore[attr-defined]
    return generator(prompt)


def generate_json_large(prompt: str, schema: type[PydanticBaseModel]) -> PydanticBaseModel | None:
    """Generate constrained JSON using large model. Returns None if unavailable."""
    model = get_large_outlines_model()
    if model is None:
        return None
    import outlines

    generator = outlines.generate.json(model, schema)  # type: ignore[attr-defined]
    return generator(prompt)


def llm_is_loaded() -> bool:
    """Check if the small LLM model is loaded."""
    return _llm_model is not None


def llm_large_is_loaded() -> bool:
    """Check if the large LLM model is loaded."""
    return _llm_large_model is not None


def llm_large_is_loading() -> bool:
    """Check if the large model is currently being loaded."""
    return _llm_large_loading


def llm_warmup() -> None:
    """Pre-load the small model."""
    _ensure_llm_loaded()


# ── LLM Extraction ─────────────────────────────────────────────────────────

try:
    from pydantic import BaseModel as _PydanticBase
except ImportError:  # fallback for test environments without pydantic
    _PydanticBase = object  # type: ignore[assignment, misc]

EXTRACT_PROMPT = (
    "Extract structured information from this spoken note. "
    "key_points: main ideas stated. action_items: things to do. topics: subjects mentioned. "
    "Use empty lists if nothing relevant.\n\nText: {text}"
)


class Extraction(_PydanticBase):  # type: ignore[misc]
    key_points: list[str]
    action_items: list[str]
    topics: list[str]


def extract(text: str) -> Extraction:
    """Extract key points, action items, and topics from transcript text."""
    return generate_json(EXTRACT_PROMPT.format(text=text), Extraction)  # type: ignore[return-value]


def extract_summary(text: str) -> str | None:
    """Extract and format as a single-line summary. Returns None if nothing useful."""
    result = extract(text)
    parts = []
    if result.key_points:
        parts.append("; ".join(result.key_points))
    if result.action_items:
        parts.append("\u2192 " + "; ".join(result.action_items))
    return " | ".join(parts) if parts else None


# ── LLM Autocomplete ───────────────────────────────────────────────────────

AUTOCOMPLETE_PROMPT = """\
Complete the user's reply naturally. Output ONLY the completion text, nothing else.

Recent messages:
{context}

User is typing: {draft}"""

REPLY_PROMPT = """\
Suggest a brief reply to the last message. Output ONLY the reply text, nothing else.

Conversation:
{context}

Received: {last_message}

Reply:"""


# ── Global Search ────────────────────────────────────────────────────────────

SEARCH_SNIPPET_LEN = 150


def _make_snippet(text: str, query: str, max_len: int = SEARCH_SNIPPET_LEN) -> str:
    """Return a short snippet with the match region, up to max_len chars."""
    if not text:
        return ""
    lower = text.lower()
    idx = lower.find(query.lower())
    if idx == -1:
        return text[:max_len]
    start = max(0, idx - 40)
    end = min(len(text), start + max_len)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _search_imessage(query: str, limit: int) -> list[dict]:
    if not IMSG_DB.exists():
        return []
    q = f"%{query}%"

    def _run(conn: sqlite3.Connection) -> list[dict]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                m.rowid, m.text,
                m.date / 1000000000 + 978307200 as ts,
                cmj.chat_id
            FROM message m
            JOIN chat_message_join cmj ON m.rowid = cmj.message_id
            WHERE m.text LIKE ? AND m.text IS NOT NULL
            ORDER BY m.rowid DESC LIMIT ?
            """,
            (q, limit),
        )
        rows = cur.fetchall()
        results = []
        for _rowid, text, ts_raw, chat_id in rows:
            body = _clean_body(text)
            if not body:
                continue
            ts_dt = datetime.fromtimestamp(ts_raw) if ts_raw else datetime.now()
            results.append(
                {
                    "source": "imessage",
                    "id": str(chat_id),
                    "title": f"iMessage chat {chat_id}",
                    "snippet": _make_snippet(body, query),
                    "timestamp": ts_dt.isoformat(),
                    "metadata": {"chat_id": str(chat_id)},
                }
            )
        return results

    return _run_sqlite_read(IMSG_DB, "_search_imessage", _run, empty_result=[], query=query)


def _search_gmail(gmail_services: dict, query: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for account_email, svc in gmail_services.items():
        try:
            resp = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
            messages = resp.get("messages", [])
            if not messages:
                continue
            metadata = _fetch_gmail_metadata_batch(svc, [m["id"] for m in messages])
            for m in messages:
                msg = metadata.get(m["id"])
                if not msg:
                    continue
                payload = msg.get("payload", {})
                headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
                subject = headers.get("Subject", "(no subject)")
                raw_from = headers.get("From", "")
                display_name, _ = _parse_email_address(raw_from)
                ts_ms = int(msg.get("internalDate", 0))
                ts_dt = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime.now()
                thread_id = m.get("threadId", m["id"])
                results.append(
                    {
                        "source": "gmail",
                        "id": m["id"],
                        "title": subject,
                        "snippet": _make_snippet(subject, query),
                        "timestamp": ts_dt.isoformat(),
                        "metadata": {
                            "thread_id": thread_id,
                            "from": display_name,
                            "account": account_email,
                        },
                    }
                )
        except Exception:
            _log_service_failure("_search_gmail", account=account_email, query=query)
    return results


def _search_notes(query: str, limit: int) -> list[dict]:
    if not NOTES_DB.exists():
        return []
    q = f"%{query}%"

    def _run(conn: sqlite3.Connection) -> list[dict]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT n.Z_PK, n.ZTITLE1, n.ZSNIPPET, n.ZMODIFICATIONDATE1
            FROM ZICCLOUDSYNCINGOBJECT n
            WHERE n.ZTITLE1 IS NOT NULL
              AND (n.ZMARKEDFORDELETION IS NULL OR n.ZMARKEDFORDELETION = 0)
              AND (n.ZTITLE1 LIKE ? OR n.ZSNIPPET LIKE ?)
            ORDER BY n.ZMODIFICATIONDATE1 DESC LIMIT ?
            """,
            (q, q, limit),
        )
        rows = cur.fetchall()
        results = []
        for pk, title, snippet, mod_date in rows:
            ts = APPLE_EPOCH + timedelta(seconds=mod_date) if mod_date else datetime.now()
            text = f"{title} {snippet or ''}"
            results.append(
                {
                    "source": "notes",
                    "id": str(pk),
                    "title": title or "(Untitled)",
                    "snippet": _make_snippet(text, query),
                    "timestamp": ts.isoformat(),
                    "metadata": {},
                }
            )
        return results

    return _run_sqlite_read(NOTES_DB, "_search_notes", _run, empty_result=[], query=query)


def _search_reminders(query: str, limit: int) -> list[dict]:
    q = f"%{query}%"
    results: list[dict] = []
    for db_path in _reminders_dbs():

        def _run(conn: sqlite3.Connection) -> list[dict]:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT r.Z_PK, r.ZTITLE, r.ZNOTES, r.ZDUEDATE, r.ZCREATIONDATE,
                       COALESCE(l.ZNAME, '') as list_name
                FROM ZREMCDREMINDER r
                LEFT JOIN ZREMCDBASELIST l ON r.ZLIST = l.Z_PK
                WHERE (r.ZMARKEDFORDELETION IS NULL OR r.ZMARKEDFORDELETION = 0)
                  AND r.ZCOMPLETED = 0
                  AND (r.ZTITLE LIKE ? OR r.ZNOTES LIKE ?)
                ORDER BY r.ZDUEDATE IS NULL, r.ZDUEDATE ASC LIMIT ?
                """,
                (q, q, limit),
            )
            rows = cur.fetchall()
            items = []
            for pk, title, notes, due, created, list_name in rows:
                ts_raw = created or due
                ts = (APPLE_EPOCH + timedelta(seconds=ts_raw)) if ts_raw else datetime.now()
                text = f"{title or ''} {notes or ''}"
                items.append(
                    {
                        "source": "reminders",
                        "id": str(pk),
                        "title": title or "(Untitled)",
                        "snippet": _make_snippet(text, query),
                        "timestamp": ts.isoformat(),
                        "metadata": {"list_name": list_name},
                    }
                )
            return items

        results.extend(
            _run_sqlite_read(db_path, "_search_reminders", _run, empty_result=[], query=query)
        )
    return results


def _search_calendar(cal_services: dict, query: str, limit: int) -> list[dict]:
    q = query.lower()
    results: list[dict] = []
    now = datetime.now().astimezone()
    time_min = (now - timedelta(days=30)).isoformat()
    time_max = (now + timedelta(days=180)).isoformat()
    for account_email, svc in cal_services.items():
        try:
            cal_list = svc.calendarList().list().execute()  # type: ignore[attr-defined]
            for cal_entry in cal_list.get("items", []):
                cal_id = cal_entry["id"]
                resp = (
                    svc.events()  # type: ignore[attr-defined]
                    .list(
                        calendarId=cal_id,
                        q=query,
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        maxResults=limit,
                        orderBy="startTime",
                    )
                    .execute()
                )
                for item in resp.get("items", []):
                    summary = item.get("summary", "(No title)")
                    description = item.get("description", "")
                    text = f"{summary} {description}"
                    if q not in text.lower():
                        continue
                    start_raw = item.get("start", {})
                    start_str = start_raw.get("dateTime") or start_raw.get("date", "")
                    try:
                        ts_dt = datetime.fromisoformat(start_str)
                    except (ValueError, TypeError):
                        ts_dt = datetime.now()
                    results.append(
                        {
                            "source": "calendar",
                            "id": item.get("id", ""),
                            "title": summary,
                            "snippet": _make_snippet(text, query),
                            "timestamp": ts_dt.isoformat(),
                            "metadata": {
                                "calendar_id": cal_id,
                                "account": account_email,
                                "location": item.get("location", ""),
                            },
                        }
                    )
        except Exception:
            _log_service_failure("_search_calendar", account=account_email, query=query)
    return results


def search_all(
    query: str,
    sources: list[str],
    limit: int = 50,
    gmail_services: dict | None = None,
    cal_services: dict | None = None,
    from_addr: str = "",
    before: str = "",
    after: str = "",
    has_attachment: bool = False,
    is_unread: bool = False,
) -> dict:
    """Search across all requested sources.  Returns ranked results dict.

    Filters (optional):
        from_addr: sender filter — prepended as from:X to Gmail query, filtered post-hoc for iMessage
        before: ISO date — upper bound cutoff (Gmail: before:YYYY/MM/DD; SQLite: ts filter)
        after: ISO date — lower bound cutoff (Gmail: after:YYYY/MM/DD; SQLite: ts filter)
        has_attachment: Gmail only — prepends has:attachment to Gmail query
        is_unread: Gmail only — prepends is:unread to Gmail query
    """
    if not query or not query.strip():
        return {"query": query, "total": 0, "results": []}

    gmail_services = gmail_services or {}
    cal_services = cal_services or {}

    want_all = "all" in sources
    results: list[dict] = []

    # Build enhanced gmail query with filter modifiers
    gmail_query = query
    gmail_modifiers = []
    if from_addr:
        gmail_modifiers.append(f"from:{from_addr}")
    if has_attachment:
        gmail_modifiers.append("has:attachment")
    if is_unread:
        gmail_modifiers.append("is:unread")
    if before:
        # Gmail uses YYYY/MM/DD format
        try:
            before_dt = datetime.fromisoformat(before)
            gmail_modifiers.append(f"before:{before_dt.strftime('%Y/%m/%d')}")
        except Exception:
            pass
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
            gmail_modifiers.append(f"after:{after_dt.strftime('%Y/%m/%d')}")
        except Exception:
            pass
    if gmail_modifiers:
        gmail_query = " ".join(gmail_modifiers) + " " + query

    if want_all or "imessage" in sources:
        results.extend(_search_imessage(query, limit))

    if want_all or "gmail" in sources:
        results.extend(_search_gmail(gmail_services, gmail_query, limit))

    if want_all or "notes" in sources:
        results.extend(_search_notes(query, limit))

    if want_all or "reminders" in sources:
        results.extend(_search_reminders(query, limit))

    if want_all or "calendar" in sources:
        results.extend(_search_calendar(cal_services, query, limit))

    # Post-hoc filters for non-Gmail sources (datetime range + from_addr for imessage)
    before_dt = None
    after_dt = None
    if before:
        with contextlib.suppress(Exception):
            before_dt = datetime.fromisoformat(before)
    if after:
        with contextlib.suppress(Exception):
            after_dt = datetime.fromisoformat(after)

    def _keep(r: dict) -> bool:
        ts = r.get("timestamp", "")
        if ts and (before_dt or after_dt):
            try:
                rts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                # Strip tz for naive comparison
                if rts.tzinfo:
                    rts = rts.replace(tzinfo=None)
                if before_dt and rts > before_dt:
                    return False
                if after_dt and rts < after_dt:
                    return False
            except Exception:
                pass
        # from_addr filter for non-gmail sources
        if from_addr and r.get("source") in ("imessage", "calendar"):
            sender = (r.get("sender") or "").lower()
            if from_addr.lower() not in sender:
                return False
        return True

    results = [r for r in results if _keep(r)]

    # Rank by timestamp desc (most recent first)
    results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    results = results[:limit]

    return {"query": query, "total": len(results), "results": results}


def build_context(messages: list[dict], max_messages: int = 6) -> str:  # type: ignore[type-arg]
    """Format recent messages into context string."""
    recent = messages[-max_messages:]
    lines = []
    for msg in recent:
        sender = msg.get("sender", "?")
        body = msg.get("body", "").strip()
        if len(body) > 200:
            body = body[:200] + "..."
        lines.append(f"{sender}: {body}")
    return "\n".join(lines)


def autocomplete(
    draft: str = "",
    messages: list[dict] | None = None,  # type: ignore[type-arg]
    max_tokens: int = 32,
    temperature: float = 0.5,
    mode: str = "complete",
) -> str | None:
    """Suggest a completion or reply for the draft text."""
    if mode not in ("complete", "reply"):
        raise ValueError(f"Invalid mode: {mode!r}. Must be 'complete' or 'reply'.")

    if mode == "complete":
        if len(draft.strip()) < 3:
            return None
        context = build_context(messages) if messages else ""
        prompt = AUTOCOMPLETE_PROMPT.format(context=context, draft=draft)
        result = llm_complete(prompt, max_tokens=max_tokens, temperature=temperature)
        result = result.strip()
        return result if result else None

    # reply mode
    if not messages:
        return None
    last = messages[-1]
    last_body = last.get("body", "").strip()
    if not last_body:
        return None
    context = build_context(messages[:-1]) if len(messages) > 1 else ""
    prompt = REPLY_PROMPT.format(context=context, last_message=last_body)
    result = llm_complete(prompt, max_tokens=max_tokens, temperature=temperature)
    result = result.strip()
    return result if result else None


# ── Desktop Notifications ─────────────────────────────────────────────────────

NOTIFICATION_CONFIG_PATH = Path.home() / ".config" / "inbox" / "notifications.json"

_DEFAULT_NOTIFICATION_CONFIG: dict = {
    "enabled": True,
    "sources": {
        "imessage": True,
        "gmail": True,
        "calendar": True,
        "github": True,
        "reminders": True,
    },
    "quiet_hours": {
        "enabled": False,
        "start": "22:00",
        "end": "08:00",
    },
}


def load_notification_config() -> dict:  # type: ignore[type-arg]
    """Load notification config, creating defaults if missing."""
    if not NOTIFICATION_CONFIG_PATH.exists():
        NOTIFICATION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        NOTIFICATION_CONFIG_PATH.write_text(json.dumps(_DEFAULT_NOTIFICATION_CONFIG, indent=2))
        return dict(_DEFAULT_NOTIFICATION_CONFIG)
    try:
        data = json.loads(NOTIFICATION_CONFIG_PATH.read_text())
        # Fill in any missing keys from defaults
        cfg = dict(_DEFAULT_NOTIFICATION_CONFIG)
        cfg.update(data)
        if "sources" in data:
            sources = dict(_DEFAULT_NOTIFICATION_CONFIG["sources"])
            sources.update(data["sources"])
            cfg["sources"] = sources
        if "quiet_hours" in data:
            qh = dict(_DEFAULT_NOTIFICATION_CONFIG["quiet_hours"])
            qh.update(data["quiet_hours"])
            cfg["quiet_hours"] = qh
        return cfg
    except Exception:
        return dict(_DEFAULT_NOTIFICATION_CONFIG)


def save_notification_config(cfg: dict) -> bool:  # type: ignore[type-arg]
    """Persist notification config. Returns True on success."""
    try:
        NOTIFICATION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        NOTIFICATION_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        return True
    except Exception:
        _log_service_failure("save_notification_config")
        return False


def _in_quiet_hours(quiet_hours: dict) -> bool:  # type: ignore[type-arg]
    """Return True if current time falls within the configured quiet-hours window."""
    if not quiet_hours.get("enabled"):
        return False
    try:
        now_str = datetime.now().strftime("%H:%M")
        start = quiet_hours.get("start", "22:00")
        end = quiet_hours.get("end", "08:00")
        # Handle overnight range (e.g. 22:00 – 08:00)
        if start <= end:
            return start <= now_str < end
        # Overnight: quiet if now >= start OR now < end
        return now_str >= start or now_str < end
    except Exception:
        return False


def send_notification(title: str, body: str, source: str = "") -> bool:
    """Send a macOS desktop notification. Returns True if sent.

    Uses UNUserNotificationCenter via pyobjc if available;
    falls back to osascript so tests/CI without pyobjc still work.
    Respects the notification config (enabled flag, per-source toggle, quiet hours).
    """
    cfg = load_notification_config()
    if not cfg.get("enabled", True):
        return False
    if source and not cfg.get("sources", {}).get(source, True):
        return False
    if _in_quiet_hours(cfg.get("quiet_hours", {})):
        return False

    # Try pyobjc UNUserNotificationCenter first
    try:
        import importlib

        objc_mod = importlib.import_module("objc")  # noqa: F841
        un_mod = importlib.import_module("UserNotifications")
        UNUserNotificationCenter = un_mod.UNUserNotificationCenter
        UNMutableNotificationContent = un_mod.UNMutableNotificationContent
        UNNotificationRequest = un_mod.UNNotificationRequest

        center = UNUserNotificationCenter.currentNotificationCenter()
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        req = UNNotificationRequest.requestWithIdentifier_content_trigger_(
            f"inbox-{time.time()}", content, None
        )
        center.addNotificationRequest_withCompletionHandler_(req, None)
        return True
    except Exception:
        pass

    # Fallback: osascript
    try:
        safe_title = title.replace("'", "\\'")
        safe_body = body.replace("'", "\\'")
        script = f"display notification '{safe_body}' with title '{safe_title}'"
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        _log_service_failure("send_notification", title=title, source=source)
        return False


# ── Contacts search / profile ────────────────────────────────────────────────

FAVORITES_FILE = Path.home() / ".config" / "inbox" / "favorites.json"


def load_favorites() -> set[str]:
    """Load favorited contact IDs from disk."""
    try:
        if FAVORITES_FILE.exists():
            import json

            return set(json.loads(FAVORITES_FILE.read_text()))
    except Exception:
        pass
    return set()


def save_favorites(ids: set[str]) -> None:
    """Persist favorited contact IDs to disk."""
    import json

    FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    FAVORITES_FILE.write_text(json.dumps(sorted(ids)))


def contacts_search(
    gmail_services: dict[str, object],
    q: str,
    limit: int = 20,
) -> list[dict]:
    """Search contacts by name, email, or phone across AddressBook + Gmail + iMessage senders."""
    q_lower = q.lower().strip()
    results: dict[str, dict] = {}  # keyed by normalized identifier

    # 1. Search AddressBook
    from contacts import _addressbook_paths, _phone_variants

    for db_path in _addressbook_paths():
        try:
            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ZABCDRECORD'")
            if not cur.fetchone():
                conn.close()
                continue
            cur.execute("""
                SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION,
                    GROUP_CONCAT(DISTINCT p.ZFULLNUMBER) as phones,
                    GROUP_CONCAT(DISTINCT e.ZADDRESS) as emails
                FROM ZABCDRECORD r
                LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE (r.ZFIRSTNAME IS NOT NULL OR r.ZLASTNAME IS NOT NULL OR r.ZORGANIZATION IS NOT NULL)
                GROUP BY r.Z_PK
            """)
            for _pk, first, last, org, phones_raw, emails_raw in cur.fetchall():
                parts = [p for p in (first, last) if p]
                name = " ".join(parts) if parts else (org or "")
                if not name:
                    continue
                emails = [e.strip() for e in (emails_raw or "").split(",") if e.strip()]
                phones = [p.strip() for p in (phones_raw or "").split(",") if p.strip()]

                # Check if matches query
                searchable = name.lower() + " " + " ".join(emails).lower() + " " + " ".join(phones)
                if q_lower and q_lower not in searchable:
                    continue

                # Use first email as key, fall back to first phone
                key = emails[0].lower() if emails else (phones[0] if phones else name.lower())
                if key not in results:
                    results[key] = {
                        "id": key,
                        "name": name,
                        "emails": emails,
                        "phones": phones,
                        "github_handle": "",
                        "photo_url": "",
                        "source_counts": {"imessage": 0, "gmail": 0, "calendar": 0},
                    }
            conn.close()
        except Exception:
            continue

    # 2. Add Gmail senders
    for account_email, svc in gmail_services.items():
        try:
            contacts = gmail_contacts(svc, account_email, limit=50)
            for c in contacts:
                addr = c.reply_to.lower()
                if not addr:
                    continue
                if q_lower and q_lower not in (c.name.lower() + " " + addr):
                    continue
                if addr not in results:
                    results[addr] = {
                        "id": addr,
                        "name": c.name,
                        "emails": [c.reply_to],
                        "phones": [],
                        "github_handle": "",
                        "photo_url": "",
                        "source_counts": {"imessage": 0, "gmail": 0, "calendar": 0},
                    }
                results[addr]["source_counts"]["gmail"] += 1
        except Exception:
            continue

    # 3. Add iMessage senders
    try:
        imsg_convs = imsg_contacts(limit=100)
        for c in imsg_convs:
            if not c.members:
                continue
            for member in c.members:
                member_lower = member.lower()
                if q_lower and q_lower not in (c.name.lower() + " " + member_lower):
                    continue
                # Try to find by email or phone variants
                matched_key = None
                for variant in _phone_variants(member):
                    if variant.lower() in results:
                        matched_key = variant.lower()
                        break
                if member_lower in results:
                    matched_key = member_lower
                if matched_key:
                    results[matched_key]["source_counts"]["imessage"] += 1
                else:
                    # New entry from iMessage
                    key = member_lower
                    if key not in results:
                        results[key] = {
                            "id": key,
                            "name": c.name if not c.is_group else member,
                            "emails": [],
                            "phones": [member] if "@" not in member else [],
                            "github_handle": "",
                            "photo_url": "",
                            "source_counts": {"imessage": 1, "gmail": 0, "calendar": 0},
                        }
    except Exception:
        pass

    return list(results.values())[:limit]


def contacts_profile(
    contact_id: str,
    gmail_services: dict[str, object],
    cal_services: dict[str, object],
) -> dict:
    """Aggregate cross-source profile for a contact."""

    # Find the contact via search
    matches = contacts_search(gmail_services, contact_id, limit=50)
    contact = next(
        (m for m in matches if m["id"] == contact_id or contact_id in m["emails"]),
        None,
    )
    if not contact:
        # Minimal fallback
        contact = {
            "id": contact_id,
            "name": contact_id,
            "emails": [contact_id] if "@" in contact_id else [],
            "phones": [contact_id] if "@" not in contact_id else [],
            "github_handle": "",
            "photo_url": "",
            "source_counts": {"imessage": 0, "gmail": 0, "calendar": 0},
        }

    emails = set(e.lower() for e in contact.get("emails", []))
    name_lower = contact.get("name", "").lower()

    # iMessages — find matching chat
    imsg_recent: list[dict] = []
    try:
        all_imsg = imsg_contacts(limit=200)
        for c in all_imsg:
            if any(m.lower() in emails or name_lower in m.lower() for m in c.members):
                msgs = imsg_thread(c.id, limit=10)
                for m in msgs[:10]:
                    imsg_recent.append(
                        {
                            "source": "imessage",
                            "sender": m.sender,
                            "body": m.body[:200],
                            "ts": m.ts.isoformat(),
                            "is_me": m.is_me,
                        }
                    )
                contact["source_counts"]["imessage"] += len(msgs)
                break
    except Exception:
        pass

    # Gmail threads
    gmail_recent: list[dict] = []
    for account_email, svc in gmail_services.items():
        try:
            convs = gmail_contacts(svc, account_email, limit=50)
            for c in convs:
                if c.reply_to.lower() in emails or name_lower in c.name.lower():
                    gmail_recent.append(
                        {
                            "source": "gmail",
                            "sender": c.name,
                            "body": c.snippet[:200],
                            "ts": c.last_ts.isoformat(),
                            "thread_id": c.thread_id,
                        }
                    )
                    contact["source_counts"]["gmail"] += 1
                    if len(gmail_recent) >= 10:
                        break
        except Exception:
            continue

    # Calendar events (last 30 + next 30 days)
    cal_events: list[dict] = []
    if cal_services:
        try:
            now = datetime.now()
            start_dt = now - timedelta(days=30)
            end_dt = now + timedelta(days=30)
            events = calendar_events(
                cal_services,
                start_date=start_dt,
                end_date=end_dt,
            )
            for ev in events:
                attendees = ev.attendees
                if any(
                    a.get("email", "").lower() in emails or name_lower in a.get("name", "").lower()
                    for a in attendees
                ):
                    cal_events.append(
                        {
                            "source": "calendar",
                            "summary": ev.summary,
                            "start": ev.start.isoformat(),
                            "end": ev.end.isoformat(),
                            "location": ev.location,
                        }
                    )
                    contact["source_counts"]["calendar"] += 1
        except Exception:
            pass

    # Build unified timeline (reverse-chron)
    timeline: list[dict] = []
    for item in imsg_recent + gmail_recent + cal_events:
        ts_str = item.get("ts") or item.get("start", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = datetime.min
        timeline.append({**item, "_ts": ts.isoformat()})
    timeline.sort(key=lambda x: x["_ts"], reverse=True)
    for item in timeline:
        item.pop("_ts", None)

    return {
        "contact": contact,
        "imessages": imsg_recent,
        "gmail_threads": gmail_recent,
        "calendar_events": cal_events,
        "timeline": timeline,
    }


# ── AI Briefing ─────────────────────────────────────────────────────────────

BRIEFING_PROMPT = """\
You are a helpful assistant. Summarize the user's day in 2-3 sentences based on this data:

Calendar events today: {events}
Pending reminders: {reminders}
Unread messages: {unread_imsg} iMessage, {unread_gmail} Gmail
GitHub notifications: {gh_unread} unread, {gh_prs} PRs awaiting review

Write a concise, friendly morning briefing paragraph."""


def ai_briefing(
    events: list[dict],  # type: ignore[type-arg]
    reminders: list[dict],  # type: ignore[type-arg]
    conversations: list[dict],  # type: ignore[type-arg]
    github_notifications: list[dict],  # type: ignore[type-arg]
    github_prs: list[dict],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    """Compile a morning briefing. Includes LLM summary if large model is loaded."""
    unread_imsg = sum(c.get("unread", 0) for c in conversations if c.get("source") == "imessage")
    unread_gmail = sum(c.get("unread", 0) for c in conversations if c.get("source") == "gmail")
    gh_unread = sum(1 for n in github_notifications if n.get("unread"))
    gh_prs = len(github_prs)
    pending_reminders = [r for r in reminders if not r.get("completed")]

    result: dict = {  # type: ignore[type-arg]
        "events": events,
        "pending_reminders": pending_reminders,
        "unread_counts": {
            "imessage": unread_imsg,
            "gmail": unread_gmail,
            "github_notifications": gh_unread,
            "github_prs": gh_prs,
        },
        "summary": None,
    }

    # Attempt large-model summary
    event_titles = ", ".join(e.get("summary", "?") for e in events[:5]) or "none"
    reminder_titles = ", ".join(r.get("title", "?") for r in pending_reminders[:5]) or "none"
    prompt = BRIEFING_PROMPT.format(
        events=event_titles,
        reminders=reminder_titles,
        unread_imsg=unread_imsg,
        unread_gmail=unread_gmail,
        gh_unread=gh_unread,
        gh_prs=gh_prs,
    )
    summary = llm_large_complete(prompt, max_tokens=150, temperature=0.4)
    if summary:
        result["summary"] = summary.strip()

    return result


# ── AI Triage ────────────────────────────────────────────────────────────────

PRIORITY_VALUES = ("urgent", "normal", "low")

TRIAGE_PROMPT = """\
Classify the priority of this conversation as one of: urgent, normal, low.
Source: {source}
Name: {name}
Snippet: {snippet}
Unread: {unread}

Respond with exactly one word: urgent, normal, or low."""


def ai_triage(conversations: list[dict]) -> dict[str, str]:  # type: ignore[type-arg]
    """Return priority mapping {conv_id: priority} for a list of conversations.

    Uses large model with constrained gen if available, otherwise defaults to "normal".
    """
    if not conversations:
        return {}

    model = get_large_outlines_model()
    if model is None:
        return {c.get("id", ""): "normal" for c in conversations}

    try:
        import outlines

        choices_gen = outlines.generate.choice(model, list(PRIORITY_VALUES))  # type: ignore[attr-defined]
        result: dict[str, str] = {}
        for conv in conversations:
            conv_id = conv.get("id", "")
            if not conv_id:
                continue
            snippet = conv.get("snippet", "")[:120]
            prompt = TRIAGE_PROMPT.format(
                source=conv.get("source", "?"),
                name=conv.get("name", "?"),
                snippet=snippet,
                unread=conv.get("unread", 0),
            )
            try:
                priority = choices_gen(prompt)
                result[conv_id] = priority if priority in PRIORITY_VALUES else "normal"
            except Exception:
                result[conv_id] = "normal"
        return result
    except Exception:
        return {c.get("id", ""): "normal" for c in conversations}


# ── AI Summarization ─────────────────────────────────────────────────────────

SUMMARIZE_PROMPT = """\
Summarize this email thread. Extract:
1. A brief summary (1-2 sentences)
2. Key points (bullet list)
3. Action items for the reader
4. Decisions made

Thread:
{thread_text}"""


def ai_summarize(thread_id: str, messages: list[dict]) -> dict:  # type: ignore[type-arg]
    """Summarize an email thread. Only meaningful for 5+ message threads."""
    if len(messages) < 5:
        return {
            "summary": None,
            "key_points": [],
            "action_items": [],
            "decisions": [],
            "skipped": True,
        }

    # Build thread text
    parts = []
    for msg in messages[-20:]:  # cap at 20 messages to fit context
        sender = msg.get("sender", "?")
        body = msg.get("body", "").strip()[:500]
        parts.append(f"{sender}: {body}")
    thread_text = "\n\n".join(parts)

    prompt = SUMMARIZE_PROMPT.format(thread_text=thread_text)
    raw = llm_large_complete(prompt, max_tokens=400, temperature=0.3)

    if not raw:
        return {
            "summary": None,
            "key_points": [],
            "action_items": [],
            "decisions": [],
            "skipped": False,
        }

    # Parse the free-form output into structured fields
    summary_text = raw.strip()
    lines = summary_text.split("\n")
    summary = lines[0].strip() if lines else summary_text

    key_points: list[str] = []
    action_items: list[str] = []
    decisions: list[str] = []
    current_section: list[str] = []
    section_name = ""

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "key point" in lower or "summary" in lower:
            section_name = "key_points"
            current_section = key_points
        elif "action" in lower:
            section_name = "action_items"
            current_section = action_items
        elif "decision" in lower:
            section_name = "decisions"
            current_section = decisions
        elif stripped.startswith(("- ", "* ", "• ")) and current_section is not None:
            # Remove leading list markers individually
            bullet_stripped = stripped
            for marker in ("- ", "* ", "• "):
                if bullet_stripped.startswith(marker):
                    bullet_stripped = bullet_stripped[len(marker) :]
                    break
            current_section.append(bullet_stripped.strip())
        elif section_name and stripped:
            current_section.append(stripped)

    return {
        "summary": summary,
        "key_points": key_points[:8],
        "action_items": action_items[:8],
        "decisions": decisions[:5],
        "skipped": False,
    }


# ── AI Action Extraction ──────────────────────────────────────────────────────

ACTION_EXTRACT_PROMPT = """\
Extract action items from this message. For each action item identify:
- text: the action to take
- deadline: date/time if mentioned (or null)
- type: "task", "meeting", "follow-up", or "reminder"

Message:
{text}

List action items as JSON. If none, return empty list."""


def ai_extract_actions(text: str) -> dict:  # type: ignore[type-arg]
    """Extract action items from message text. Returns {actions: [...]}."""
    if len(text.strip()) < 20:
        return {"actions": []}

    try:
        from pydantic import BaseModel as _PM

        class ActionItem(_PM):
            text: str
            deadline: str | None = None
            type: str = "task"

        class ActionList(_PM):
            actions: list[ActionItem]

        prompt = ACTION_EXTRACT_PROMPT.format(text=text[:1000])

        # Try large model first for better quality
        result = generate_json_large(prompt, ActionList)  # type: ignore[arg-type]
        if result is None:
            result = generate_json(prompt, ActionList)  # type: ignore[arg-type]

        actions = []
        for item in result.actions:  # type: ignore[union-attr]
            action_type = (
                item.type if item.type in ("task", "meeting", "follow-up", "reminder") else "task"
            )
            actions.append(
                {
                    "text": item.text,
                    "deadline": item.deadline,
                    "type": action_type,
                }
            )
        return {"actions": actions}
    except Exception:
        return {"actions": []}


# ── Voice Config ────────────────────────────────────────────────────────────

VOICE_CONFIG_PATH = Path.home() / ".config" / "inbox" / "voice.json"

_VOICE_CONFIG_DEFAULTS: dict[str, object] = {
    "ambient_autostart": False,
    "dictation_hotkey": "f5",
    "vault_dir": str(Path.home() / "vault"),
}


def load_voice_config() -> dict[str, object]:  # type: ignore[type-arg]
    """Load voice config from disk, merging missing keys with defaults."""
    if VOICE_CONFIG_PATH.exists():
        try:
            data = json.loads(VOICE_CONFIG_PATH.read_text())
            return {**_VOICE_CONFIG_DEFAULTS, **data}
        except Exception:
            pass
    return dict(_VOICE_CONFIG_DEFAULTS)


def save_voice_config(config: dict[str, object]) -> None:  # type: ignore[type-arg]
    """Persist voice config to disk."""
    VOICE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**_VOICE_CONFIG_DEFAULTS, **config}
    VOICE_CONFIG_PATH.write_text(json.dumps(merged, indent=2))


# ── Voice Command Routing ───────────────────────────────────────────────────

_voice_command_handlers: list[Callable[[str], bool]] = []


def register_voice_command_handler(handler: Callable[[str], bool]) -> None:
    """Register a handler that receives voice text and returns True if handled."""
    _voice_command_handlers.append(handler)


def route_voice_command(text: str) -> bool:
    """Dispatch voice text to registered handlers. Returns True if handled."""
    for handler in _voice_command_handlers:
        try:
            if handler(text):
                return True
        except Exception:
            pass
    return False


def _event_to_calendar(
    item: dict,
    account: str = "",
    calendar_id: str = "primary",
) -> CalendarEvent | None:
    """Parse a Google Calendar API event dict into a CalendarEvent."""
    try:
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})

        all_day = "date" in start_raw
        if all_day:
            start_dt = datetime.strptime(start_raw["date"], "%Y-%m-%d")
            end_dt = datetime.strptime(end_raw["date"], "%Y-%m-%d")
        else:
            start_dt = datetime.fromisoformat(start_raw.get("dateTime", ""))
            end_dt = datetime.fromisoformat(end_raw.get("dateTime", ""))

        # Extract attendee data
        raw_attendees = item.get("attendees", [])
        attendee_list = [
            {
                "name": a.get("displayName", ""),
                "email": a.get("email", ""),
                "responseStatus": a.get("responseStatus", ""),
            }
            for a in raw_attendees
        ]

        # Extract recurrence and reminders
        recurrence = item.get("recurrence", [])
        reminders = item.get("reminders", {})
        recurring_event_id = item.get("recurringEventId", "")

        return CalendarEvent(
            summary=item.get("summary", "(No title)"),
            start=start_dt,
            end=end_dt,
            location=item.get("location", ""),
            description=item.get("description", ""),
            account=account,
            all_day=all_day,
            event_id=item.get("id", ""),
            calendar_id=calendar_id,
            attendees=attendee_list,
            recurrence=recurrence,
            reminders=reminders,
            recurring_event_id=recurring_event_id,
        )
    except Exception:
        return None


def calendar_list_calendars(cal_services: dict[str, object]) -> list[dict]:
    """List all calendars across all accounts."""
    result: list[dict] = []
    for account, svc in cal_services.items():
        try:
            calendars_list = svc.calendarList().list().execute().get("items", [])
            for cal in calendars_list:
                result.append(
                    {
                        "id": cal.get("id", ""),
                        "summary": cal.get("summary", ""),
                        "description": cal.get("description", ""),
                        "primary": cal.get("primary", False),
                        "access_role": cal.get("accessRole", ""),
                        "background_color": cal.get("backgroundColor", ""),
                        "account": account,
                    }
                )
        except Exception:
            _log_service_failure("calendar_list_calendars", account=account)
    return result


def calendar_get_event(
    cal_service,
    event_id: str,
    calendar_id: str = "primary",
) -> CalendarEvent | None:
    """Fetch a single event and return as CalendarEvent."""
    try:
        item = cal_service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        return _event_to_calendar(item, account="", calendar_id=calendar_id)
    except Exception:
        _log_service_failure(
            "calendar_get_event",
            event_id=event_id,
            calendar_id=calendar_id,
        )
        return None


def calendar_rsvp_event(
    cal_service,
    event_id: str,
    self_email: str,
    response: str,
    calendar_id: str = "primary",
) -> bool:
    """RSVP to an event (accept/decline/tentative)."""
    try:
        cal_service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={"attendees": [{"email": self_email, "responseStatus": response}]},
        ).execute()
        return True
    except Exception:
        _log_service_failure(
            "calendar_rsvp_event",
            event_id=event_id,
            self_email=self_email,
            response=response,
            calendar_id=calendar_id,
        )
        return False


def calendar_modify_attendees(
    cal_service,
    event_id: str,
    add: list[dict[str, str]] | None = None,
    remove: list[str] | None = None,
    calendar_id: str = "primary",
) -> bool:
    """Add/remove attendees from an event."""
    try:
        existing = cal_service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        attendees = existing.get("attendees", [])

        if remove:
            remove_set = {e.lower() for e in remove}
            attendees = [a for a in attendees if a.get("email", "").lower() not in remove_set]

        if add:
            existing_emails = {a.get("email", "").lower() for a in attendees}
            for new in add:
                new_email = new.get("email", "").lower()
                if new_email and new_email not in existing_emails:
                    attendees.append(new)

        cal_service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={"attendees": attendees},
        ).execute()
        return True
    except Exception:
        _log_service_failure(
            "calendar_modify_attendees",
            event_id=event_id,
            calendar_id=calendar_id,
        )
        return False


def calendar_get_recurring_instances(
    cal_service,
    event_id: str,
    calendar_id: str = "primary",
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    max_results: int = 50,
) -> list[CalendarEvent]:
    """Get all instances of a recurring event."""
    result: list[CalendarEvent] = []
    try:
        kwargs = {"calendarId": calendar_id, "eventId": event_id, "maxResults": max_results}
        if time_min:
            kwargs["timeMin"] = time_min.isoformat()
        if time_max:
            kwargs["timeMax"] = time_max.isoformat()

        items = cal_service.events().instances(**kwargs).execute().get("items", [])
        for item in items:
            evt = _event_to_calendar(item, account="", calendar_id=calendar_id)
            if evt:
                result.append(evt)
    except Exception:
        _log_service_failure(
            "calendar_get_recurring_instances",
            event_id=event_id,
            calendar_id=calendar_id,
        )
    return result


def calendar_search_events(
    cal_services: dict[str, object],
    query: str = "",
    attendee_email: str = "",
    location: str = "",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    calendar_id: str | None = None,
    max_results: int = 50,
) -> list[CalendarEvent]:
    """Search events across calendars with filtering."""
    result: list[CalendarEvent] = []
    attendee_email_lower = attendee_email.lower()
    location_lower = location.lower()

    for account, svc in cal_services.items():
        try:
            cal_ids = [calendar_id] if calendar_id else ["primary"]

            for cal_id in cal_ids:
                kwargs = {"calendarId": cal_id, "maxResults": max_results}
                if query:
                    kwargs["q"] = query
                if start_date:
                    kwargs["timeMin"] = start_date.isoformat()
                if end_date:
                    kwargs["timeMax"] = end_date.isoformat()

                items = svc.events().list(**kwargs).execute().get("items", [])

                for item in items:
                    if attendee_email:
                        attendees = item.get("attendees", [])
                        if not any(
                            a.get("email", "").lower() == attendee_email_lower for a in attendees
                        ):
                            continue

                    if location and location_lower not in item.get("location", "").lower():
                        continue

                    evt = _event_to_calendar(item, account=account, calendar_id=cal_id)
                    if evt:
                        result.append(evt)
        except Exception:
            _log_service_failure("calendar_search_events", account=account)

    return result


def calendar_freebusy(
    cal_service,
    time_min: datetime,
    time_max: datetime,
    calendar_ids: list[str],
    timezone: str = "UTC",
) -> dict[str, list[dict[str, str]]]:
    """Get busy blocks for calendars."""
    try:
        result = (
            cal_service.freebusy()
            .query(
                body={
                    "timeMin": time_min.isoformat(),
                    "timeMax": time_max.isoformat(),
                    "timeZone": timezone,
                    "items": [{"id": cid} for cid in calendar_ids],
                }
            )
            .execute()
        )

        busy_dict: dict[str, list[dict[str, str]]] = {}
        for cal_id, data in result.get("calendars", {}).items():
            busy_list = data.get("busy", [])
            busy_dict[cal_id] = [
                {"start": b.get("start", ""), "end": b.get("end", "")} for b in busy_list
            ]
        return busy_dict
    except Exception:
        _log_service_failure("calendar_freebusy")
        return {}


def calendar_find_free_slots(
    cal_service,
    time_min: datetime,
    time_max: datetime,
    calendar_ids: list[str],
    duration_minutes: int = 30,
    timezone: str = "UTC",
) -> list[dict[str, str]]:
    """Find free slots between busy blocks."""
    busy_dict = calendar_freebusy(cal_service, time_min, time_max, calendar_ids, timezone)

    all_busy = []
    for busy_list in busy_dict.values():
        for b in busy_list:
            all_busy.append(
                (
                    datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                    datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
                )
            )

    all_busy.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in all_busy:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    current = time_min
    slots: list[dict[str, str]] = []
    duration = timedelta(minutes=duration_minutes)

    for busy_start, busy_end in merged:
        if busy_start > current and (busy_start - current) >= duration:
            slots.append(
                {
                    "start": current.isoformat(),
                    "end": busy_start.isoformat(),
                }
            )
        current = max(current, busy_end)

    if current + duration <= time_max:
        slots.append(
            {
                "start": current.isoformat(),
                "end": time_max.isoformat(),
            }
        )

    return slots
