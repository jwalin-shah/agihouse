"""
Inbox API server — local REST API for iMessage, Gmail, Calendar, Notes, Reminders.
Run: uv run python inbox_server.py
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

import ambient_notes
import g2_agent as g2
from memory_store import MemoryStore
from message_index_store import MessageIndexStore
from message_sync import bootstrap as index_bootstrap_sync
from message_sync import incremental as index_incremental_sync
from scheduler import SchedulerStore
from services import (
    MLX_LARGE_MODEL,
    AmbientService,
    CalendarEvent,
    Contact,
    DictationService,
    DriveFile,
    GitHubNotification,
    GoogleTask,
    Msg,
    Note,
    Reminder,
    SheetTab,
    Spreadsheet,
    ThreadSummary,
    add_google_account,
    ai_briefing,
    ai_extract_actions,
    ai_extract_memory,
    ai_summarize,
    ai_triage,
    ambient_available,
    calendar_create_event,
    calendar_delete_event,
    calendar_event_to_reminder,
    calendar_events,
    calendar_find_conflicts,
    calendar_find_free_slots,
    calendar_freebusy,
    calendar_get_event,
    calendar_get_recurring_instances,
    calendar_list_calendars,
    calendar_modify_attendees,
    calendar_rsvp_event,
    calendar_search_events,
    calendar_update_event,
    close_sqlite_connections,
    contacts_profile,
    contacts_search,
    departure_times_for_events,
    docs_create,
    docs_delete,
    docs_export,
    docs_get,
    docs_get_text,
    docs_insert_text,
    docs_list,
    drive_create_folder,
    drive_delete,
    drive_download,
    drive_files,
    drive_get,
    drive_upload,
    gemini_categorize,
    gemini_digest,
    gemini_extract_action_items,
    gemini_smart_reply,
    gemini_summarize,
    get_current_location,
    github_mark_all_read,
    github_mark_read,
    github_notifications,
    github_pulls,
    gmail_archive,
    gmail_attachment_download,
    gmail_batch_modify,
    gmail_compose_send,
    gmail_contacts,
    gmail_contacts_by_label,
    gmail_create_filter,
    gmail_delete,
    gmail_label_create,
    gmail_labels,
    gmail_mark_read,
    gmail_mark_unread,
    gmail_reply,
    gmail_search,
    gmail_send,
    gmail_star,
    gmail_thread,
    gmail_thread_summary,
    gmail_unstar,
    gmail_unsubscribe,
    google_auth_all,
    imsg_contacts,
    imsg_send,
    imsg_thread,
    init_contacts,
    llm_large_is_loaded,
    llm_large_is_loading,
    load_favorites,
    load_notification_config,
    load_voice_config,
    maps_travel_time,
    note_body,
    notes_list,
    parse_quick_event,
    reauth_google_account,
    reminder_by_id,
    reminder_complete,
    reminder_create,
    reminder_delete,
    reminder_edit,
    reminder_uncomplete,
    reminders_list,
    reminders_lists,
    save_favorites,
    save_notification_config,
    save_voice_config,
    search_all,
    send_notification,
    sheets_add_sheet,
    sheets_copy_to,
    sheets_create,
    sheets_delete,
    sheets_delete_sheet,
    sheets_format,
    sheets_get,
    sheets_list,
    sheets_rename_sheet,
    sheets_values_append,
    sheets_values_batch_get,
    sheets_values_batch_update,
    sheets_values_clear,
    sheets_values_get,
    sheets_values_update,
    task_complete,
    task_create,
    task_delete,
    task_update,
    tasks_list,
    tasks_lists,
    whatsapp_check_accessibility,
    whatsapp_contacts,
    whatsapp_contacts_all,
    whatsapp_launch,
    whatsapp_scroll_sidebar,
    whatsapp_send,
    whatsapp_thread,
    whatsapp_thread_full,
)
from services import (
    autocomplete as services_autocomplete,
)

PORT = 9849
AUTH_TOKEN_ENV = "INBOX_SERVER_TOKEN"  # nosec: B105 - env var name, not a hardcoded credential


# ── Pydantic models ──────────────────────────────────────────────────────────


class ConversationOut(BaseModel):
    id: str
    name: str
    source: str
    snippet: str
    unread: int
    last_ts: str
    guid: str = ""
    is_group: bool = False
    members: list[str] = []
    reply_to: str = ""
    thread_id: str = ""
    message_id_header: str = ""
    gmail_account: str = ""


class MessageOut(BaseModel):
    sender: str
    body: str
    ts: str
    is_me: bool
    source: str
    attachments: list[dict] = []  # type: ignore[type-arg]
    message_id: str = ""


class CalendarEventOut(BaseModel):
    summary: str
    start: str
    end: str
    location: str = ""
    description: str = ""
    account: str = ""
    all_day: bool = False
    event_id: str = ""
    calendar_id: str = ""
    attendees: list[dict[str, str]] = []
    recurrence: list[str] = []
    reminders: dict = {}
    recurring_event_id: str = ""
    workflow: str = ""


class CalendarOut(BaseModel):
    id: str
    summary: str
    description: str = ""
    primary: bool = False
    access_role: str = ""
    background_color: str = ""
    account: str = ""


class RsvpRequest(BaseModel):
    response: str  # "accepted" | "declined" | "tentative"
    calendar_id: str = "primary"
    account: str = ""


class ModifyAttendeesRequest(BaseModel):
    add: list[dict[str, str]] = []
    remove: list[str] = []
    calendar_id: str = "primary"
    account: str = ""


class EventRemindersRequest(BaseModel):
    use_default: bool = False
    overrides: list[dict[str, int | str]] = []
    calendar_id: str = "primary"
    account: str = ""


class FreeBusyRequest(BaseModel):
    time_min: str
    time_max: str
    calendar_ids: list[str] = ["primary"]
    timezone: str = "UTC"
    account: str = ""


class FreeSlotsRequest(BaseModel):
    time_min: str
    time_max: str
    calendar_ids: list[str] = ["primary"]
    duration_minutes: int = 30
    timezone: str = "UTC"
    account: str = ""


class NoteOut(BaseModel):
    id: str
    title: str
    snippet: str
    modified: str
    folder: str = ""


class SendRequest(BaseModel):
    conv_id: str
    source: str  # "imessage" | "gmail"
    text: str


class CreateEventRequest(BaseModel):
    summary: str
    start: str  # ISO datetime or quick format
    end: str  # ISO datetime
    location: str = ""
    description: str = ""
    all_day: bool = False
    attendees: list[dict[str, str]] = []
    account: str = ""  # defaults to first calendar account


class QuickEventRequest(BaseModel):
    text: str  # e.g. "Meeting 2pm-3pm @ Office"
    account: str = ""


class UpdateEventRequest(BaseModel):
    summary: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    description: str | None = None
    reminders: dict | None = None


class AccountRequest(BaseModel):
    email: str = ""


class ReminderOut(BaseModel):
    id: str
    title: str
    completed: bool
    list_name: str = ""
    due_date: str | None = None
    notes: str = ""
    priority: int = 0
    flagged: bool = False
    creation_date: str | None = None


class ReminderCreateRequest(BaseModel):
    title: str
    list_name: str = "Reminders"
    due_date: str = ""
    notes: str = ""
    priority: int = 0
    flagged: bool = False


class ReminderEditRequest(BaseModel):
    title: str | None = None
    due_date: str | None = None
    notes: str | None = None
    priority: int | None = None
    flagged: bool | None = None


class TaskOut(BaseModel):
    id: str
    title: str
    status: str
    list_id: str
    list_title: str
    due: str | None = None
    notes: str = ""
    completed: str | None = None
    account: str = ""
    workflow: str = ""


class TaskCreateRequest(BaseModel):
    title: str
    list_id: str = "@default"
    due: str = ""
    notes: str = ""


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    due: str | None = None
    notes: str | None = None


class ScheduleMessageRequest(BaseModel):
    source: str  # "gmail" | "imessage"
    conv_id: str  # iMessage contact id, or Gmail "to|subject" for compose
    text: str
    send_at: str  # ISO datetime
    account: str = ""


class FollowupCreateRequest(BaseModel):
    source: str  # "gmail" | "imessage"
    conv_id: str
    thread_id: str = ""
    remind_after: str  # ISO datetime
    reminder_title: str
    reminder_list: str = "Reminders"


class TaskLinkRequest(BaseModel):
    task_id: str
    task_source: str  # "google_tasks" | "reminders"
    message_id: str
    message_source: str  # "gmail" | "imessage"
    thread_id: str = ""
    account: str = ""


class TaskFromMessageRequest(BaseModel):
    message_id: str
    message_source: str  # "gmail" | "imessage"
    title: str
    task_type: str = "google_tasks"  # "google_tasks" | "reminders"
    list_id: str = "@default"  # for google_tasks
    list_name: str = "Reminders"  # for reminders
    notes: str = ""
    thread_id: str = ""
    account: str = ""


class GitHubNotificationOut(BaseModel):
    id: str
    title: str
    repo: str
    type: str
    reason: str
    unread: bool
    updated_at: str
    url: str = ""


class DriveFileOut(BaseModel):
    id: str
    name: str
    mime_type: str
    modified: str
    size: int = 0
    shared: bool = False
    web_link: str = ""
    parents: list[str] = []
    account: str = ""


class DriveCreateFolderRequest(BaseModel):
    name: str
    parent_id: str = ""
    account: str = ""


class SheetTabOut(BaseModel):
    sheet_id: int
    title: str
    index: int
    row_count: int
    col_count: int


class SpreadsheetOut(BaseModel):
    id: str
    title: str
    url: str
    sheets: list[SheetTabOut] = []
    account: str = ""


class CreateSpreadsheetRequest(BaseModel):
    title: str
    sheets: list[str] = []
    account: str = ""


class SheetValuesUpdateRequest(BaseModel):
    values: list[list]  # type: ignore[type-arg]
    value_input: str = "USER_ENTERED"


class SheetValuesBatchUpdateRequest(BaseModel):
    data: list[dict]  # type: ignore[type-arg]
    value_input: str = "USER_ENTERED"


class AddSheetRequest(BaseModel):
    title: str
    rows: int = 1000
    cols: int = 26
    account: str = ""


class FormatRequest(BaseModel):
    requests: list[dict]  # type: ignore[type-arg]
    account: str = ""


class BatchGetRequest(BaseModel):
    ranges: list[str]


class DocumentOut(BaseModel):
    id: str
    title: str
    url: str
    mime_type: str = "application/vnd.google-apps.document"
    account: str = ""


class PreflightResult(BaseModel):
    kind: str
    resolved_account: str
    destination: str
    destination_id: str
    valid: bool
    warnings: list[str] = []
    explanation: str


class GmailThreadSummaryOut(BaseModel):
    thread_id: str
    owning_account: str
    participants: list[str]
    subject: str
    last_message_at: str
    labels: list[str]
    summary: str
    action_items: list[str]
    needs_reply: bool
    workflow: str
    message_count: int
    rank: float = 0.0
    brief: str = ""
    rich_data: dict[str, str] = {}


class ThreadBriefOut(BaseModel):
    thread_id: str
    brief: str
    rank: float
    workflow: str
    needs_reply: bool


class CreateDocumentRequest(BaseModel):
    title: str
    account: str = ""


class WorkflowEventRequest(BaseModel):
    kind: str = ""  # "interview" | "deadline" | "meeting"
    title: str
    workflow: str = ""
    start: str
    end: str
    location: str = ""
    description: str = ""
    attendees: list[dict[str, str]] = []
    account: str = ""


class NeedsActionOut(BaseModel):
    threads: list[GmailThreadSummaryOut]
    tasks: list[TaskOut]
    events: list[CalendarEventOut]
    workflow_counts: dict[str, int]


class IndexSyncOut(BaseModel):
    ok: bool
    mode: str
    stats: dict[str, dict[str, int]]


class IndexSyncStateOut(BaseModel):
    source: str
    account: str
    checkpoint_type: str
    checkpoint_value: str
    last_success_at: str
    last_full_sync_at: str
    status: str
    last_run_started_at: str
    last_error: str
    metadata: dict[str, Any]


class IndexStatusOut(BaseModel):
    db_path: str
    threads: list[GmailThreadSummaryOut]


class IndexOverviewOut(BaseModel):
    db_path: str
    counts: dict[str, int]
    sync_states: list[IndexSyncStateOut]


class IndexedThreadListOut(BaseModel):
    view: str
    threads: list[GmailThreadSummaryOut]


class WorkflowFolderRequest(BaseModel):
    workflow: str
    name: str = ""
    parent_id: str = ""
    account: str = ""


class WorkflowDocRequest(BaseModel):
    title: str
    workflow: str = ""
    account: str = ""


class WorkflowSheetRequest(BaseModel):
    title: str
    workflow: str = ""
    account: str = ""


class InsertTextRequest(BaseModel):
    text: str
    index: int = 1


class CopySheetRequest(BaseModel):
    dest_spreadsheet_id: str


class AutocompleteRequest(BaseModel):
    draft: str = ""
    messages: list[dict] = []  # type: ignore[type-arg]
    max_tokens: int = 32
    temperature: float = 0.5
    mode: str = "complete"


class NotificationTestRequest(BaseModel):
    title: str
    body: str = ""


class VoiceConfigRequest(BaseModel):
    ambient_autostart: bool | None = None
    dictation_hotkey: str | None = None
    vault_dir: str | None = None


class ComposeRequest(BaseModel):
    to: str
    subject: str
    body: str
    account: str = ""


class GmailReplyRequest(BaseModel):
    msg_id: str
    body: str
    thread_id: str = ""
    to: str = ""
    subject: str = ""
    message_id_header: str = ""
    account: str = ""


class GmailBatchModifyRequest(BaseModel):
    msg_ids: list[str]
    add_label_ids: list[str] = []
    remove_label_ids: list[str] = []
    account: str = ""


class GmailFilterCreateRequest(BaseModel):
    from_filter: str = ""
    to_filter: str = ""
    subject_filter: str = ""
    query: str = ""
    has_words: str = ""
    does_not_have_words: str = ""
    add_label_ids: list[str] = []
    remove_label_ids: list[str] = []
    forward: str = ""
    account: str = ""


class SearchRequest(BaseModel):
    q: str
    sources: list[str] = ["all"]
    limit: int = 50
    from_addr: str = ""  # filter by sender (gmail/imessage)
    before: str = ""  # ISO date cutoff (inclusive upper bound)
    after: str = ""  # ISO date cutoff (inclusive lower bound)
    has_attachment: bool = False  # Gmail only
    is_unread: bool = False  # Gmail only


class TriageRequest(BaseModel):
    conversations: list[dict] = []  # type: ignore[type-arg]


class SummarizeRequest(BaseModel):
    thread_id: str = ""
    messages: list[dict] = []  # type: ignore[type-arg]


class ExtractActionsRequest(BaseModel):
    text: str


class BulkUnsubscribeRequest(BaseModel):
    msg_ids: list[str]


# ── Server state ─────────────────────────────────────────────────────────────


class ServerState:
    def __init__(self) -> None:
        self.gmail_services: dict[str, object] = {}
        self.cal_services: dict[str, object] = {}
        self.drive_services: dict[str, object] = {}
        self.sheets_services: dict[str, object] = {}
        self.docs_services: dict[str, object] = {}
        self.tasks_services: dict[str, object] = {}
        self.conv_cache: dict[str, Contact] = {}  # "source:id" -> Contact
        self.events_cache: list[CalendarEvent] = []
        self.ambient: AmbientService = AmbientService(
            on_note=lambda raw, summary: ambient_notes.save_note(raw, summary)
        )
        self.dictation: DictationService = DictationService()
        self.scheduler: SchedulerStore = SchedulerStore()
        self.index_store: MessageIndexStore = MessageIndexStore()


state = ServerState()
memory_store = MemoryStore()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _contact_to_out(c: Contact) -> ConversationOut:
    return ConversationOut(
        id=c.id,
        name=c.name,
        source=c.source,
        snippet=c.snippet,
        unread=c.unread,
        last_ts=c.last_ts.isoformat(),
        guid=c.guid,
        is_group=c.is_group,
        members=c.members,
        reply_to=c.reply_to,
        thread_id=c.thread_id,
        message_id_header=c.message_id_header,
        gmail_account=c.gmail_account,
    )


def _msg_to_out(m: Msg) -> MessageOut:
    return MessageOut(
        sender=m.sender,
        body=m.body,
        ts=m.ts.isoformat(),
        is_me=m.is_me,
        source=m.source,
        attachments=m.attachments,
        message_id=m.message_id,
    )


def _event_to_out(e: CalendarEvent) -> CalendarEventOut:
    return CalendarEventOut(
        summary=e.summary,
        start=e.start.isoformat(),
        end=e.end.isoformat(),
        location=e.location,
        description=e.description,
        account=e.account,
        all_day=e.all_day,
        event_id=e.event_id,
        calendar_id=e.calendar_id,
        attendees=e.attendees,
        workflow=_classify_workflow(f"{e.summary} {e.description}"),
    )


def _note_to_out(n: Note) -> NoteOut:
    return NoteOut(
        id=n.id,
        title=n.title,
        snippet=n.snippet,
        modified=n.modified.isoformat(),
        folder=n.folder,
    )


def _reminder_to_out(r: Reminder) -> ReminderOut:
    return ReminderOut(
        id=r.id,
        title=r.title,
        completed=r.completed,
        list_name=r.list_name,
        due_date=r.due_date.isoformat() if r.due_date else None,
        notes=r.notes,
        priority=r.priority,
        flagged=r.flagged,
        creation_date=r.creation_date.isoformat() if r.creation_date else None,
    )


def _task_to_out(t: GoogleTask, account: str = "") -> TaskOut:
    return TaskOut(
        id=t.id,
        title=t.title,
        status=t.status,
        list_id=t.list_id,
        list_title=t.list_title,
        due=t.due.isoformat() if t.due else None,
        notes=t.notes,
        completed=t.completed.isoformat() if t.completed else None,
        account=account,
        workflow=_classify_workflow(f"{t.title} {t.notes}"),
    )


def _gh_notif_to_out(n: GitHubNotification) -> GitHubNotificationOut:
    return GitHubNotificationOut(
        id=n.id,
        title=n.title,
        repo=n.repo,
        type=n.type,
        reason=n.reason,
        unread=n.unread,
        updated_at=n.updated_at.isoformat(),
        url=n.url,
    )


def _thread_summary_to_out(ts: ThreadSummary, label_map: dict[str, str]) -> GmailThreadSummaryOut:
    labels = [label_map.get(lid, lid) for lid in ts.label_ids if not lid.startswith("CATEGORY_")]
    text = f"{ts.subject} {ts.body_text}"
    workflow = _classify_workflow(text)
    needs_reply = not ts.last_sender_is_me
    action_items = _extract_action_items(ts.body_text)
    last_iso = ts.last_message_at.isoformat()
    rank = _rank_thread(last_iso, needs_reply, bool(action_items), workflow, ts.message_count)
    sender = ts.participants[0] if ts.participants else "Unknown"
    brief_parts = [f"{sender} \u00b7 {ts.subject[:60].rstrip()}"]
    if needs_reply:
        brief_parts.append("[needs reply]")
    if workflow:
        brief_parts.append(f"[{workflow}]")
    return GmailThreadSummaryOut(
        thread_id=ts.thread_id,
        owning_account=ts.owning_account,
        participants=ts.participants,
        subject=ts.subject,
        last_message_at=last_iso,
        labels=labels,
        summary=ts.last_message_body[:300].strip(),
        action_items=action_items,
        needs_reply=needs_reply,
        workflow=workflow,
        message_count=ts.message_count,
        rank=rank,
        brief=" ".join(brief_parts),
        rich_data=_extract_rich_data(workflow, text),
    )


def _drive_to_out(f: DriveFile, account: str = "") -> DriveFileOut:
    return DriveFileOut(
        id=f.id,
        name=f.name,
        mime_type=f.mime_type,
        modified=f.modified.isoformat(),
        size=f.size,
        shared=f.shared,
        web_link=f.web_link,
        parents=f.parents,
        account=account or f.account,
    )


def _sheet_tab_to_out(tab: SheetTab) -> SheetTabOut:
    return SheetTabOut(
        sheet_id=tab.sheet_id,
        title=tab.title,
        index=tab.index,
        row_count=tab.row_count,
        col_count=tab.col_count,
    )


def _spreadsheet_to_out(s: Spreadsheet, account: str = "") -> SpreadsheetOut:
    return SpreadsheetOut(
        id=s.id,
        title=s.title,
        url=s.url,
        sheets=[_sheet_tab_to_out(tab) for tab in s.sheets],
        account=account or s.account,
    )


def _cache_key(source: str, conv_id: str) -> str:
    return f"{source}:{conv_id}"


# ── Background scheduler loop ────────────────────────────────────────────────


async def _process_scheduled_messages() -> None:
    """Send any scheduled messages that are due."""
    try:
        due = await asyncio.to_thread(state.scheduler.get_due_messages)
        for msg in due:
            msg_id = msg["id"]
            source = msg["source"]
            try:
                if source == "gmail":
                    acct = msg.get("account", "")
                    svc_key = acct or (
                        next(iter(state.gmail_services)) if state.gmail_services else ""
                    )
                    svc = state.gmail_services.get(svc_key)
                    if not svc:
                        await asyncio.to_thread(
                            state.scheduler.mark_failed, msg_id, "No Gmail service"
                        )
                        continue
                    # conv_id here holds the "to" email address for compose
                    # Format: "to|subject" — if no pipe, treat whole as recipient with empty subject
                    raw_conv = msg["conv_id"]
                    if "|" in raw_conv:
                        to, subject = raw_conv.split("|", 1)
                    else:
                        to, subject = raw_conv, "(no subject)"
                    ok = await asyncio.to_thread(gmail_compose_send, svc, to, subject, msg["text"])
                    if ok:
                        await asyncio.to_thread(state.scheduler.mark_sent, msg_id)
                        logger.info(f"[scheduler] Sent gmail msg {msg_id} → {to}")
                    else:
                        await asyncio.to_thread(
                            state.scheduler.mark_failed, msg_id, "gmail_compose_send returned False"
                        )
                elif source == "imessage":
                    cache_key = _cache_key("imessage", msg["conv_id"])
                    contact = state.conv_cache.get(cache_key)
                    if not contact:
                        contacts = await asyncio.to_thread(imsg_contacts, 200)
                        contact = next((c for c in contacts if c.id == msg["conv_id"]), None)
                        if contact:
                            state.conv_cache[cache_key] = contact
                    if not contact:
                        await asyncio.to_thread(
                            state.scheduler.mark_failed, msg_id, "Contact not found"
                        )
                        continue
                    ok = await asyncio.to_thread(imsg_send, contact, msg["text"])
                    if ok:
                        await asyncio.to_thread(state.scheduler.mark_sent, msg_id)
                        logger.info(f"[scheduler] Sent imsg {msg_id} → {contact.name}")
                    else:
                        await asyncio.to_thread(
                            state.scheduler.mark_failed, msg_id, "imsg_send returned False"
                        )
                else:
                    await asyncio.to_thread(
                        state.scheduler.mark_failed, msg_id, f"Unknown source: {source}"
                    )
            except Exception as e:
                logger.exception(f"[scheduler] Failed to send msg {msg_id}")
                await asyncio.to_thread(state.scheduler.mark_failed, msg_id, str(e))
    except Exception:
        logger.exception("[scheduler] _process_scheduled_messages failed")


async def _process_followup_reminders() -> None:
    """Check follow-up reminders — create Apple Reminders if no reply has come in."""
    try:
        due = await asyncio.to_thread(state.scheduler.get_due_followups)
        for fu in due:
            fid = fu["id"]
            try:
                created_at = datetime.fromisoformat(fu["created_at"])
                replied = False
                if fu["source"] == "gmail" and fu["thread_id"]:
                    svc_key = next(iter(state.gmail_services)) if state.gmail_services else ""
                    svc = state.gmail_services.get(svc_key)
                    if svc:
                        msgs = await asyncio.to_thread(
                            gmail_thread, svc, fu["conv_id"], fu["thread_id"]
                        )
                        # Reply = any message in thread newer than created_at that isn't from me
                        replied = any(m.ts > created_at and not m.is_me for m in msgs)
                elif fu["source"] == "imessage":
                    cache_key = _cache_key("imessage", fu["conv_id"])
                    contact = state.conv_cache.get(cache_key)
                    if not contact:
                        contacts = await asyncio.to_thread(imsg_contacts, 200)
                        contact = next((c for c in contacts if c.id == fu["conv_id"]), None)
                    if contact:
                        msgs = await asyncio.to_thread(imsg_thread, contact, 20)
                        replied = any(m.ts > created_at and not m.is_me for m in msgs)

                if replied:
                    await asyncio.to_thread(state.scheduler.mark_followup_replied, fid)
                    logger.info(f"[scheduler] Follow-up {fid} replied — skipping task")
                else:
                    # Try Google Tasks first (integrates with Google Calendar)
                    ok = False
                    task_created_via = ""
                    title = fu["reminder_title"]
                    notes = f"No reply in conversation: {fu['conv_id']}"

                    if state.tasks_services:
                        try:
                            _, tasks_svc = _get_tasks_service_for_account("")
                            due_iso = datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")
                            ok = await asyncio.to_thread(
                                task_create, tasks_svc, title, "@default", due_iso, notes
                            )
                            if ok:
                                task_created_via = "google_tasks"
                        except Exception as e:
                            logger.warning(
                                f"[scheduler] Google Tasks followup failed ({e}), falling back to Apple Reminders"
                            )

                    # Fallback: Apple Reminders
                    if not ok:
                        now_str = datetime.now().strftime("%B %d, %Y %I:%M:%S %p")
                        ok = await asyncio.to_thread(
                            reminder_create,
                            title=title,
                            list_name=fu["reminder_list"],
                            due_date=now_str,
                            notes=notes,
                        )
                        if ok:
                            task_created_via = "apple_reminders"

                    if ok:
                        await asyncio.to_thread(state.scheduler.mark_followup_fired, fid)
                        logger.info(
                            f"[scheduler] Follow-up {fid} fired — created via {task_created_via}"
                        )
                    else:
                        logger.warning(
                            f"[scheduler] Follow-up {fid} failed to create task via any backend"
                        )
            except Exception:
                logger.exception(f"[scheduler] Failed to process followup {fid}")
    except Exception:
        logger.exception("[scheduler] _process_followup_reminders failed")


# Track which events we've already created departure tasks for (avoid duplicates)
_departure_task_created: set[str] = set()


async def _process_departure_alerts() -> None:
    """Check upcoming events and create 'time to leave' tasks when departure time is near."""
    home = await asyncio.to_thread(get_current_location)
    if not home:
        return  # No location available — skip departure alerts

    try:
        events = await asyncio.to_thread(calendar_events, state.cal_services)
        departures = await asyncio.to_thread(
            departure_times_for_events, events, home, "driving", 10, 4
        )
        now = datetime.now()
        for dep in departures:
            # Alert if departure time is within 15 minutes from now
            minutes_until_departure = (dep.departure_time - now).total_seconds() / 60
            if -5 < minutes_until_departure < 15:
                event_key = f"{dep.event_summary}|{dep.event_start.isoformat()}"
                if event_key in _departure_task_created:
                    continue
                _departure_task_created.add(event_key)

                title = f"🚗 Leave now for {dep.event_summary} ({dep.duration_text})"
                notes = f"Travel: {dep.distance_text}, {dep.duration_text} ({dep.mode})\nTo: {dep.event_location}\nEvent: {dep.event_start.strftime('%I:%M %p')}"

                # Try Google Tasks first, fallback to Apple Reminders
                ok = False
                if state.tasks_services:
                    try:
                        _, tasks_svc = _get_tasks_service_for_account("")
                        due_iso = dep.departure_time.strftime("%Y-%m-%dT00:00:00.000Z")
                        ok = await asyncio.to_thread(
                            task_create, tasks_svc, title, "@default", due_iso, notes
                        )
                    except Exception:
                        pass
                if not ok:
                    now_str = dep.departure_time.strftime("%B %d, %Y %I:%M:%S %p")
                    ok = await asyncio.to_thread(
                        reminder_create, title=title, due_date=now_str, notes=notes
                    )

                if ok:
                    logger.info(f"[scheduler] Departure alert: {title}")
    except Exception:
        logger.exception("[scheduler] _process_departure_alerts failed")


async def _scheduler_loop() -> None:
    """Background loop: check scheduled messages, followups, departures every 30s."""
    logger.info("[scheduler] Background loop started")
    while True:
        try:
            await asyncio.sleep(30)
            await _process_scheduled_messages()
            await _process_followup_reminders()
            await _process_departure_alerts()
        except asyncio.CancelledError:
            logger.info("[scheduler] Background loop cancelled")
            raise
        except Exception:
            logger.exception("[scheduler] Loop iteration failed")


# ── App lifecycle ────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = await asyncio.to_thread(init_contacts)
    print(f"Loaded {n} contacts")

    gmail, cal, drive, sheets, docs, tasks = await asyncio.to_thread(google_auth_all)
    state.gmail_services = gmail
    state.cal_services = cal
    state.drive_services = drive
    state.sheets_services = sheets
    state.docs_services = docs
    state.tasks_services = tasks
    print(
        f"Gmail accounts: {list(gmail.keys())}, "
        f"Calendar accounts: {list(cal.keys())}, "
        f"Drive accounts: {list(drive.keys())}, "
        f"Sheets accounts: {list(sheets.keys())}, "
        f"Docs accounts: {list(docs.keys())}, "
        f"Tasks accounts: {list(tasks.keys())}"
    )

    # Pre-warm conversation cache if enabled (reduces cold-start latency)
    if os.environ.get("INBOX_PRE_WARM_CONVERSATIONS", "").strip() in ("1", "true", "yes"):
        try:
            results = await _fetch_conversations("all", limit=50)
            state.conv_cache.clear()
            for c in results:
                state.conv_cache[_cache_key(c.source, c.id)] = c
            print(f"Pre-warmed {len(state.conv_cache)} conversations")
        except Exception:
            logger.warning("Pre-warm conversations failed (non-fatal)")

    # Ambient autostart — can be disabled by env and otherwise respects voice config
    try:
        disable_ambient = os.environ.get("INBOX_DISABLE_AMBIENT", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        voice_cfg = load_voice_config()
        if disable_ambient:
            print("[ambient] Autostart disabled by INBOX_DISABLE_AMBIENT")
        elif voice_cfg.get("ambient_autostart", False):
            avail, reason = ambient_available()
            if avail:
                state.ambient.start()
                print("[ambient] Auto-started ambient listening")
            else:
                print(f"[ambient] Autostart skipped: {reason}")
    except Exception:
        logger.warning("Ambient autostart failed (non-fatal)")

    # Start background scheduler loop (message scheduling + followup reminders)
    scheduler_task = asyncio.create_task(_scheduler_loop())

    # Start G2 ambient agents — each runs independently at its own cadence
    g2_tasks = await g2.start_g2_agents()

    try:
        yield
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await scheduler_task
        for t in g2_tasks:
            t.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(*g2_tasks, return_exceptions=True)
        state.ambient.stop()
        await asyncio.to_thread(close_sqlite_connections)


app = FastAPI(
    title="Inbox API",
    lifespan=lifespan,
    docs_url="/api-docs",
    redoc_url="/api-redoc",
    openapi_url="/api-openapi.json",
)

# Allow G2 phone WebView to connect (WebView origin varies by device/port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── G2 Routes ─────────────────────────────────────────────────────────────────

@app.websocket("/g2/ws")
async def g2_websocket(websocket: WebSocket):
    """G2 phone WebView connects here. Receives HUD commands, sends IMU state."""
    await g2.ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            g2.ws_manager.handle_message(data)
    except WebSocketDisconnect:
        g2.ws_manager.disconnect(websocket)
    except Exception:
        g2.ws_manager.disconnect(websocket)


@app.post("/g2/signal")
async def g2_inject_signal(body: dict):
    """Demo control panel calls this to inject a synthetic signal into the blackboard."""
    signal = g2.Signal(
        agent_id=body.get("agent_id", "demo"),
        priority=int(body.get("priority", 5)),
        category=body.get("category", "system"),
        data=body.get("data", {}),
        ttl=float(body.get("ttl", 120)),
    )
    await g2.blackboard.inject(signal)
    decision = None
    if body.get("demo_mode", True):
        decision = await g2.apply_demo_arbitration()
    return {"ok": True, "decision": decision}


@app.get("/g2/blackboard")
async def g2_blackboard_state():
    """Demo panel polls this to show the live blackboard."""
    signals = await g2.blackboard.snapshot()
    return {
        "signals": signals,
        "attention_state": g2._attention_state,
        "stats": {
            "evaluated": g2.blackboard.total_evaluated,
            "shown": g2.blackboard.total_shown,
            "suppressed": g2.blackboard.total_suppressed,
        },
        "last_reasoning": g2.blackboard.last_arbitrator_reasoning,
    }


@app.post("/g2/hud")
async def g2_push_hud(body: dict):
    """Demo panel direct-push: bypass blackboard and send straight to HUD."""
    await g2.ws_manager.broadcast_hud(
        body.get("line1", "")[:28],
        body.get("line2", "")[:28],
    )
    return {"ok": True}


@app.post("/g2/clear")
async def g2_clear_hud():
    """Clear the HUD display."""
    await g2.ws_manager.broadcast_clear()
    return {"ok": True}


@app.post("/g2/reset")
async def g2_reset_demo():
    """Reset demo state: clear HUD, blackboard, stats, and reasoning."""
    await g2.blackboard.reset()
    await g2.ws_manager.broadcast_clear()
    return {"ok": True}


def _auth_token() -> str:
    return os.getenv(AUTH_TOKEN_ENV, "").strip()


def _is_authorized(request: Request) -> bool:
    token = _auth_token()
    if not token:
        return True

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        provided = auth_header[7:].strip()
        if provided and compare_digest(provided, token):
            return True

    api_key = request.headers.get("x-api-key", "").strip()
    return bool(api_key) and compare_digest(api_key, token)


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if not _is_authorized(request):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    from services import _github_token

    return {
        "status": "ok",
        "gmail_accounts": list(state.gmail_services.keys()),
        "calendar_accounts": list(state.cal_services.keys()),
        "drive_accounts": list(state.drive_services.keys()),
        "sheets_accounts": list(state.sheets_services.keys()),
        "github_configured": _github_token() is not None,
    }


# ── Conversations ────────────────────────────────────────────────────────────


async def _fetch_conversations(source: str, limit: int, account: str = "") -> list[Contact]:
    """Fetch conversations from all requested sources in parallel.

    iMessage and Gmail fetches run concurrently via asyncio.gather().
    Multiple Gmail accounts are also fetched concurrently.
    """
    fetch_tasks: list[asyncio.Task[list[Contact]]] = []

    if source in ("all", "imessage"):
        fetch_tasks.append(asyncio.create_task(asyncio.to_thread(imsg_contacts, limit=limit)))

    if source in ("all", "gmail"):
        targets = (
            {account: state.gmail_services[account]}
            if account and account in state.gmail_services
            else state.gmail_services
        )
        for email, svc in targets.items():
            fetch_tasks.append(
                asyncio.create_task(asyncio.to_thread(gmail_contacts, svc, email, limit=limit))
            )

    if not fetch_tasks:
        return []

    result_lists = await asyncio.gather(*fetch_tasks)
    results: list[Contact] = []
    for contacts in result_lists:
        results.extend(contacts)

    return results


@app.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(source: str = "all", limit: int = 50, account: str = ""):
    results = await _fetch_conversations(source, limit, account)

    results.sort(key=lambda c: c.last_ts, reverse=True)

    # Update cache
    state.conv_cache.clear()
    for c in results:
        state.conv_cache[_cache_key(c.source, c.id)] = c

    return [_contact_to_out(c) for c in results]


# ── Messages ─────────────────────────────────────────────────────────────────


@app.get("/messages/{source}/{conv_id}", response_model=list[MessageOut])
async def get_messages(source: str, conv_id: str, thread_id: str = "", limit: int = 50):
    if source == "imessage":
        msgs = await asyncio.to_thread(imsg_thread, conv_id, limit=limit)
    elif source == "gmail":
        # Find the right service
        contact = state.conv_cache.get(_cache_key("gmail", conv_id))
        if contact and contact.gmail_account in state.gmail_services:
            svc = state.gmail_services[contact.gmail_account]
        elif state.gmail_services:
            svc = next(iter(state.gmail_services.values()))
        else:
            raise HTTPException(404, "No Gmail service available")
        tid = thread_id or (contact.thread_id if contact else "")
        msgs = await asyncio.to_thread(gmail_thread, svc, conv_id, tid)
    else:
        raise HTTPException(400, f"Unknown source: {source}")

    return [_msg_to_out(m) for m in msgs]


@app.post("/messages/send")
async def send_message(req: SendRequest):
    contact = state.conv_cache.get(_cache_key(req.source, req.conv_id))
    if not contact:
        raise HTTPException(404, "Conversation not found in cache — call /conversations first")

    if req.source == "imessage":
        ok = await asyncio.to_thread(imsg_send, contact, req.text)
    elif req.source == "gmail":
        svc = state.gmail_services.get(contact.gmail_account)
        if not svc:
            raise HTTPException(404, "Gmail account not found")
        ok = await asyncio.to_thread(gmail_send, svc, contact, req.text)
    else:
        raise HTTPException(400, f"Unknown source: {req.source}")

    return {"ok": ok}


# ── Gmail actions ────────────────────────────────────────────────────────────


def _get_gmail_service(msg_id: str) -> tuple[object, Contact | None]:
    """Look up the correct Gmail service for a message, using cache or fallback."""
    contact = state.conv_cache.get(_cache_key("gmail", msg_id))
    if contact and contact.gmail_account in state.gmail_services:
        return state.gmail_services[contact.gmail_account], contact
    default_acct = _default_google_account(state.gmail_services)
    if default_acct:
        return state.gmail_services[default_acct], contact
    raise HTTPException(404, "No Gmail service available")


def _get_gmail_service_for_account(account: str = "") -> tuple[str, object]:
    acct = account or _default_google_account(state.gmail_services)
    svc = state.gmail_services.get(acct)
    if not svc:
        raise HTTPException(404, "No Gmail account available")
    return acct, svc


def _gmail_message_or_thread_exists(service: object, msg_id: str = "", thread_id: str = "") -> bool:
    """Return True if the Gmail message or thread exists in the given mailbox."""
    try:
        if msg_id:
            (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["Message-ID"],
                )
                .execute()
            )
            return True
        if thread_id:
            service.users().threads().get(userId="me", id=thread_id, format="metadata").execute()
            return True
    except Exception:
        return False
    return False


def _get_gmail_service_for_message(
    msg_id: str = "",
    thread_id: str = "",
    account: str = "",
) -> tuple[str, object]:
    """Resolve the mailbox that owns a Gmail message/thread, falling back conservatively."""
    if account:
        return _get_gmail_service_for_account(account)

    for cache_id in filter(None, [msg_id, thread_id]):
        contact = state.conv_cache.get(_cache_key("gmail", cache_id))
        if contact and contact.gmail_account in state.gmail_services:
            return contact.gmail_account, state.gmail_services[contact.gmail_account]

    for acct, svc in state.gmail_services.items():
        if _gmail_message_or_thread_exists(svc, msg_id=msg_id, thread_id=thread_id):
            return acct, svc

    return _get_gmail_service_for_account("")


def _get_sheets_service_for_account(account: str = "") -> tuple[str, object]:
    acct = account or _default_google_account(state.sheets_services)
    svc = state.sheets_services.get(acct)
    if not svc:
        raise HTTPException(404, "No Sheets account available")
    return acct, svc


def _default_google_account(services: dict[str, object]) -> str:
    """Pick the default account for a Google service.

    Priority: INBOX_DEFAULT_GOOGLE_ACCOUNT env var if present in services,
    then first service key.
    """
    preferred = os.environ.get("INBOX_DEFAULT_GOOGLE_ACCOUNT", "").strip()
    if preferred and preferred in services:
        return preferred
    return next(iter(services)) if services else ""


def _get_drive_service_for_account(account: str = "") -> tuple[str, object]:
    acct = account or _default_google_account(state.drive_services)
    svc = state.drive_services.get(acct)
    if not svc:
        raise HTTPException(404, "No Drive account available")
    return acct, svc


def _get_tasks_service_for_account(account: str = "") -> tuple[str, object]:
    acct = account or _default_google_account(state.tasks_services)
    svc = state.tasks_services.get(acct)
    if not svc:
        raise HTTPException(404, "No Tasks account available")
    return acct, svc


@app.post("/messages/gmail/{msg_id}/archive")
async def archive_gmail(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    ok = await asyncio.to_thread(gmail_archive, svc, msg_id)
    return {"ok": ok}


@app.post("/messages/gmail/{msg_id}/delete")
async def delete_gmail(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    ok = await asyncio.to_thread(gmail_delete, svc, msg_id)
    return {"ok": ok}


@app.post("/messages/gmail/{msg_id}/unsubscribe")
async def unsubscribe_gmail(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    result = await asyncio.to_thread(gmail_unsubscribe, svc, msg_id)
    if not result["raw"]:
        raise HTTPException(422, "No List-Unsubscribe header found")
    return result


@app.post("/messages/gmail/bulk-unsubscribe")
async def bulk_unsubscribe_gmail(req: BulkUnsubscribeRequest):
    """Unsubscribe from multiple emails in parallel."""
    results = []
    for msg_id in req.msg_ids:
        try:
            svc, _ = _get_gmail_service(msg_id)
            result = await asyncio.to_thread(gmail_unsubscribe, svc, msg_id)
            results.append({"msg_id": msg_id, **result})
        except Exception as e:
            results.append({"msg_id": msg_id, "error": str(e)})

    return {"total": len(req.msg_ids), "results": results}


@app.post("/messages/gmail/{msg_id}/star")
async def star_gmail(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    ok = await asyncio.to_thread(gmail_star, svc, msg_id)
    return {"ok": ok}


@app.post("/messages/gmail/{msg_id}/unstar")
async def unstar_gmail(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    ok = await asyncio.to_thread(gmail_unstar, svc, msg_id)
    return {"ok": ok}


@app.post("/messages/gmail/{msg_id}/read")
async def mark_gmail_read(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    ok = await asyncio.to_thread(gmail_mark_read, svc, msg_id)
    return {"ok": ok}


@app.post("/messages/gmail/{msg_id}/unread")
async def mark_gmail_unread(msg_id: str):
    svc, _ = _get_gmail_service(msg_id)
    ok = await asyncio.to_thread(gmail_mark_unread, svc, msg_id)
    return {"ok": ok}


@app.get("/gmail/labels")
async def list_gmail_labels(account: str = ""):
    acct = account or (next(iter(state.gmail_services)) if state.gmail_services else "")
    svc = state.gmail_services.get(acct)
    if not svc:
        raise HTTPException(404, "No Gmail account available")
    labels = await asyncio.to_thread(gmail_labels, svc)
    return labels


@app.get("/messages/gmail/{msg_id}/attachments/{att_id}")
async def download_gmail_attachment(msg_id: str, att_id: str):
    svc, _ = _get_gmail_service(msg_id)
    data = await asyncio.to_thread(gmail_attachment_download, svc, msg_id, att_id)
    if data is None:
        raise HTTPException(404, "Attachment not found")
    return {"data": base64.urlsafe_b64encode(data).decode(), "size": len(data)}


@app.post("/messages/compose")
async def compose_email(req: ComposeRequest):
    _, svc = _get_gmail_service_for_account(req.account)
    ok = await asyncio.to_thread(gmail_compose_send, svc, req.to, req.subject, req.body)
    return {"ok": ok}


@app.post("/messages/gmail/reply")
async def reply_gmail(req: GmailReplyRequest):
    acct, svc = _get_gmail_service_for_message(req.msg_id, req.thread_id, req.account)
    ok = await asyncio.to_thread(
        gmail_reply,
        svc,
        req.msg_id,
        req.body,
        req.thread_id,
        req.to,
        req.subject,
        req.message_id_header,
    )
    return {"ok": ok, "account": acct}


@app.get("/gmail/conversations", response_model=list[ConversationOut])
async def list_gmail_by_label(label: str = "INBOX", limit: int = 50, account: str = ""):
    """List Gmail conversations filtered by label."""
    results: list[Contact] = []
    targets = (
        {account: state.gmail_services[account]}
        if account and account in state.gmail_services
        else state.gmail_services
    )
    for email, svc in targets.items():
        contacts = await asyncio.to_thread(
            gmail_contacts_by_label, svc, email, label_id=label, limit=limit
        )
        results.extend(contacts)

    results.sort(key=lambda c: c.last_ts, reverse=True)

    # Update cache with these results
    for c in results:
        state.conv_cache[_cache_key(c.source, c.id)] = c

    return [_contact_to_out(c) for c in results]


@app.get("/gmail/search", response_model=list[ConversationOut])
async def search_gmail(
    q: str = "",
    limit: int = 20,
    label: str = "",
    from_filter: str = "",
    subject_filter: str = "",
    after: str = "",
    before: str = "",
    account: str = "",
):
    results: list[Contact] = []
    targets = (
        {account: state.gmail_services[account]}
        if account and account in state.gmail_services
        else state.gmail_services
    )
    for email, svc in targets.items():
        contacts = await asyncio.to_thread(
            gmail_search,
            svc,
            email,
            q,
            limit,
            label,
            from_filter,
            subject_filter,
            after,
            before,
        )
        results.extend(contacts)

    results.sort(key=lambda c: c.last_ts, reverse=True)
    for c in results:
        state.conv_cache[_cache_key(c.source, c.id)] = c
    return [_contact_to_out(c) for c in results[:limit]]


# ── Thread summaries ───────────────────────────────────────────────────────────


@app.get("/gmail/threads/{thread_id}/summary", response_model=GmailThreadSummaryOut)
async def get_gmail_thread_summary(thread_id: str, account: str = ""):
    acct, svc = _get_gmail_service_for_message(thread_id=thread_id, account=account)
    ts = await asyncio.to_thread(gmail_thread_summary, svc, thread_id, acct)
    if ts is None:
        raise HTTPException(404, "Thread not found")
    label_data = await asyncio.to_thread(gmail_labels, svc)
    label_map = {lbl["id"]: lbl["name"] for lbl in label_data}
    return _thread_summary_to_out(ts, label_map)


@app.get("/gmail/thread-summaries", response_model=list[GmailThreadSummaryOut])
async def search_gmail_thread_summaries(
    q: str = "",
    workflow: str = "",
    needs_reply: bool | None = None,
    account: str = "",
    limit: int = 20,
):
    """Search Gmail and return normalized thread summaries with workflow tags."""
    acct, svc = _get_gmail_service_for_account(account)
    contacts = await asyncio.to_thread(gmail_search, svc, acct, q, limit * 3)
    seen: set[str] = set()
    summaries: list[GmailThreadSummaryOut] = []
    for c in contacts:
        tid = c.thread_id or c.id
        if tid in seen:
            continue
        seen.add(tid)
        ts = _contact_to_thread_summary(c)
        if workflow and ts.workflow != workflow:
            continue
        if needs_reply is not None and ts.needs_reply != needs_reply:
            continue
        summaries.append(ts)
    summaries.sort(key=lambda t: t.rank, reverse=True)
    return summaries[:limit]


@app.get("/gmail/thread-briefs", response_model=list[ThreadBriefOut])
async def get_gmail_thread_briefs(
    q: str = "",
    workflow: str = "",
    needs_reply: bool | None = None,
    account: str = "",
    limit: int = 20,
):
    """Ultra-compact thread list for triage — brief + rank only, no body or participants."""
    acct, svc = _get_gmail_service_for_account(account)
    contacts = await asyncio.to_thread(gmail_search, svc, acct, q, limit * 3)
    seen: set[str] = set()
    raw: list[GmailThreadSummaryOut] = []
    for c in contacts:
        tid = c.thread_id or c.id
        if tid in seen:
            continue
        seen.add(tid)
        ts = _contact_to_thread_summary(c)
        if workflow and ts.workflow != workflow:
            continue
        if needs_reply is not None and ts.needs_reply != needs_reply:
            continue
        raw.append(ts)
    raw.sort(key=lambda t: t.rank, reverse=True)
    return [
        ThreadBriefOut(
            thread_id=t.thread_id,
            brief=t.brief,
            rank=t.rank,
            workflow=t.workflow,
            needs_reply=t.needs_reply,
        )
        for t in raw[:limit]
    ]


@app.post("/gmail/batch-modify")
async def batch_modify_gmail(req: GmailBatchModifyRequest):
    acct, svc = _get_gmail_service_for_account(req.account)
    ok = await asyncio.to_thread(
        gmail_batch_modify,
        svc,
        req.msg_ids,
        req.add_label_ids,
        req.remove_label_ids,
    )
    return {"ok": ok, "account": acct, "count": len(req.msg_ids)}


@app.post("/gmail/filters")
async def create_gmail_filter(req: GmailFilterCreateRequest):
    acct, svc = _get_gmail_service_for_account(req.account)
    criteria = {
        "from": req.from_filter,
        "to": req.to_filter,
        "subject": req.subject_filter,
        "query": req.query or req.has_words,
        "negatedQuery": req.does_not_have_words,
    }
    result = await asyncio.to_thread(
        gmail_create_filter,
        svc,
        criteria,
        req.add_label_ids,
        req.remove_label_ids,
        req.forward,
    )
    if not result:
        raise HTTPException(
            400,
            "Failed to create Gmail filter. Re-auth may be required for gmail.settings.basic scope.",
        )
    return {"ok": True, "account": acct, "filter": result}


# ── Calendar ─────────────────────────────────────────────────────────────────


@app.get("/calendar/events", response_model=list[CalendarEventOut])
async def list_events(
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    if start and end:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        evts = await asyncio.to_thread(
            calendar_events,
            state.cal_services,
            start_date=start_dt,
            end_date=end_dt,
        )
    else:
        dt = datetime.fromisoformat(date) if date else None
        evts = await asyncio.to_thread(calendar_events, state.cal_services, dt)
    state.events_cache = evts
    return [_event_to_out(e) for e in evts]


@app.get("/calendar/upcoming", response_model=list[CalendarEventOut])
async def list_upcoming_events(days: int = 7):
    days = max(1, min(days, 30))
    start_dt = datetime.now()
    end_dt = start_dt + timedelta(days=days - 1)
    evts = await asyncio.to_thread(
        calendar_events,
        state.cal_services,
        start_date=start_dt,
        end_date=end_dt,
    )
    state.events_cache = evts
    return [_event_to_out(e) for e in evts]


@app.post("/calendar/events")
async def create_event(req: CreateEventRequest):
    account, svc = _get_cal_service_for_account(req.account)

    try:
        event_id = await asyncio.to_thread(
            calendar_create_event,
            svc,
            summary=req.summary,
            start=datetime.fromisoformat(req.start),
            end=datetime.fromisoformat(req.end),
            location=req.location,
            description=req.description,
            all_day=req.all_day,
            attendees=req.attendees,
        )
        return {"ok": True, "event_id": event_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to create event: {str(e)}") from e


@app.post("/calendar/events/quick")
async def create_quick_event(req: QuickEventRequest):
    account, svc = _get_cal_service_for_account(req.account)

    parsed = parse_quick_event(req.text)
    try:
        event_id = await asyncio.to_thread(
            calendar_create_event,
            svc,
            summary=parsed["summary"],
            start=parsed["start"],
            end=parsed["end"],
            location=parsed.get("location", ""),
            all_day=parsed.get("all_day", False),
        )
        return {"ok": True, "event_id": event_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to create event: {str(e)}") from e


@app.put("/calendar/events/{event_id}")
async def update_event(
    event_id: str,
    req: UpdateEventRequest,
    calendar_id: str = "primary",
    account: str = "",
):
    acct, svc = _get_cal_service_for_account(account)

    ok = await asyncio.to_thread(
        calendar_update_event,
        svc,
        event_id,
        summary=req.summary,
        start=(datetime.fromisoformat(req.start) if req.start else None),
        end=datetime.fromisoformat(req.end) if req.end else None,
        location=req.location,
        description=req.description,
        calendar_id=calendar_id,
    )
    return {"ok": ok}


@app.delete("/calendar/events/{event_id}")
async def delete_event(
    event_id: str,
    calendar_id: str = "primary",
    account: str = "",
):
    acct, svc = _get_cal_service_for_account(account)

    ok = await asyncio.to_thread(calendar_delete_event, svc, event_id, calendar_id)
    return {"ok": ok}


# ── Notes ────────────────────────────────────────────────────────────────────


@app.get("/notes", response_model=list[NoteOut])
async def list_notes(limit: int = 50):
    notes = await asyncio.to_thread(notes_list, limit=limit)
    return [_note_to_out(n) for n in notes]


@app.get("/notes/{note_id}")
async def get_note(note_id: str):
    notes = await asyncio.to_thread(notes_list, limit=500)
    note = next((n for n in notes if n.id == note_id), None)
    if not note:
        raise HTTPException(404, "Note not found")
    body = await asyncio.to_thread(note_body, note.title)
    return {
        "id": note.id,
        "title": note.title,
        "body": body or note.snippet,
        "modified": note.modified.isoformat(),
        "folder": note.folder,
    }


# ── Reminders ────────────────────────────────────────────────────────────────


@app.get("/reminders/lists")
async def list_reminder_lists():
    lists = await asyncio.to_thread(reminders_lists)
    return lists


@app.get("/reminders", response_model=list[ReminderOut])
async def list_reminders(
    list_name: str | None = None,
    show_completed: bool = False,
    limit: int = 100,
):
    items = await asyncio.to_thread(
        reminders_list,
        list_name=list_name,
        show_completed=show_completed,
        limit=limit,
    )
    return [_reminder_to_out(r) for r in items]


@app.post("/reminders/{reminder_id}/complete")
async def complete_reminder(reminder_id: str):
    reminder = await asyncio.to_thread(reminder_by_id, reminder_id)
    if not reminder:
        raise HTTPException(404, "Reminder not found")
    ok = await asyncio.to_thread(reminder_complete, reminder.title, reminder.list_name)
    return {"ok": ok}


@app.post("/reminders/{reminder_id}/uncomplete")
async def uncomplete_reminder(reminder_id: str):
    reminder = await asyncio.to_thread(reminder_by_id, reminder_id)
    if not reminder:
        raise HTTPException(404, "Reminder not found")
    ok = await asyncio.to_thread(reminder_uncomplete, reminder.title, reminder.list_name)
    return {"ok": ok}


@app.post("/reminders")
async def create_reminder(req: ReminderCreateRequest):
    ok = await asyncio.to_thread(
        reminder_create,
        title=req.title,
        list_name=req.list_name,
        due_date=req.due_date,
        notes=req.notes,
        priority=req.priority,
        flagged=req.flagged,
    )
    return {"ok": ok}


@app.put("/reminders/{reminder_id}")
async def edit_reminder(reminder_id: str, req: ReminderEditRequest):
    reminder = await asyncio.to_thread(reminder_by_id, reminder_id)
    if not reminder:
        raise HTTPException(404, "Reminder not found")
    ok = await asyncio.to_thread(
        reminder_edit,
        current_title=reminder.title,
        title=req.title,
        due_date=req.due_date,
        notes=req.notes,
        list_name=reminder.list_name,
        priority=req.priority,
        flagged=req.flagged,
    )
    return {"ok": ok}


@app.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: str):
    reminder = await asyncio.to_thread(reminder_by_id, reminder_id)
    if not reminder:
        raise HTTPException(404, "Reminder not found")
    ok = await asyncio.to_thread(reminder_delete, reminder.title, reminder.list_name)
    return {"ok": ok}


# ── Google Tasks ─────────────────────────────────────────────────────────────


@app.get("/tasks/lists", response_model=list[dict])
async def list_task_lists(account: str = ""):
    _, svc = _get_tasks_service_for_account(account)
    return await asyncio.to_thread(tasks_lists, svc)


@app.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    list_id: str = "@default",
    show_completed: bool = False,
    limit: int = 100,
    account: str = "",
    workflow: str = "",
):
    acct, svc = _get_tasks_service_for_account(account)
    raw = await asyncio.to_thread(tasks_list, svc, list_id, show_completed, limit)
    out = [_task_to_out(t, acct) for t in raw]
    if workflow:
        out = [t for t in out if t.workflow == workflow]
    return out


@app.post("/tasks")
async def create_task(req: TaskCreateRequest, account: str = ""):
    _, svc = _get_tasks_service_for_account(account)
    ok = await asyncio.to_thread(task_create, svc, req.title, req.list_id, req.due, req.notes)
    return {"ok": ok}


@app.post("/tasks/{task_id}/complete")
async def complete_task(task_id: str, list_id: str = "@default", account: str = ""):
    _, svc = _get_tasks_service_for_account(account)
    ok = await asyncio.to_thread(task_complete, svc, task_id, list_id)
    return {"ok": ok}


@app.put("/tasks/{task_id}")
async def update_task(
    task_id: str, req: TaskUpdateRequest, list_id: str = "@default", account: str = ""
):
    _, svc = _get_tasks_service_for_account(account)
    ok = await asyncio.to_thread(task_update, svc, task_id, list_id, req.title, req.due, req.notes)
    return {"ok": ok}


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str, list_id: str = "@default", account: str = ""):
    _, svc = _get_tasks_service_for_account(account)
    ok = await asyncio.to_thread(task_delete, svc, task_id, list_id)
    return {"ok": ok}


# ── Scheduled Messages ───────────────────────────────────────────────────────


@app.get("/scheduled")
async def list_scheduled_messages(status: str = "pending"):
    return await asyncio.to_thread(state.scheduler.list_scheduled, status)


@app.post("/scheduled")
async def create_scheduled_message(req: ScheduleMessageRequest):
    result = await asyncio.to_thread(
        state.scheduler.schedule_message,
        req.source,
        req.conv_id,
        req.text,
        req.send_at,
        req.account,
    )
    return result


@app.delete("/scheduled/{msg_id}")
async def cancel_scheduled_message(msg_id: int):
    ok = await asyncio.to_thread(state.scheduler.cancel_scheduled, msg_id)
    return {"ok": ok}


# ── Follow-up Reminders ──────────────────────────────────────────────────────


@app.get("/followups")
async def list_followup_reminders(status: str = "active"):
    return await asyncio.to_thread(state.scheduler.list_followups, status)


@app.post("/followups")
async def create_followup_reminder(req: FollowupCreateRequest):
    result = await asyncio.to_thread(
        state.scheduler.create_followup,
        req.source,
        req.conv_id,
        req.thread_id,
        req.remind_after,
        req.reminder_title,
        req.reminder_list,
    )
    return result


@app.delete("/followups/{fid}")
async def cancel_followup_reminder(fid: int):
    ok = await asyncio.to_thread(state.scheduler.cancel_followup, fid)
    return {"ok": ok}


# ── Task ↔ Message Links ─────────────────────────────────────────────────────


@app.get("/tasks/links")
async def list_task_links(
    message_id: str = "",
    message_source: str = "",
    task_id: str = "",
    task_source: str = "",
):
    if message_id and message_source:
        return await asyncio.to_thread(
            state.scheduler.links_for_message, message_id, message_source
        )
    if task_id and task_source:
        return await asyncio.to_thread(state.scheduler.links_for_task, task_id, task_source)
    raise HTTPException(400, "Must provide (message_id, message_source) OR (task_id, task_source)")


@app.post("/tasks/links")
async def create_task_link(req: TaskLinkRequest):
    result = await asyncio.to_thread(
        state.scheduler.link_task,
        req.task_id,
        req.task_source,
        req.message_id,
        req.message_source,
        req.thread_id,
        req.account,
    )
    return result


@app.delete("/tasks/links/{link_id}")
async def delete_task_link(link_id: int):
    ok = await asyncio.to_thread(state.scheduler.unlink_task, link_id)
    return {"ok": ok}


@app.post("/tasks/from-message")
async def create_task_from_message(req: TaskFromMessageRequest):
    """Create a task from a message and auto-link it."""
    task_id = ""
    if req.task_type == "google_tasks":
        _, svc = _get_tasks_service_for_account(req.account)
        # Include message reference in notes
        notes = req.notes
        if req.message_source == "gmail":
            notes = f"{notes}\n\nFrom email: {req.message_id}".strip()
        ok = await asyncio.to_thread(task_create, svc, req.title, req.list_id, "", notes)
        if not ok:
            raise HTTPException(500, "Failed to create Google Task")
        # Query newly-created task id (Google Tasks API doesn't return it from task_create)
        tasks = await asyncio.to_thread(tasks_list, svc, req.list_id, False, 10)
        latest = next((t for t in tasks if t.title == req.title), None)
        task_id = latest.id if latest else ""
    elif req.task_type == "reminders":
        notes = req.notes
        if req.message_source == "gmail":
            notes = f"{notes}\n\nFrom email: {req.message_id}".strip()
        ok = await asyncio.to_thread(reminder_create, req.title, req.list_name, "", notes, 0, False)
        if not ok:
            raise HTTPException(500, "Failed to create Reminder")
        # Reminder ids come from SQLite Z_PK — not easy to get the just-created one
        task_id = req.title  # fallback: use title as identifier
    else:
        raise HTTPException(400, f"Unknown task_type: {req.task_type}")

    # Auto-link
    link = await asyncio.to_thread(
        state.scheduler.link_task,
        task_id,
        req.task_type,
        req.message_id,
        req.message_source,
        req.thread_id,
        req.account,
    )
    return {"ok": True, "task_id": task_id, "link": link}


# ── Gemini AI ────────────────────────────────────────────────────────────────


@app.post("/ai/gemini-summarize")
async def ai_gemini_summarize(messages: list[dict]):
    result = await asyncio.to_thread(gemini_summarize, messages)
    if result is None:
        raise HTTPException(502, "Gemini summarization failed")
    return {"summary": result}


@app.post("/ai/smart-reply")
async def ai_gemini_smart_reply(messages: list[dict], num_replies: int = 3):
    replies = await asyncio.to_thread(gemini_smart_reply, messages, num_replies)
    return {"replies": replies}


@app.post("/ai/categorize")
async def ai_gemini_categorize(emails: list[dict]):
    categories = await asyncio.to_thread(gemini_categorize, emails)
    return {"categories": categories}


@app.post("/ai/digest")
async def ai_gemini_digest():
    """Generate morning digest from all sources."""
    # Gather data from all sources
    emails_raw = await _fetch_conversations("gmail", limit=20)
    emails = [
        {"name": c.name, "snippet": c.snippet, "id": c.id} for c in emails_raw if c.unread > 0
    ]

    events_raw = await asyncio.to_thread(calendar_events, state.cal_services)
    events = [
        {"summary": e.summary, "start": e.start.isoformat(), "location": e.location}
        for e in events_raw
    ]

    tasks_data = []
    if state.tasks_services:
        try:
            _, svc = _get_tasks_service_for_account("")
            tasks_raw = await asyncio.to_thread(tasks_list, svc, "@default", False, 20)
            tasks_data = [
                {"title": t.title, "due": t.due.isoformat() if t.due else ""} for t in tasks_raw
            ]
        except Exception:
            pass

    rem_raw = await asyncio.to_thread(reminders_list, limit=20)
    reminders_data = [
        {"title": r.title, "due_date": r.due_date.isoformat() if r.due_date else ""}
        for r in rem_raw
    ]

    notifs = await asyncio.to_thread(github_notifications)
    notifs_data = [{"title": n.title, "repo": n.repo, "type": n.type} for n in notifs if n.unread]

    result = await asyncio.to_thread(
        gemini_digest, emails, events, tasks_data, reminders_data, notifs_data
    )
    if result is None:
        raise HTTPException(502, "Gemini digest failed")
    return {"digest": result}


@app.post("/ai/action-items")
async def ai_gemini_action_items(messages: list[dict]):
    items = await asyncio.to_thread(gemini_extract_action_items, messages)
    return {"action_items": items}


# ── Departure Times (Google Maps) ────────────────────────────────────────────


@app.get("/calendar/departure-times")
async def get_departure_times(
    origin: str = "",
    mode: str = "driving",
    buffer_minutes: int = 10,
    lookahead_hours: int = 24,
):
    """Calculate when to leave for upcoming calendar events with locations.

    Args:
        origin: Your starting address (home/office). If empty, uses INBOX_HOME_ADDRESS env var.
        mode: "driving" | "transit" | "walking" | "bicycling"
        buffer_minutes: Extra buffer time on top of travel estimate.
        lookahead_hours: Only check events within this many hours ahead.
    """
    # Try live location first, then env var, then error
    home = origin
    if not home:
        home = await asyncio.to_thread(get_current_location)
    if not home:
        raise HTTPException(
            400,
            "No origin. Set INBOX_HOME_ADDRESS env var, grant Location Services, or pass ?origin=",
        )

    # Fetch today's events from all calendar accounts
    events = await asyncio.to_thread(calendar_events, state.cal_services)
    departures = await asyncio.to_thread(
        departure_times_for_events,
        events,
        home,
        mode,
        buffer_minutes,
        lookahead_hours,
    )
    return [
        {
            "event_summary": d.event_summary,
            "event_start": d.event_start.isoformat(),
            "event_location": d.event_location,
            "travel_minutes": d.travel_minutes,
            "departure_time": d.departure_time.isoformat(),
            "distance_text": d.distance_text,
            "duration_text": d.duration_text,
            "mode": d.mode,
        }
        for d in departures
    ]


@app.get("/maps/travel-time")
async def get_travel_time(
    origin: str,
    destination: str,
    mode: str = "driving",
    avoid: str | None = None,
    units: str = "imperial",
):
    """Get travel time between two locations.

    Args:
        avoid: "tolls", "highways", "ferries", or combo like "tolls|highways".
        units: "imperial" (miles) or "metric" (km).
    """
    result = await asyncio.to_thread(
        maps_travel_time, origin, destination, mode, None, avoid, units
    )
    if not result:
        raise HTTPException(502, "Could not get travel time — check Maps API key and addresses")
    return result


# ── WhatsApp ─────────────────────────────────────────────────────────────────


@app.get("/whatsapp/status")
async def whatsapp_status():
    """Return WhatsApp app state + Accessibility permission status."""
    from services import _whatsapp_pid

    pid = await asyncio.to_thread(_whatsapp_pid)
    trusted = await asyncio.to_thread(whatsapp_check_accessibility, False)
    return {"running": pid is not None, "pid": pid, "accessibility_granted": trusted}


@app.post("/whatsapp/launch")
async def launch_whatsapp(prompt_permission: bool = False):
    """Launch WhatsApp.app. If prompt_permission=True, also shows the macOS Accessibility dialog."""
    running = await asyncio.to_thread(whatsapp_launch, 5.0)
    trusted = await asyncio.to_thread(whatsapp_check_accessibility, prompt_permission)
    return {"running": running, "accessibility_granted": trusted}


@app.post("/whatsapp/send")
async def send_whatsapp(payload: dict):
    """Send a WhatsApp message. Body: {"chat_name": str, "text": str}."""
    chat = payload.get("chat_name", "").strip()
    text = payload.get("text", "").strip()
    if not chat or not text:
        raise HTTPException(400, "chat_name and text required")
    ok = await asyncio.to_thread(whatsapp_send, chat, text)
    if not ok:
        raise HTTPException(
            502, "send failed (app not running, chat not visible, or permission missing)"
        )
    return {"sent": True, "chat_name": chat}


@app.post("/whatsapp/scroll")
async def scroll_whatsapp(pages: int = 1):
    """Scroll the WhatsApp sidebar down N pages so more chats render."""
    done = await asyncio.to_thread(whatsapp_scroll_sidebar, pages)
    return {"pages_scrolled": done}


@app.get("/whatsapp/contacts/all", response_model=list[ConversationOut])
async def list_all_whatsapp_contacts(max_pages: int = 10):
    """Scroll the sidebar to collect all reachable chats (deduplicated)."""
    contacts = await asyncio.to_thread(whatsapp_contacts_all, max_pages)
    return [
        ConversationOut(
            id=c.id,
            name=c.name,
            source=c.source,
            snippet=c.snippet,
            unread=c.unread,
            last_ts=c.last_ts.isoformat(),
            guid=c.guid,
            is_group=c.is_group,
            members=c.members,
        )
        for c in contacts
    ]


@app.get("/whatsapp/messages/{chat_name}/full", response_model=list[MessageOut])
async def get_whatsapp_messages_full(chat_name: str, max_loads: int = 10, limit: int = 500):
    """Fetch full WhatsApp chat history via repeated 'Load more messages'."""
    messages = await asyncio.to_thread(whatsapp_thread_full, chat_name, max_loads, limit)
    return [
        MessageOut(
            sender=m.sender,
            body=m.body,
            ts=m.ts.isoformat(),
            is_me=m.is_me,
            source=m.source,
            attachments=m.attachments,
            message_id=m.message_id,
        )
        for m in messages
    ]


@app.get("/whatsapp/contacts", response_model=list[ConversationOut])
async def list_whatsapp_contacts(limit: int = 20):
    """List WhatsApp conversations via macOS Accessibility API (read-only).
    WhatsApp app must be running. Returns empty list if app is not running or AX tree inspection fails.
    """
    contacts = await asyncio.to_thread(whatsapp_contacts, limit)
    return [
        ConversationOut(
            id=c.id,
            name=c.name,
            source=c.source,
            snippet=c.snippet,
            unread=c.unread,
            last_ts=c.last_ts.isoformat(),
            guid=c.guid,
            is_group=c.is_group,
            members=c.members,
        )
        for c in contacts
    ]


@app.get("/whatsapp/messages/{chat_name}", response_model=list[MessageOut])
async def get_whatsapp_messages(chat_name: str, limit: int = 50):
    """Fetch WhatsApp messages for a conversation.
    chat_name: Name of the conversation.
    limit: Max messages to return.
    Placeholder: returns empty list pending AX tree navigation implementation.
    """
    messages = await asyncio.to_thread(whatsapp_thread, chat_name, limit)
    return [
        MessageOut(
            sender=m.sender,
            body=m.body,
            ts=m.ts.isoformat(),
            is_me=m.is_me,
            source=m.source,
            attachments=m.attachments,
            message_id=m.message_id,
        )
        for m in messages
    ]


# ── GitHub ───────────────────────────────────────────────────────────────────


@app.get("/github/notifications", response_model=list[GitHubNotificationOut])
async def list_github_notifications(all: bool = False):
    notifs = await asyncio.to_thread(github_notifications, all_notifs=all)
    return [_gh_notif_to_out(n) for n in notifs]


@app.post("/github/notifications/{notification_id}/read")
async def mark_github_read(notification_id: str):
    ok = await asyncio.to_thread(github_mark_read, notification_id)
    return {"ok": ok}


@app.post("/github/notifications/read-all")
async def mark_all_github_read():
    ok = await asyncio.to_thread(github_mark_all_read)
    return {"ok": ok}


@app.get("/github/pulls")
async def list_github_pulls(repo: str | None = None):
    pulls = await asyncio.to_thread(github_pulls, repo=repo)
    return pulls


# ── Google Drive ─────────────────────────────────────────────────────────────


@app.get("/drive/files", response_model=list[DriveFileOut])
async def list_drive_files(
    q: str = "",
    shared: bool = False,
    limit: int = 20,
    account: str = "",
    folder_id: str = "",
):
    results: list[DriveFileOut] = []
    targets = (
        {account: state.drive_services[account]}
        if account and account in state.drive_services
        else state.drive_services
    )
    for email, svc in targets.items():
        files = await asyncio.to_thread(
            drive_files,
            svc,
            query=q,
            limit=limit,
            shared_with_me=shared,
            folder_id=folder_id,
        )
        results.extend(_drive_to_out(f, account=email) for f in files)
    results.sort(key=lambda f: f.modified, reverse=True)
    return results[:limit]


@app.get("/drive/files/{file_id}/download")
async def download_drive_file(file_id: str, account: str = ""):
    from fastapi.responses import Response

    acct, svc = _get_drive_service_for_account(account)
    result = await asyncio.to_thread(drive_download, svc, file_id)
    if not result:
        raise HTTPException(404, "File not found or download failed")
    content, mime_type = result
    return Response(content=content, media_type=mime_type)


@app.get("/drive/files/{file_id}", response_model=DriveFileOut)
async def get_drive_file(file_id: str, account: str = ""):
    acct, svc = _get_drive_service_for_account(account)
    f = await asyncio.to_thread(drive_get, svc, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    return _drive_to_out(f, account=acct)


_file_field = File(...)


@app.post("/drive/upload", response_model=DriveFileOut)
async def upload_to_drive(
    file: UploadFile = _file_field,
    folder_id: str = "",
    account: str = "",
):
    import tempfile
    from pathlib import Path

    acct, svc = _get_drive_service_for_account(account)

    # Save upload to temp file, then upload to Drive
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await asyncio.to_thread(
            drive_upload, svc, tmp_path, folder_id=folder_id, name=file.filename or ""
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not result:
        raise HTTPException(500, "Upload failed")
    return _drive_to_out(result, account=acct)


@app.post("/drive/folder", response_model=DriveFileOut)
async def create_drive_folder(req: DriveCreateFolderRequest):
    acct, svc = _get_drive_service_for_account(req.account)
    result = await asyncio.to_thread(drive_create_folder, svc, req.name, parent_id=req.parent_id)
    if not result:
        raise HTTPException(500, "Failed to create folder")
    return _drive_to_out(result, account=acct)


@app.delete("/drive/files/{file_id}")
async def delete_drive_file(file_id: str, account: str = ""):
    acct, svc = _get_drive_service_for_account(account)
    ok = await asyncio.to_thread(drive_delete, svc, file_id)
    return {"ok": ok}


# ── Sheets ───────────────────────────────────────────────────────────────────


@app.get("/sheets", response_model=list[SpreadsheetOut])
async def list_sheets(q: str = "", limit: int = 20, account: str = ""):

    # List spreadsheets from Drive (need Drive service)
    if account and account in state.drive_services:
        drive_svcs = {account: state.drive_services[account]}
    else:
        drive_svcs = state.drive_services

    results = []
    for acct, drive_svc in drive_svcs.items():
        sheets = await asyncio.to_thread(sheets_list, drive_svc, q, limit, acct)
        results.extend(sheets)
    return [_spreadsheet_to_out(s, s.account) for s in results]


@app.post("/sheets", response_model=SpreadsheetOut)
async def create_spreadsheet(req: CreateSpreadsheetRequest):

    acct, sheets_svc = _get_sheets_service_for_account(req.account)
    result = await asyncio.to_thread(sheets_create, sheets_svc, req.title, req.sheets or [])
    if not result:
        raise HTTPException(400, "Failed to create spreadsheet")
    return _spreadsheet_to_out(result, acct)


@app.get("/sheets/{spreadsheet_id}", response_model=SpreadsheetOut)
async def get_spreadsheet(spreadsheet_id: str, account: str = ""):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(sheets_get, sheets_svc, spreadsheet_id)
    if not result:
        raise HTTPException(404, "Spreadsheet not found")
    return _spreadsheet_to_out(result, acct)


@app.delete("/sheets/{spreadsheet_id}")
async def delete_spreadsheet(spreadsheet_id: str, account: str = ""):

    acct, drive_svc = _get_drive_service_for_account(account)
    ok = await asyncio.to_thread(sheets_delete, drive_svc, spreadsheet_id)
    return {"ok": ok}


@app.get("/sheets/{spreadsheet_id}/values/{range_}")
async def read_range(spreadsheet_id: str, range_: str, account: str = ""):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(sheets_values_get, sheets_svc, spreadsheet_id, range_)
    if result is None:
        raise HTTPException(404, "Failed to read range")
    return {"range": range_, "values": result}


@app.put("/sheets/{spreadsheet_id}/values/{range_}")
async def update_range(
    spreadsheet_id: str, range_: str, req: SheetValuesUpdateRequest, account: str = ""
):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(
        sheets_values_update, sheets_svc, spreadsheet_id, range_, req.values, req.value_input
    )
    if result is None:
        raise HTTPException(400, "Failed to update range")
    return result


@app.post("/sheets/{spreadsheet_id}/values/{range_}/append")
async def append_range(
    spreadsheet_id: str, range_: str, req: SheetValuesUpdateRequest, account: str = ""
):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(
        sheets_values_append, sheets_svc, spreadsheet_id, range_, req.values, req.value_input
    )
    if result is None:
        raise HTTPException(400, "Failed to append range")
    return result


@app.delete("/sheets/{spreadsheet_id}/values/{range_}")
async def clear_range(spreadsheet_id: str, range_: str, account: str = ""):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    ok = await asyncio.to_thread(sheets_values_clear, sheets_svc, spreadsheet_id, range_)
    return {"ok": ok}


@app.post("/sheets/{spreadsheet_id}/values/batch-get")
async def batch_get_values(spreadsheet_id: str, req: BatchGetRequest, account: str = ""):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(
        sheets_values_batch_get, sheets_svc, spreadsheet_id, req.ranges
    )
    if result is None:
        raise HTTPException(404, "Failed to read ranges")
    return result


@app.post("/sheets/{spreadsheet_id}/values/batch-update")
async def batch_update_values(
    spreadsheet_id: str, req: SheetValuesBatchUpdateRequest, account: str = ""
):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(
        sheets_values_batch_update, sheets_svc, spreadsheet_id, req.data, req.value_input
    )
    if result is None:
        raise HTTPException(400, "Failed to batch update ranges")
    return result


@app.post("/sheets/{spreadsheet_id}/tabs", response_model=SheetTabOut)
async def add_sheet_tab(spreadsheet_id: str, req: AddSheetRequest):

    acct, sheets_svc = _get_sheets_service_for_account(req.account)
    result = await asyncio.to_thread(
        sheets_add_sheet, sheets_svc, spreadsheet_id, req.title, req.rows, req.cols
    )
    if not result:
        raise HTTPException(400, "Failed to add sheet tab")
    return _sheet_tab_to_out(result)


@app.delete("/sheets/{spreadsheet_id}/tabs/{sheet_id}")
async def delete_sheet_tab(spreadsheet_id: str, sheet_id: int, account: str = ""):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    ok = await asyncio.to_thread(sheets_delete_sheet, sheets_svc, spreadsheet_id, sheet_id)
    return {"ok": ok}


@app.patch("/sheets/{spreadsheet_id}/tabs/{sheet_id}")
async def rename_sheet_tab(spreadsheet_id: str, sheet_id: int, title: str, account: str = ""):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    ok = await asyncio.to_thread(sheets_rename_sheet, sheets_svc, spreadsheet_id, sheet_id, title)
    return {"ok": ok}


@app.post("/sheets/{spreadsheet_id}/tabs/{sheet_id}/copy")
async def copy_sheet_tab(
    spreadsheet_id: str, sheet_id: int, req: CopySheetRequest, account: str = ""
):

    acct, sheets_svc = _get_sheets_service_for_account(account)
    result = await asyncio.to_thread(
        sheets_copy_to, sheets_svc, spreadsheet_id, sheet_id, req.dest_spreadsheet_id
    )
    if not result:
        raise HTTPException(400, "Failed to copy sheet")
    return _sheet_tab_to_out(result)


@app.post("/sheets/{spreadsheet_id}/format")
async def format_spreadsheet(spreadsheet_id: str, req: FormatRequest):

    acct, sheets_svc = _get_sheets_service_for_account(req.account)
    result = await asyncio.to_thread(sheets_format, sheets_svc, spreadsheet_id, req.requests)
    if result is None:
        raise HTTPException(400, "Failed to apply formatting")
    return result


# ── Docs ─────────────────────────────────────────────────────────────────────


def _get_docs_service_for_account(account: str = "") -> tuple[str, object]:
    """Get docs service for account, return (account_email, service). Raises HTTPException on failure."""
    acct = account or _default_google_account(state.docs_services)
    if not acct or acct not in state.docs_services:
        raise HTTPException(400, "No docs service available")
    return acct, state.docs_services[acct]


_WORKFLOW_KEYWORDS: dict[str, list[str]] = {
    "job_hunt": [
        "recruiter",
        "hiring",
        "offer letter",
        "interview",
        "application",
        "linkedin",
        "resume",
        "salary",
        "compensation",
        "candidate",
        "onboarding",
        "job description",
        "cover letter",
        "job offer",
    ],
    "legal": [
        "attorney",
        "legal counsel",
        "contract",
        "lawsuit",
        "court",
        "settlement",
        "nda",
        "litigation",
        "subpoena",
        "non-disclosure",
    ],
    "medical": [
        "appointment",
        "prescription",
        "insurance claim",
        "clinic",
        "hospital",
        "lab result",
        "diagnosis",
        "patient",
        "referral",
        "copay",
        "doctor",
        "medical",
    ],
    "finance": [
        "invoice",
        "payment due",
        "tax return",
        "bank statement",
        "investment",
        "reimbursement",
        "payroll",
        "billing",
        "receipt",
        "w-2",
        "1099",
    ],
    "personal_admin": [
        "dmv",
        "renew",
        "renewal",
        "license plate",
        "utility bill",
        "lease",
        "landlord",
        "visa application",
        "passport",
    ],
}

_WORKFLOW_DISPLAY: dict[str, str] = {
    "job_hunt": "Job Hunt",
    "legal": "Legal",
    "medical": "Medical",
    "finance": "Finance",
    "personal_admin": "Personal Admin",
}

_KIND_PREFIX: dict[str, str] = {
    "interview": "[Interview]",
    "deadline": "[Deadline]",
    "meeting": "[Meeting]",
}

_ACTION_ITEM_RE = re.compile(
    r"(?:please\s+\w[\w\s,]{5,80}|"
    r"can you\s+\w[\w\s,]{5,80}|"
    r"could you\s+\w[\w\s,]{5,80}|"
    r"(?:Review|Send|Complete|Submit|Sign|Confirm|Approve|Update|Schedule|Respond|Reply|Attach|Forward)"
    r"\s+\w[\w\s,]{3,80})"
    r"[.!?]?",
    re.IGNORECASE,
)


def _classify_workflow(text: str) -> str:
    lower = text.lower()
    for workflow, keywords in _WORKFLOW_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return workflow
    return ""


def _extract_action_items(body: str) -> list[str]:
    matches = _ACTION_ITEM_RE.findall(body)
    seen: set[str] = set()
    items: list[str] = []
    for m in matches:
        cleaned = m.strip()[:120]
        key = cleaned.lower()[:40]
        if key not in seen and cleaned:
            seen.add(key)
            items.append(cleaned)
    return items[:5]


_RICH_PATTERNS: dict[str, dict[str, re.Pattern]] = {
    "job_hunt": {
        "company": re.compile(
            r"(?:at|from|with|@)\s+([A-Z][A-Za-z0-9&\s\-]{1,40}?)(?:\s+(?:Inc|LLC|Corp|Ltd|Co)\.?)?"
            r"(?=[,\s]|$)",
            re.IGNORECASE,
        ),
        "role": re.compile(
            r"(?:position|role|opening|opportunity|for)\s*[:\-]?\s*([A-Za-z][A-Za-z\s]{4,50}?)"
            r"(?=\s+(?:at|with|role|position)|[,\.]|$)",
            re.IGNORECASE,
        ),
    },
    "finance": {
        "amount": re.compile(r"\$\s*[\d,]+(?:\.\d{2})?"),
        "due_date": re.compile(
            r"due\s+(?:on\s+|by\s+)?([A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?|\d{1,2}[\/-]\d{1,2}(?:[\/-]\d{2,4})?)",
            re.IGNORECASE,
        ),
    },
    "legal": {
        "ref": re.compile(
            r"(?:agreement|contract|nda|case)\s+(?:no\.?\s*)?([A-Z0-9\-]{3,20})",
            re.IGNORECASE,
        ),
    },
    "medical": {
        "appointment": re.compile(
            r"(?:appointment|visit|scheduled)\s+(?:for\s+|on\s+)?([A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?|\d{1,2}[\/-]\d{1,2})",
            re.IGNORECASE,
        ),
    },
}


def _extract_rich_data(workflow: str, text: str) -> dict[str, str]:
    patterns = _RICH_PATTERNS.get(workflow, {})
    result: dict[str, str] = {}
    for key, pat in patterns.items():
        m = pat.search(text)
        if m:
            result[key] = (m.group(1) if m.lastindex else m.group(0)).strip()
    return result


def _rank_thread(
    last_message_at_iso: str,
    needs_reply: bool,
    has_action_items: bool,
    workflow: str,
    message_count: int,
) -> float:
    """Score a thread — higher = fresher, more actionable, has workflow."""
    try:
        last = datetime.fromisoformat(last_message_at_iso)
        days_old = max(0.0, (datetime.now() - last).total_seconds() / 86400)
        score = 2.0 * math.exp(-days_old / 7)
    except Exception:
        score = 0.0
    if needs_reply:
        score += 1.0
    if has_action_items:
        score += 0.5
    if workflow:
        score += 0.3
    if message_count > 1:
        score += 0.2
    return round(score, 4)


def _contact_to_thread_summary(c: Contact) -> GmailThreadSummaryOut:
    """Build a lightweight GmailThreadSummaryOut from a Contact (no full thread fetch)."""
    wf = _classify_workflow(c.snippet)
    nr = c.unread > 0
    last_iso = c.last_ts.isoformat()
    rank = _rank_thread(last_iso, nr, False, wf, 0)
    sender = c.name or "Unknown"
    brief_parts = [f"{sender} \u00b7 {c.snippet[:60].rstrip()}"]
    if nr:
        brief_parts.append("[needs reply]")
    if wf:
        brief_parts.append(f"[{wf}]")
    return GmailThreadSummaryOut(
        thread_id=c.thread_id or c.id,
        owning_account=c.gmail_account,
        participants=[c.name],
        subject=c.snippet,
        last_message_at=last_iso,
        labels=[],
        summary=c.snippet,
        action_items=[],
        needs_reply=nr,
        workflow=wf,
        message_count=0,
        rank=rank,
        brief=" ".join(brief_parts),
        rich_data=_extract_rich_data(wf, c.snippet),
    )


def _indexed_thread_to_summary(row: dict[str, object]) -> GmailThreadSummaryOut:
    subject = str(row.get("latest_subject", "") or row.get("latest_snippet", "") or "")
    summary = str(row.get("summary", "") or subject)
    workflow = _classify_workflow(f"{subject}\n{summary}")
    needs_reply = bool(row.get("needs_reply"))
    action_items = [str(row["open_loop"])] if row.get("open_loop") else []
    last_message_at = str(row.get("latest_item_at", ""))
    rank = _rank_thread(
        last_message_at,
        needs_reply,
        bool(action_items),
        workflow,
        int(row.get("message_count", 0) or 0),
    )
    sender = str(row.get("latest_sender", "") or "Unknown")
    brief_parts = [f"{sender} · {summary[:60].rstrip()}"]
    if needs_reply:
        brief_parts.append("[needs reply]")
    if workflow:
        brief_parts.append(f"[{workflow}]")
    rich_text = "\n".join(
        part
        for part in [
            subject,
            summary,
            str(row.get("open_loop", "")),
            str(row.get("topic", "")),
        ]
        if part
    )
    return GmailThreadSummaryOut(
        thread_id=str(row.get("thread_id", "")),
        owning_account=str(row.get("account", "")),
        participants=[str(p) for p in row.get("participants_json", [])],
        subject=subject,
        last_message_at=last_message_at,
        labels=[],
        summary=summary,
        action_items=action_items,
        needs_reply=needs_reply,
        workflow=workflow,
        message_count=int(row.get("message_count", 0) or 0),
        rank=rank,
        brief=" ".join(brief_parts),
        rich_data=_extract_rich_data(workflow, rich_text),
    )


def _index_view_rows(view: str, limit: int) -> list[dict[str, object]]:
    if view == "actionable":
        return state.index_store.list_threads(
            limit=limit,
            actions=("reply", "review", "track"),
            newest_only=True,
            sort_mode="priority",
        )
    if view == "recent":
        return state.index_store.list_threads(
            limit=limit,
            newest_only=True,
            sort_mode="recent",
        )
    if view == "waiting-on":
        return state.index_store.list_threads(
            limit=limit,
            actions=("track",),
            has_open_loop=True,
            newest_only=True,
            sort_mode="recent",
        )
    raise HTTPException(status_code=404, detail=f"Unknown index view: {view}")


def _preflight_google_write(
    kind: str,
    account: str = "",
    folder_id: str = "",
    list_id: str = "",
    calendar_id: str = "",
    title: str = "",
) -> PreflightResult:
    """Inspect where a Google write will land without executing it."""
    warnings: list[str] = []

    if kind in ("doc", "sheet", "drive_folder"):
        resolved = account or _default_google_account(state.drive_services)
        if not resolved or resolved not in state.drive_services:
            return PreflightResult(
                kind=kind,
                resolved_account=resolved,
                destination="Drive",
                destination_id=folder_id,
                valid=False,
                warnings=["No Drive account available"],
                explanation=f"No Drive service available for account '{resolved}'",
            )
        svc = state.drive_services[resolved]
        destination = "Drive root"
        dest_id = folder_id or ""
        if folder_id:
            try:
                folder = drive_get(svc, folder_id)
                if folder:
                    destination = f"Folder '{folder.name}'"
                else:
                    warnings.append(f"Folder '{folder_id}' not found")
                    return PreflightResult(
                        kind=kind,
                        resolved_account=resolved,
                        destination=f"Unknown folder '{folder_id}'",
                        destination_id=folder_id,
                        valid=False,
                        warnings=warnings,
                        explanation=f"Folder '{folder_id}' not found in Drive for {resolved}",
                    )
            except Exception:
                warnings.append(f"Could not verify folder '{folder_id}'")
                destination = f"Folder '{folder_id}' (unverified)"
        label = f"'{title}' " if title else ""
        return PreflightResult(
            kind=kind,
            resolved_account=resolved,
            destination=destination,
            destination_id=dest_id,
            valid=True,
            warnings=warnings,
            explanation=f"Will create {kind} {label}in {destination} using {resolved}",
        )

    elif kind == "task":
        resolved = account or _default_google_account(state.tasks_services)
        if not resolved or resolved not in state.tasks_services:
            return PreflightResult(
                kind=kind,
                resolved_account=resolved,
                destination="Google Tasks",
                destination_id=list_id,
                valid=False,
                warnings=["No Tasks account available"],
                explanation=f"No Tasks service available for account '{resolved}'",
            )
        svc = state.tasks_services[resolved]
        destination = "My Tasks"
        dest_id = list_id or "@default"
        if list_id and list_id != "@default":
            try:
                all_lists = tasks_lists(svc)
                matched = next(
                    (
                        lst
                        for lst in all_lists
                        if lst.get("id") == list_id or lst.get("title") == list_id
                    ),
                    None,
                )
                if matched:
                    destination = f"Task list '{matched.get('title', list_id)}'"
                    dest_id = matched.get("id", list_id)
                else:
                    warnings.append(f"Task list '{list_id}' not found")
                    return PreflightResult(
                        kind=kind,
                        resolved_account=resolved,
                        destination=f"Unknown list '{list_id}'",
                        destination_id=list_id,
                        valid=False,
                        warnings=warnings,
                        explanation=f"Task list '{list_id}' not found for {resolved}",
                    )
            except Exception:
                warnings.append(f"Could not verify task list '{list_id}'")
                destination = f"List '{list_id}' (unverified)"
        label = f"'{title}' " if title else ""
        return PreflightResult(
            kind=kind,
            resolved_account=resolved,
            destination=destination,
            destination_id=dest_id,
            valid=True,
            warnings=warnings,
            explanation=f"Will create task {label}in {destination} using {resolved}",
        )

    elif kind == "calendar_event":
        resolved = account or _default_google_account(state.cal_services)
        if not resolved or resolved not in state.cal_services:
            return PreflightResult(
                kind=kind,
                resolved_account=resolved,
                destination="Calendar",
                destination_id=calendar_id,
                valid=False,
                warnings=["No calendar account available"],
                explanation=f"No calendar service available for account '{resolved}'",
            )
        cal_id = calendar_id or "primary"
        destination = "primary calendar" if cal_id == "primary" else f"Calendar '{cal_id}'"
        label = f"'{title}' " if title else ""
        return PreflightResult(
            kind=kind,
            resolved_account=resolved,
            destination=destination,
            destination_id=cal_id,
            valid=True,
            warnings=warnings,
            explanation=f"Will create event {label}in {destination} using {resolved}",
        )

    return PreflightResult(
        kind=kind,
        resolved_account="",
        destination="",
        destination_id="",
        valid=False,
        warnings=[f"Unknown kind '{kind}'"],
        explanation=f"Unknown write kind '{kind}'. Expected: doc, sheet, drive_folder, task, calendar_event",
    )


def _document_to_out(d) -> DocumentOut:  # type: ignore[no-untyped-def]
    return DocumentOut(
        id=d.id,
        title=d.title,
        url=d.url,
        mime_type=d.mime_type,
        account=d.account,
    )


@app.get("/docs", response_model=list[DocumentOut])
async def list_docs(q: str = "", limit: int = 20, account: str = ""):

    acct, drive_svc = _get_drive_service_for_account(account)
    docs = await asyncio.to_thread(docs_list, drive_svc, q, limit, acct)
    return [_document_to_out(d) for d in docs]


@app.post("/docs", response_model=DocumentOut)
async def create_doc(req: CreateDocumentRequest):

    acct, docs_svc = _get_docs_service_for_account(req.account)
    doc = await asyncio.to_thread(docs_create, docs_svc, req.title)
    if not doc:
        raise HTTPException(400, "Failed to create document")
    return _document_to_out(doc)


@app.get("/docs/{document_id}", response_model=DocumentOut)
async def get_doc(document_id: str, account: str = ""):

    acct, docs_svc = _get_docs_service_for_account(account)
    doc = await asyncio.to_thread(docs_get, docs_svc, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return _document_to_out(doc)


@app.delete("/docs/{document_id}")
async def delete_doc(document_id: str, account: str = ""):

    acct, drive_svc = _get_drive_service_for_account(account)
    ok = await asyncio.to_thread(docs_delete, drive_svc, document_id)
    return {"ok": ok}


@app.get("/docs/{document_id}/text")
async def get_doc_text(document_id: str, account: str = ""):

    acct, docs_svc = _get_docs_service_for_account(account)
    text = await asyncio.to_thread(docs_get_text, docs_svc, document_id)
    if text is None:
        raise HTTPException(400, "Failed to read document")
    return {"text": text}


@app.post("/docs/{document_id}/text")
async def insert_doc_text(document_id: str, req: InsertTextRequest, account: str = ""):

    acct, docs_svc = _get_docs_service_for_account(account)
    ok = await asyncio.to_thread(docs_insert_text, docs_svc, document_id, req.text, req.index)
    return {"ok": ok}


@app.get("/docs/{document_id}/export")
async def export_doc(document_id: str, format: str = "text/plain", account: str = ""):

    acct, drive_svc = _get_drive_service_for_account(account)
    content = await asyncio.to_thread(docs_export, drive_svc, document_id, format)
    if not content:
        raise HTTPException(400, "Failed to export document")
    # Return raw bytes with appropriate content type
    from starlette.responses import Response

    mime_type = format
    if format == "text/plain":
        mime_type = "text/plain; charset=utf-8"
    elif format == "application/pdf":
        mime_type = "application/pdf"
    elif format == "text/html":
        mime_type = "text/html; charset=utf-8"
    return Response(content=content, media_type=mime_type)


# ── Preflight ───────────────────────────────────────────────────────────────


@app.get("/preflight/google-write", response_model=PreflightResult)
async def preflight_google_write(
    kind: str,
    account: str = "",
    folder_id: str = "",
    list_id: str = "",
    calendar_id: str = "",
    title: str = "",
):
    return await asyncio.to_thread(
        _preflight_google_write, kind, account, folder_id, list_id, calendar_id, title
    )


# ── Search ───────────────────────────────────────────────────────────────────


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    result = await asyncio.to_thread(
        search_all,
        query=req.q,
        sources=req.sources,
        limit=req.limit,
        gmail_services=state.gmail_services,
        cal_services=state.cal_services,
        from_addr=req.from_addr,
        before=req.before,
        after=req.after,
        has_attachment=req.has_attachment,
        is_unread=req.is_unread,
    )
    return result


# ── Ambient / Dictation ─────────────────────────────────────────────────────


@app.post("/ambient/start")
async def start_ambient():
    if state.ambient.is_running:
        return {"status": "already_running"}
    state.ambient.start()
    return {"status": "started"}


@app.post("/ambient/stop")
async def stop_ambient():
    if not state.ambient.is_running:
        return {"status": "not_running"}
    state.ambient.stop()
    return {"status": "stopped"}


@app.get("/ambient/status")
async def ambient_status():
    avail, reason = ambient_available()
    return {
        "ambient": state.ambient.is_running,
        "available": avail,
        "reason": reason,
        "dictation": state.dictation.is_running,
        "dictation_available": state.dictation.available,
    }


@app.get("/ambient/transcript")
async def get_ambient_transcript(limit: int = 50):
    segments = state.ambient.get_transcript(max_segments=limit)
    return {"segments": segments, "count": len(segments)}


@app.get("/ambient/notes")
async def list_ambient_notes(limit: int = 50, q: str = ""):
    notes = await asyncio.to_thread(ambient_notes.list_daily_notes, limit=limit)
    if q:
        q_lower = q.lower()
        notes = [n for n in notes if q_lower in n.get("date", "").lower()]
    return notes


@app.get("/ambient/notes/{date}")
async def get_ambient_note(date: str):
    content = await asyncio.to_thread(ambient_notes.read_daily_note, date)
    if content is None:
        raise HTTPException(404, "Note not found")
    return {"date": date, "content": content}


@app.post("/dictation/start")
async def start_dictation():
    if not state.dictation.available:
        raise HTTPException(400, "whisper-stream binary not available")
    if state.dictation.is_running:
        return {"status": "already_running"}
    state.dictation.start()
    return {"status": "started"}


@app.post("/dictation/stop")
async def stop_dictation():
    if not state.dictation.is_running:
        return {"status": "not_running"}
    state.dictation.stop()
    return {"status": "stopped"}


@app.get("/dictation/status")
async def dictation_status():
    return {
        "running": state.dictation.is_running,
        "available": state.dictation.available,
    }


# ── Voice Config ─────────────────────────────────────────────────────────────


@app.get("/voice/config")
async def get_voice_config():
    return await asyncio.to_thread(load_voice_config)


@app.put("/voice/config")
async def put_voice_config(req: VoiceConfigRequest):
    current = await asyncio.to_thread(load_voice_config)
    updates = req.model_dump(exclude_none=True)
    merged = {**current, **updates}
    await asyncio.to_thread(save_voice_config, merged)
    return merged


# ── Autocomplete / LLM ──────────────────────────────────────────────────────


@app.post("/autocomplete")
async def autocomplete_endpoint(req: AutocompleteRequest):
    try:
        result = await asyncio.to_thread(
            services_autocomplete,
            req.draft,
            req.messages,
            req.max_tokens,
            req.temperature,
            req.mode,
        )
        return {"completion": result}
    except Exception as e:
        return {"completion": None, "error": str(e)}


@app.get("/llm/status")
async def llm_status():
    from services import llm_is_loaded

    return {
        "loaded": llm_is_loaded(),
        "small": {
            "loaded": llm_is_loaded(),
            "model_id": "mlx-community/Qwen3.5-0.8B-MLX-4bit",
        },
        "large": {
            "loaded": llm_large_is_loaded(),
            "model_id": MLX_LARGE_MODEL,
            "loading": llm_large_is_loading(),
        },
    }


@app.post("/llm/warmup")
async def llm_warmup_endpoint():
    from services import llm_warmup

    await asyncio.to_thread(llm_warmup)
    return {"status": "ready"}


# ── Contacts ─────────────────────────────────────────────────────────────────


@app.get("/contacts/search")
async def search_contacts(q: str = "", limit: int = 20):
    results = await asyncio.to_thread(
        contacts_search,
        state.gmail_services,
        q,
        limit,
    )
    return results


@app.get("/contacts/{contact_id}/profile")
async def get_contact_profile(contact_id: str):
    profile = await asyncio.to_thread(
        contacts_profile,
        contact_id,
        state.gmail_services,
        state.cal_services,
    )
    return profile


@app.get("/contacts/favorites")
async def get_favorites():
    favs = await asyncio.to_thread(load_favorites)
    return {"favorites": sorted(favs)}


@app.post("/contacts/favorites/{contact_id}")
async def add_favorite(contact_id: str):
    favs = await asyncio.to_thread(load_favorites)
    favs.add(contact_id)
    await asyncio.to_thread(save_favorites, favs)
    return {"ok": True, "favorites": sorted(favs)}


@app.delete("/contacts/favorites/{contact_id}")
async def remove_favorite(contact_id: str):
    favs = await asyncio.to_thread(load_favorites)
    favs.discard(contact_id)
    await asyncio.to_thread(save_favorites, favs)
    return {"ok": True, "favorites": sorted(favs)}


# ── AI endpoints ─────────────────────────────────────────────────────────────


@app.post("/ai/briefing")
async def ai_briefing_endpoint():
    """Compile a morning briefing from today's data."""
    try:
        today_dt = datetime.now()
        events_raw = await asyncio.to_thread(calendar_events, state.cal_services, today_dt)
        events = [
            {
                "summary": e.summary,
                "start": e.start.isoformat(),
                "end": e.end.isoformat(),
                "all_day": e.all_day,
            }
            for e in events_raw
        ]
    except Exception:
        events = []

    try:
        reminders_raw = await asyncio.to_thread(reminders_list)
        reminders = [
            {"title": r.title, "completed": r.completed, "list_name": r.list_name}
            for r in reminders_raw
        ]
    except Exception:
        reminders = []

    try:
        all_convos: list[dict] = []  # type: ignore[type-arg]
        for acct_email, svc in state.gmail_services.items():
            try:
                gmail_raw = await asyncio.to_thread(gmail_contacts, svc, acct_email, 20)
                all_convos.extend({"source": "gmail", "unread": c.unread} for c in gmail_raw)
            except Exception:
                pass
        imsg_raw = await asyncio.to_thread(lambda: imsg_contacts(limit=50))
        all_convos.extend({"source": "imessage", "unread": c.unread} for c in imsg_raw)
    except Exception:
        all_convos = []

    try:
        gh_notifications = await asyncio.to_thread(github_notifications)
        gh_notifs = [{"unread": n.unread} for n in gh_notifications]
    except Exception:
        gh_notifs = []

    try:
        gh_prs = await asyncio.to_thread(github_pulls)
    except Exception:
        gh_prs = []

    result = await asyncio.to_thread(ai_briefing, events, reminders, all_convos, gh_notifs, gh_prs)
    return result


@app.post("/ai/triage")
async def ai_triage_endpoint(req: TriageRequest):
    """Return priority mapping for a list of conversations."""
    result = await asyncio.to_thread(ai_triage, req.conversations)
    return result


@app.post("/ai/summarize")
async def ai_summarize_endpoint(req: SummarizeRequest):
    """Summarize an email thread."""
    result = await asyncio.to_thread(ai_summarize, req.thread_id, req.messages)
    return result


@app.post("/ai/extract-actions")
async def ai_extract_actions_endpoint(req: ExtractActionsRequest):
    """Extract action items from message text."""
    result = await asyncio.to_thread(ai_extract_actions, req.text)
    return result


# ── Accounts ─────────────────────────────────────────────────────────────────


@app.get("/accounts")
async def list_accounts():
    from services import _github_token

    return {
        "gmail": list(state.gmail_services.keys()),
        "calendar": list(state.cal_services.keys()),
        "drive": list(state.drive_services.keys()),
        "sheets": list(state.sheets_services.keys()),
        "github": _github_token() is not None,
    }


@app.post("/accounts/add")
async def add_account():
    email = await asyncio.to_thread(add_google_account)
    if not email:
        raise HTTPException(400, "Failed to add account — no credentials.json")
    # Reload all services
    gmail, cal, drive, sheets, docs, tasks = await asyncio.to_thread(google_auth_all)
    state.gmail_services = gmail
    state.cal_services = cal
    state.drive_services = drive
    state.sheets_services = sheets
    state.docs_services = docs
    state.tasks_services = tasks
    return {"email": email}


@app.post("/accounts/reauth")
async def reauth_account(req: AccountRequest):
    if not req.email:
        raise HTTPException(400, "email is required")
    email = await asyncio.to_thread(reauth_google_account, req.email)
    if not email:
        raise HTTPException(400, "Re-auth failed")
    gmail, cal, drive, sheets, docs, tasks = await asyncio.to_thread(google_auth_all)
    state.gmail_services = gmail
    state.cal_services = cal
    state.drive_services = drive
    state.sheets_services = sheets
    state.docs_services = docs
    state.tasks_services = tasks
    return {"email": email}


# ── Notifications ────────────────────────────────────────────────────────────


@app.get("/notifications/config")
async def get_notification_config():
    return await asyncio.to_thread(load_notification_config)


@app.put("/notifications/config")
async def put_notification_config(cfg: dict):  # type: ignore[type-arg]
    ok = await asyncio.to_thread(save_notification_config, cfg)
    if not ok:
        raise HTTPException(500, "Failed to save notification config")
    return {"ok": True}


@app.post("/notifications/test")
async def test_notification(req: NotificationTestRequest):
    sent = await asyncio.to_thread(send_notification, req.title, req.body or "Test notification")
    return {"ok": sent, "sent": sent}


# ── Main ─────────────────────────────────────────────────────────────────────


# ── Calendar (extended) ──────────────────────────────────────────────────────


def _get_cal_service_for_account(account: str = "") -> tuple[str, object]:
    """Get calendar service for account, raising HTTPException if unavailable."""
    acct = account or _default_google_account(state.cal_services)
    svc = state.cal_services.get(acct)
    if not svc:
        raise HTTPException(404, "No calendar account available")
    return acct, svc


@app.get("/calendar/calendars", response_model=list[CalendarOut])
async def list_calendars(account: str = ""):
    acct, svc = _get_cal_service_for_account(account)
    calendars = await asyncio.to_thread(calendar_list_calendars, state.cal_services)
    return calendars


@app.get("/calendar/events/{event_id}", response_model=CalendarEventOut)
async def get_event(
    event_id: str,
    calendar_id: str = "primary",
    account: str = "",
):
    acct, svc = _get_cal_service_for_account(account)
    evt = await asyncio.to_thread(calendar_get_event, svc, event_id, calendar_id)
    if not evt:
        raise HTTPException(404, "Event not found")
    return _event_to_out(evt)


@app.get("/calendar/events/{event_id}/attendees")
async def get_event_attendees(
    event_id: str,
    calendar_id: str = "primary",
    account: str = "",
):
    acct, svc = _get_cal_service_for_account(account)
    evt = await asyncio.to_thread(calendar_get_event, svc, event_id, calendar_id)
    if not evt:
        raise HTTPException(404, "Event not found")
    return {"event_id": event_id, "attendees": evt.attendees}


@app.post("/calendar/events/{event_id}/rsvp")
async def rsvp_event(
    event_id: str,
    req: RsvpRequest,
):
    acct, svc = _get_cal_service_for_account(req.account)
    ok = await asyncio.to_thread(
        calendar_rsvp_event, svc, event_id, acct, req.response, req.calendar_id
    )
    return {"ok": ok}


@app.patch("/calendar/events/{event_id}/attendees")
async def modify_attendees(
    event_id: str,
    req: ModifyAttendeesRequest,
):
    acct, svc = _get_cal_service_for_account(req.account)
    ok = await asyncio.to_thread(
        calendar_modify_attendees, svc, event_id, req.add, req.remove, req.calendar_id
    )
    return {"ok": ok}


@app.get("/calendar/events/{event_id}/instances", response_model=list[CalendarEventOut])
async def get_instances(
    event_id: str,
    calendar_id: str = "primary",
    account: str = "",
    time_min: str = "",
    time_max: str = "",
    max_results: int = 50,
):
    from datetime import datetime

    acct, svc = _get_cal_service_for_account(account)
    time_min_dt = datetime.fromisoformat(time_min) if time_min else None
    time_max_dt = datetime.fromisoformat(time_max) if time_max else None
    instances = await asyncio.to_thread(
        calendar_get_recurring_instances,
        svc,
        event_id,
        calendar_id,
        time_min_dt,
        time_max_dt,
        max_results,
    )
    return [_event_to_out(e) for e in instances]


@app.get("/calendar/search", response_model=list[CalendarEventOut])
async def search_calendar(
    q: str = "",
    attendee: str = "",
    location: str = "",
    start: str = "",
    end: str = "",
    calendar_id: str = "",
    account: str = "",
    limit: int = 50,
):
    from datetime import datetime

    # Use single account if specified, else all
    cal_svcs = (
        {account: state.cal_services[account]}
        if account and account in state.cal_services
        else state.cal_services
    )
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    events = await asyncio.to_thread(
        calendar_search_events,
        cal_svcs,
        q,
        attendee,
        location,
        start_dt,
        end_dt,
        calendar_id or None,
        limit,
    )
    return [_event_to_out(e) for e in events]


@app.put("/calendar/events/{event_id}/reminders")
async def set_reminders(
    event_id: str,
    req: EventRemindersRequest,
):
    acct, svc = _get_cal_service_for_account(req.account)
    reminders_dict = {"useDefault": req.use_default}
    if not req.use_default:
        reminders_dict["overrides"] = req.overrides
    ok = await asyncio.to_thread(
        calendar_update_event, svc, event_id, reminders=reminders_dict, calendar_id=req.calendar_id
    )
    return {"ok": ok}


@app.post("/calendar/events/{event_id}/create-reminder")
async def create_event_reminder(
    event_id: str,
    list_name: str = "Reminders",
    minutes_before: int = 30,
    account: str = "",
    calendar_id: str = "primary",
):
    """Create an Apple Reminder from a calendar event.

    The reminder is due ``minutes_before`` minutes before the event start.
    """
    try:
        acct, svc = _get_cal_service_for_account(account)
        event = await asyncio.to_thread(calendar_get_event, svc, event_id, calendar_id)
        if not event:
            return {"error": "Event not found"}

        success = await asyncio.to_thread(
            calendar_event_to_reminder, event, list_name, minutes_before
        )
        return {"success": success, "reminder_created": success}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.post("/calendar/freebusy")
async def get_freebusy(req: FreeBusyRequest):
    from datetime import datetime

    acct, svc = _get_cal_service_for_account(req.account)
    time_min = datetime.fromisoformat(req.time_min)
    time_max = datetime.fromisoformat(req.time_max)
    busy = await asyncio.to_thread(
        calendar_freebusy, svc, time_min, time_max, req.calendar_ids, req.timezone
    )
    return {"time_min": req.time_min, "time_max": req.time_max, "busy": busy}


@app.post("/calendar/free-slots")
async def find_free_slots(req: FreeSlotsRequest):
    from datetime import datetime

    acct, svc = _get_cal_service_for_account(req.account)
    time_min = datetime.fromisoformat(req.time_min)
    time_max = datetime.fromisoformat(req.time_max)
    slots = await asyncio.to_thread(
        calendar_find_free_slots,
        svc,
        time_min,
        time_max,
        req.calendar_ids,
        req.duration_minutes,
        req.timezone,
    )
    return {"slots": slots}


@app.post("/gmail/labels")
async def create_gmail_label(name: str, visibility: str = "labelShow", account: str = ""):
    """Create a new Gmail label."""
    try:
        acct, svc = _get_gmail_service_for_account(account)
        result = await asyncio.to_thread(gmail_label_create, svc, name, visibility)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/calendar/conflicts")
async def check_calendar_conflicts(start: str, end: str, account: str = ""):
    """Find calendar conflicts in time range [start, end]."""
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        acct, svc = _get_cal_service_for_account(account)
        conflicts = await asyncio.to_thread(calendar_find_conflicts, {acct: svc}, start_dt, end_dt)
        return {
            "conflicts": [
                {
                    "id": c.id,
                    "title": c.title,
                    "start": c.start,
                    "end": c.end,
                    "location": c.location or "",
                }
                for c in conflicts
            ]
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/memory/extract")
async def extract_memory_endpoint(text: str, source: str = "manual", auto_save: bool = False):
    """Extract memory entities from text and optionally save to memory store."""
    try:
        extracted = await asyncio.to_thread(ai_extract_memory, text)
        saved_count = 0

        if auto_save:
            # Auto-save extracted entities
            for person in extracted.get("people", []):
                memory_store.save_entry(
                    memory_type="person",
                    subject=person.get("name", ""),
                    content=person.get("context", ""),
                    source=source,
                    confidence=0.8,
                    metadata={"relationship": person.get("relationship", "")},
                )
                saved_count += 1

            for project in extracted.get("projects", []):
                memory_store.save_entry(
                    memory_type="project",
                    subject=project.get("name", ""),
                    content=project.get("description", ""),
                    source=source,
                    confidence=0.85,
                    metadata={"status": project.get("status", "active")},
                )
                saved_count += 1

            for commitment in extracted.get("commitments", []):
                memory_store.save_entry(
                    memory_type="commitment",
                    subject=commitment.get("text", ""),
                    content=f"Owner: {commitment.get('owner', '')}",
                    source=source,
                    confidence=0.9,
                    status="open",
                    expires_at=commitment.get("deadline", None),
                )
                saved_count += 1

        return {"extracted": extracted, "saved": saved_count}
    except Exception as e:
        return {"error": str(e)}


# ── Phase 4: Workflow tools ─────────────────────────────────────────────────


@app.get("/gmail/threads/needing-reply", response_model=list[GmailThreadSummaryOut])
async def get_threads_needing_reply(
    workflow: str = "",
    days_stale: int = 3,
    account: str = "",
    limit: int = 20,
):
    """Unread inbox threads older than days_stale days where a reply is expected."""
    acct, svc = _get_gmail_service_for_account(account)
    contacts = await asyncio.to_thread(gmail_search, svc, acct, "is:inbox", limit * 3)
    cutoff = datetime.now() - timedelta(days=days_stale)
    seen: set[str] = set()
    results: list[GmailThreadSummaryOut] = []
    for c in contacts:
        tid = c.thread_id or c.id
        if c.unread == 0 or c.last_ts > cutoff or tid in seen:
            continue
        seen.add(tid)
        ts = _contact_to_thread_summary(c)
        if workflow and ts.workflow != workflow:
            continue
        results.append(ts)
    results.sort(key=lambda t: t.rank, reverse=True)
    return results[:limit]


@app.post("/calendar/workflow-event", response_model=CalendarEventOut)
async def create_calendar_workflow_event(req: WorkflowEventRequest):
    """Create a calendar event with kind prefix in title and workflow tag in description."""
    account, svc = _get_cal_service_for_account(req.account)
    prefix = _KIND_PREFIX.get(req.kind, "")
    title = (
        f"{prefix} {req.title}".strip()
        if prefix and not req.title.startswith(prefix)
        else req.title
    )
    workflow = req.workflow or _classify_workflow(req.title)
    tag = f"#{workflow}" if workflow else ""
    description = req.description
    if tag and tag not in description:
        description = f"{description} {tag}".strip() if description else tag
    try:
        event_id = await asyncio.to_thread(
            calendar_create_event,
            svc,
            summary=title,
            start=datetime.fromisoformat(req.start),
            end=datetime.fromisoformat(req.end),
            location=req.location,
            description=description,
            all_day=False,
            attendees=req.attendees,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to create event: {str(e)}") from e
    return CalendarEventOut(
        summary=title,
        start=req.start,
        end=req.end,
        location=req.location,
        description=description,
        account=account,
        event_id=event_id or "",
        workflow=workflow,
    )


@app.get("/inbox/needs-action", response_model=NeedsActionOut)
async def get_needs_action(workflow: str = "", account: str = ""):
    """Cross-source rollup: reply-needed threads + overdue tasks + upcoming calendar events."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    three_days_out = today + timedelta(days=3)

    threads: list[GmailThreadSummaryOut] = []
    indexed_threads = state.index_store.list_threads(
        limit=25, actionable_only=True, newest_only=True
    )
    if indexed_threads:
        for row in indexed_threads:
            if account and row.get("account") != account:
                continue
            ts = _indexed_thread_to_summary(row)
            if workflow and ts.workflow != workflow:
                continue
            threads.append(ts)
            if len(threads) >= 10:
                break
    elif state.gmail_services:
        try:
            acct, svc = _get_gmail_service_for_account(account)
            contacts = await asyncio.to_thread(gmail_search, svc, acct, "is:inbox", 40)
            seen_na: set[str] = set()
            for c in contacts:
                tid = c.thread_id or c.id
                if c.unread == 0 or tid in seen_na:
                    continue
                seen_na.add(tid)
                ts = _contact_to_thread_summary(c)
                if workflow and ts.workflow != workflow:
                    continue
                threads.append(ts)
                if len(threads) >= 10:
                    break
        except Exception:
            pass

    tasks: list[TaskOut] = []
    if state.tasks_services:
        try:
            acct, svc = _get_tasks_service_for_account(account)
            raw_tasks = await asyncio.to_thread(tasks_list, svc, "@default", False, 100)
            for t in raw_tasks:
                task_out = _task_to_out(t, acct)
                if workflow and task_out.workflow != workflow:
                    continue
                if (t.due and t.due <= today) or (t.status == "needs_action" and not t.due):
                    tasks.append(task_out)
                if len(tasks) >= 10:
                    break
        except Exception:
            pass

    events: list[CalendarEventOut] = []
    if state.cal_services:
        try:
            evts = await asyncio.to_thread(
                calendar_events,
                state.cal_services,
                start_date=today,
                end_date=three_days_out,
            )
            for e in evts:
                ev = _event_to_out(e)
                if workflow and ev.workflow != workflow:
                    continue
                events.append(ev)
                if len(events) >= 10:
                    break
        except Exception:
            pass

    counts: dict[str, int] = {}
    for wf in (
        [t.workflow for t in threads] + [t.workflow for t in tasks] + [e.workflow for e in events]
    ):
        if wf:
            counts[wf] = counts.get(wf, 0) + 1

    return NeedsActionOut(threads=threads, tasks=tasks, events=events, workflow_counts=counts)


@app.get("/index/threads", response_model=IndexStatusOut)
async def get_index_threads(
    limit: int = 20, actionable_only: bool = True, newest_only: bool = True
):
    rows = state.index_store.list_threads(
        limit=limit,
        actionable_only=actionable_only,
        newest_only=newest_only,
    )
    return IndexStatusOut(
        db_path=str(state.index_store.db_path),
        threads=[_indexed_thread_to_summary(row) for row in rows],
    )


@app.get("/index/status", response_model=IndexOverviewOut)
async def get_index_status():
    return IndexOverviewOut(
        db_path=str(state.index_store.db_path),
        counts=state.index_store.index_counts(),
        sync_states=[IndexSyncStateOut(**row) for row in state.index_store.list_sync_states()],
    )


@app.get("/index/views/{view_name}", response_model=IndexedThreadListOut)
async def get_index_view(view_name: str, limit: int = 20):
    rows = _index_view_rows(view_name, limit)
    return IndexedThreadListOut(
        view=view_name,
        threads=[_indexed_thread_to_summary(row) for row in rows],
    )


@app.post("/index/sync/bootstrap", response_model=IndexSyncOut)
async def post_index_sync_bootstrap():
    stats = await asyncio.to_thread(index_bootstrap_sync, state.index_store)
    return IndexSyncOut(ok=True, mode="bootstrap", stats=stats)


@app.post("/index/sync/incremental", response_model=IndexSyncOut)
async def post_index_sync_incremental():
    stats = await asyncio.to_thread(index_incremental_sync, state.index_store)
    return IndexSyncOut(ok=True, mode="incremental", stats=stats)


@app.post("/drive/workflow-folder", response_model=DriveFileOut)
async def create_drive_workflow_folder(req: WorkflowFolderRequest):
    """Create a Drive folder using workflow display name and default account."""
    acct, svc = _get_drive_service_for_account(req.account)
    folder_name = req.name or _WORKFLOW_DISPLAY.get(req.workflow, req.workflow)
    result = await asyncio.to_thread(drive_create_folder, svc, folder_name, parent_id=req.parent_id)
    if not result:
        raise HTTPException(500, "Failed to create folder")
    return _drive_to_out(result, account=acct)


@app.post("/docs/workflow-doc", response_model=DocumentOut)
async def create_workflow_doc(req: WorkflowDocRequest):
    """Create a Google Doc using default account."""
    acct, docs_svc = _get_docs_service_for_account(req.account)
    doc = await asyncio.to_thread(docs_create, docs_svc, req.title)
    if not doc:
        raise HTTPException(400, "Failed to create document")
    return _document_to_out(doc)


@app.post("/sheets/workflow-sheet", response_model=SpreadsheetOut)
async def create_workflow_sheet(req: WorkflowSheetRequest):
    """Create a Google Sheet using default account."""
    acct, sheets_svc = _get_sheets_service_for_account(req.account)
    result = await asyncio.to_thread(sheets_create, sheets_svc, req.title, [])
    if not result:
        raise HTTPException(400, "Failed to create spreadsheet")
    return _spreadsheet_to_out(result, acct)


# ── Cross-silo query (gemma4-hackathon orchestrator) ──────────────────────────


class _QueryRequest(BaseModel):
    question: str
    per_silo_limit: int = 10
    use_real_model: bool = False


@app.post("/query")
async def cross_silo_query(req: _QueryRequest):
    """Natural-language cross-silo query via gemma4 orchestrator.

    Requires gemma4-hackathon installed as an editable dep:
        uv pip install -e ~/projects/gemma4-hackathon
    Returns gracefully with 503 when not installed.
    """
    try:
        import gemma4_hackathon.silos.calendar  # noqa: F401
        import gemma4_hackathon.silos.gmail  # noqa: F401
        import gemma4_hackathon.silos.imessage  # noqa: F401  trigger registration
        import gemma4_hackathon.silos.memory  # noqa: F401
        import gemma4_hackathon.silos.photos  # noqa: F401
        from gemma4_hackathon.orchestrator import Orchestrator  # type: ignore
    except ImportError as exc:
        raise HTTPException(503, f"gemma4_hackathon not installed: {exc}") from exc

    if req.use_real_model:
        from gemma4_hackathon.runtime import get_runtime  # type: ignore

        runtime = get_runtime("e4b")
    else:
        from gemma4_hackathon.fake_runtime import FakeRuntime  # type: ignore

        runtime = FakeRuntime()

    orch = Orchestrator(runtime)
    result = await asyncio.to_thread(orch.query, req.question, per_silo_limit=req.per_silo_limit)
    return {
        "question": result.question,
        "answer": result.answer,
        "plan": result.plan,
        "citations": [
            {"silo": r.silo, "id": r.id, "when": r.when.isoformat() if r.when else None}
            for r in result.retrieved
        ],
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("INBOX_SERVER_PORT", PORT))
    host = os.environ.get("INBOX_SERVER_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")
