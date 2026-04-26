"""
HTTP client for the Inbox API server.
Used by the TUI and agents to interact with inbox data.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

_DEFAULT_PORT = int(os.environ.get("INBOX_SERVER_PORT", "9849"))
SERVER_URL = os.environ.get("INBOX_SERVER_URL", f"http://127.0.0.1:{_DEFAULT_PORT}")
SERVER_SCRIPT = Path(__file__).parent / "inbox_server.py"


class InboxClient:
    def __init__(self, base_url: str = SERVER_URL, timeout: float = 30):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # ── Health ────────────────────────────────────────────────────────────

    def health(self) -> dict:
        return self._client.get("/health").json()

    def health_check(self) -> dict:
        """Check if server is responding. Raises if not."""
        r = self._client.get("/health", timeout=2)
        r.raise_for_status()
        return r.json()

    def is_server_running(self) -> bool:
        try:
            self._client.get("/health", timeout=2)
            return True
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    @staticmethod
    def start_server() -> subprocess.Popen:
        """Launch the server as a background process with logs."""
        log_path = SERVER_SCRIPT.parent / "server.log"
        log_file = open(log_path, "w")  # noqa: SIM115
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        return proc

    def ensure_server(self, max_wait: float = 30) -> None:
        """Start the server if it's not running and wait for it."""
        if self.is_server_running():
            return
        proc = self.start_server()
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            time.sleep(0.5)
            # Check if process died
            if proc.poll() is not None:
                log_path = SERVER_SCRIPT.parent / "server.log"
                log = log_path.read_text() if log_path.exists() else "no log"
                raise RuntimeError(
                    f"Server crashed on startup (exit {proc.returncode}): {log[-500:]}"
                )
            if self.is_server_running():
                return
        raise RuntimeError(f"Server failed to start within {max_wait}s — check server.log")

    # ── Conversations ────────────────────────────────────────────────────

    def conversations(self, source: str = "all", limit: int = 50) -> list[dict]:
        r = self._client.get("/conversations", params={"source": source, "limit": limit})
        r.raise_for_status()
        return r.json()

    # ── Indexed inbox views ───────────────────────────────────────────────

    def index_threads(
        self,
        *,
        limit: int = 20,
        actionable_only: bool = True,
        newest_only: bool = True,
    ) -> dict:
        r = self._client.get(
            "/index/threads",
            params={
                "limit": limit,
                "actionable_only": actionable_only,
                "newest_only": newest_only,
            },
        )
        r.raise_for_status()
        return r.json()

    def index_status(self) -> dict:
        r = self._client.get("/index/status")
        r.raise_for_status()
        return r.json()

    def index_view(self, view_name: str, *, limit: int = 20) -> dict:
        r = self._client.get(f"/index/views/{view_name}", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    # ── Messages ─────────────────────────────────────────────────────────

    def messages(
        self,
        source: str,
        conv_id: str,
        thread_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if thread_id:
            params["thread_id"] = thread_id
        r = self._client.get(f"/messages/{source}/{conv_id}", params=params)
        r.raise_for_status()
        return r.json()

    def send(self, conv_id: str, source: str, text: str) -> bool:
        r = self._client.post(
            "/messages/send",
            json={"conv_id": conv_id, "source": source, "text": text},
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── Gmail actions ────────────────────────────────────────────────────

    def gmail_archive(self, msg_id: str) -> bool:
        r = self._client.post(f"/messages/gmail/{msg_id}/archive")
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_delete(self, msg_id: str) -> bool:
        r = self._client.post(f"/messages/gmail/{msg_id}/delete")
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_star(self, msg_id: str) -> bool:
        r = self._client.post(f"/messages/gmail/{msg_id}/star")
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_unstar(self, msg_id: str) -> bool:
        r = self._client.post(f"/messages/gmail/{msg_id}/unstar")
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_mark_read(self, msg_id: str) -> bool:
        r = self._client.post(f"/messages/gmail/{msg_id}/read")
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_mark_unread(self, msg_id: str) -> bool:
        r = self._client.post(f"/messages/gmail/{msg_id}/unread")
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_labels(self, account: str = "") -> list[dict]:
        params = {"account": account} if account else {}
        r = self._client.get("/gmail/labels", params=params)
        r.raise_for_status()
        return r.json()

    def gmail_attachment(self, msg_id: str, att_id: str) -> dict:
        r = self._client.get(f"/messages/gmail/{msg_id}/attachments/{att_id}")
        r.raise_for_status()
        return r.json()

    def gmail_compose(self, to: str, subject: str, body: str, account: str = "") -> bool:
        r = self._client.post(
            "/messages/compose",
            json={"to": to, "subject": subject, "body": body, "account": account},
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    def gmail_conversations_by_label(
        self, label: str = "INBOX", limit: int = 50, account: str = ""
    ) -> list[dict]:
        r = self._client.get(
            "/gmail/conversations",
            params={"label": label, "limit": limit, "account": account},
        )
        r.raise_for_status()
        return r.json()

    # ── Calendar ─────────────────────────────────────────────────────────

    def calendar_events(
        self,
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if start_date and end_date:
            params["start"] = start_date
            params["end"] = end_date
        elif date:
            params["date"] = date
        r = self._client.get("/calendar/events", params=params)
        r.raise_for_status()
        return r.json()

    def calendar_events_range(self, start: str, end: str) -> list[dict]:
        """Convenience method for fetching events over a date range."""
        return self.calendar_events(start_date=start, end_date=end)

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
        all_day: bool = False,
        account: str = "",
    ) -> dict:
        r = self._client.post(
            "/calendar/events",
            json={
                "summary": summary,
                "start": start,
                "end": end,
                "location": location,
                "description": description,
                "all_day": all_day,
                "account": account,
            },
        )
        r.raise_for_status()
        return r.json()

    def create_quick_event(self, text: str, account: str = "") -> dict:
        r = self._client.post(
            "/calendar/events/quick",
            json={"text": text, "account": account},
        )
        r.raise_for_status()
        return r.json()

    def update_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        account: str = "",
        **fields,
    ) -> bool:
        r = self._client.put(
            f"/calendar/events/{event_id}",
            params={
                "calendar_id": calendar_id,
                "account": account,
            },
            json=fields,
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    def delete_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        account: str = "",
    ) -> bool:
        r = self._client.delete(
            f"/calendar/events/{event_id}",
            params={
                "calendar_id": calendar_id,
                "account": account,
            },
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── Notes ────────────────────────────────────────────────────────────

    def notes(self, limit: int = 50) -> list[dict]:
        r = self._client.get("/notes", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    def note(self, note_id: str) -> dict:
        r = self._client.get(f"/notes/{note_id}")
        r.raise_for_status()
        return r.json()

    # ── Reminders ────────────────────────────────────────────────────────

    def reminder_lists(self) -> list[dict]:
        r = self._client.get("/reminders/lists")
        r.raise_for_status()
        return r.json()

    def reminders(
        self,
        list_name: str | None = None,
        show_completed: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        params: dict = {"show_completed": show_completed, "limit": limit}
        if list_name:
            params["list_name"] = list_name
        r = self._client.get("/reminders", params=params)
        r.raise_for_status()
        return r.json()

    def reminder_complete(self, reminder_id: str) -> bool:
        r = self._client.post(f"/reminders/{reminder_id}/complete")
        r.raise_for_status()
        return r.json().get("ok", False)

    def reminder_uncomplete(self, reminder_id: str) -> bool:
        r = self._client.post(f"/reminders/{reminder_id}/uncomplete")
        r.raise_for_status()
        return r.json().get("ok", False)

    def reminder_create(
        self,
        title: str,
        list_name: str = "Reminders",
        due_date: str = "",
        notes: str = "",
        priority: int = 0,
        flagged: bool = False,
    ) -> bool:
        r = self._client.post(
            "/reminders",
            json={
                "title": title,
                "list_name": list_name,
                "due_date": due_date,
                "notes": notes,
                "priority": priority,
                "flagged": flagged,
            },
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    def reminder_edit(
        self,
        reminder_id: str,
        title: str | None = None,
        due_date: str | None = None,
        notes: str | None = None,
        priority: int | None = None,
        flagged: bool | None = None,
    ) -> bool:
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if due_date is not None:
            payload["due_date"] = due_date
        if notes is not None:
            payload["notes"] = notes
        if priority is not None:
            payload["priority"] = priority
        if flagged is not None:
            payload["flagged"] = flagged
        r = self._client.put(f"/reminders/{reminder_id}", json=payload)
        r.raise_for_status()
        return r.json().get("ok", False)

    def reminder_delete(self, reminder_id: str) -> bool:
        r = self._client.delete(f"/reminders/{reminder_id}")
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── Google Tasks ─────────────────────────────────────────────────────

    def list_task_lists(self, account: str = "") -> list[dict]:
        params = {"account": account} if account else {}
        r = self._client.get("/tasks/lists", params=params)
        r.raise_for_status()
        return r.json()

    def list_tasks(
        self,
        list_id: str = "@default",
        show_completed: bool = False,
        limit: int = 100,
        account: str = "",
    ) -> list[dict]:
        params = {
            "list_id": list_id,
            "show_completed": show_completed,
            "limit": limit,
        }
        if account:
            params["account"] = account
        r = self._client.get("/tasks", params=params)
        r.raise_for_status()
        return r.json()

    def create_task(
        self,
        title: str,
        list_id: str = "@default",
        due: str = "",
        notes: str = "",
        account: str = "",
    ) -> bool:
        params = {}
        if account:
            params["account"] = account
        r = self._client.post(
            "/tasks",
            json={"title": title, "list_id": list_id, "due": due, "notes": notes},
            params=params,
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    def complete_task(self, task_id: str, list_id: str = "@default", account: str = "") -> bool:
        params = {}
        if account:
            params["account"] = account
        r = self._client.post(
            f"/tasks/{task_id}/complete", params={**{"list_id": list_id}, **params}
        )
        r.raise_for_status()
        return r.json().get("ok", False)

    def update_task(
        self,
        task_id: str,
        list_id: str = "@default",
        title: str | None = None,
        due: str | None = None,
        notes: str | None = None,
        account: str = "",
    ) -> bool:
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if due is not None:
            payload["due"] = due
        if notes is not None:
            payload["notes"] = notes
        params = {"list_id": list_id}
        if account:
            params["account"] = account
        r = self._client.put(f"/tasks/{task_id}", json=payload, params=params)
        r.raise_for_status()
        return r.json().get("ok", False)

    def delete_task(self, task_id: str, list_id: str = "@default", account: str = "") -> bool:
        params = {"list_id": list_id}
        if account:
            params["account"] = account
        r = self._client.delete(f"/tasks/{task_id}", params=params)
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── WhatsApp ─────────────────────────────────────────────────────────

    # ── Departure Times ────────────────────────────────────────────────

    def departure_times(
        self,
        origin: str = "",
        mode: str = "driving",
        buffer_minutes: int = 10,
        lookahead_hours: int = 24,
    ) -> list[dict]:
        params = {
            "mode": mode,
            "buffer_minutes": buffer_minutes,
            "lookahead_hours": lookahead_hours,
        }
        if origin:
            params["origin"] = origin
        r = self._client.get("/calendar/departure-times", params=params)
        r.raise_for_status()
        return r.json()

    def travel_time(self, origin: str, destination: str, mode: str = "driving") -> dict:
        r = self._client.get(
            "/maps/travel-time", params={"origin": origin, "destination": destination, "mode": mode}
        )
        r.raise_for_status()
        return r.json()

    # ── WhatsApp ─────────────────────────────────────────────────────────

    def whatsapp_contacts(self, limit: int = 20) -> list[dict]:
        r = self._client.get("/whatsapp/contacts", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    def whatsapp_messages(self, chat_name: str, limit: int = 50) -> list[dict]:
        r = self._client.get(f"/whatsapp/messages/{chat_name}", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    # ── Scheduled Messages ───────────────────────────────────────────────

    def list_scheduled(self, status: str = "pending") -> list[dict]:
        r = self._client.get("/scheduled", params={"status": status})
        r.raise_for_status()
        return r.json()

    def schedule_message(
        self,
        source: str,
        conv_id: str,
        text: str,
        send_at: str,
        account: str = "",
    ) -> dict:
        r = self._client.post(
            "/scheduled",
            json={
                "source": source,
                "conv_id": conv_id,
                "text": text,
                "send_at": send_at,
                "account": account,
            },
        )
        r.raise_for_status()
        return r.json()

    def cancel_scheduled(self, msg_id: int) -> bool:
        r = self._client.delete(f"/scheduled/{msg_id}")
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── Follow-up Reminders ──────────────────────────────────────────────

    def list_followups(self, status: str = "active") -> list[dict]:
        r = self._client.get("/followups", params={"status": status})
        r.raise_for_status()
        return r.json()

    def create_followup(
        self,
        source: str,
        conv_id: str,
        remind_after: str,
        reminder_title: str,
        thread_id: str = "",
        reminder_list: str = "Reminders",
    ) -> dict:
        r = self._client.post(
            "/followups",
            json={
                "source": source,
                "conv_id": conv_id,
                "thread_id": thread_id,
                "remind_after": remind_after,
                "reminder_title": reminder_title,
                "reminder_list": reminder_list,
            },
        )
        r.raise_for_status()
        return r.json()

    def cancel_followup(self, fid: int) -> bool:
        r = self._client.delete(f"/followups/{fid}")
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── Task ↔ Message Links ────────────────────────────────────────────

    def list_task_links(
        self,
        message_id: str = "",
        message_source: str = "",
        task_id: str = "",
        task_source: str = "",
    ) -> list[dict]:
        params = {}
        if message_id:
            params["message_id"] = message_id
            params["message_source"] = message_source
        if task_id:
            params["task_id"] = task_id
            params["task_source"] = task_source
        r = self._client.get("/tasks/links", params=params)
        r.raise_for_status()
        return r.json()

    def link_task_to_message(
        self,
        task_id: str,
        task_source: str,
        message_id: str,
        message_source: str,
        thread_id: str = "",
        account: str = "",
    ) -> dict:
        r = self._client.post(
            "/tasks/links",
            json={
                "task_id": task_id,
                "task_source": task_source,
                "message_id": message_id,
                "message_source": message_source,
                "thread_id": thread_id,
                "account": account,
            },
        )
        r.raise_for_status()
        return r.json()

    def unlink_task(self, link_id: int) -> bool:
        r = self._client.delete(f"/tasks/links/{link_id}")
        r.raise_for_status()
        return r.json().get("ok", False)

    def create_task_from_message(
        self,
        message_id: str,
        message_source: str,
        title: str,
        task_type: str = "google_tasks",
        list_id: str = "@default",
        list_name: str = "Reminders",
        notes: str = "",
        thread_id: str = "",
        account: str = "",
    ) -> dict:
        r = self._client.post(
            "/tasks/from-message",
            json={
                "message_id": message_id,
                "message_source": message_source,
                "title": title,
                "task_type": task_type,
                "list_id": list_id,
                "list_name": list_name,
                "notes": notes,
                "thread_id": thread_id,
                "account": account,
            },
        )
        r.raise_for_status()
        return r.json()

    # ── GitHub ──────────────────────────────────────────────────────────

    def github_notifications(self, all_notifs: bool = False) -> list[dict]:
        r = self._client.get("/github/notifications", params={"all": all_notifs})
        r.raise_for_status()
        return r.json()

    def github_mark_read(self, notification_id: str) -> bool:
        r = self._client.post(f"/github/notifications/{notification_id}/read")
        r.raise_for_status()
        return r.json().get("ok", False)

    def github_mark_all_read(self) -> bool:
        r = self._client.post("/github/notifications/read-all")
        r.raise_for_status()
        return r.json().get("ok", False)

    def github_pulls(self, repo: str | None = None) -> list[dict]:
        params = {"repo": repo} if repo else {}
        r = self._client.get("/github/pulls", params=params)
        r.raise_for_status()
        return r.json()

    # ── Google Drive ────────────────────────────────────────────────────

    def drive_files(
        self,
        query: str = "",
        shared: bool = False,
        limit: int = 20,
        account: str = "",
        folder_id: str = "",
    ) -> list[dict]:
        params: dict = {
            "q": query,
            "shared": shared,
            "limit": limit,
            "account": account,
        }
        if folder_id:
            params["folder_id"] = folder_id
        r = self._client.get("/drive/files", params=params)
        r.raise_for_status()
        return r.json()

    def drive_file(self, file_id: str, account: str = "") -> dict:
        r = self._client.get(f"/drive/files/{file_id}", params={"account": account})
        r.raise_for_status()
        return r.json()

    def drive_download(self, file_id: str, account: str = "") -> bytes:
        """Download file content from Drive. Returns raw bytes."""
        r = self._client.get(
            f"/drive/files/{file_id}/download",
            params={"account": account},
            timeout=120,
        )
        r.raise_for_status()
        return r.content

    def drive_upload(self, file_path: str, folder_id: str = "", account: str = "") -> dict:
        from pathlib import Path as P

        p = P(file_path)
        with open(p, "rb") as f:
            r = self._client.post(
                "/drive/upload",
                files={"file": (p.name, f)},
                data={"folder_id": folder_id, "account": account},
                timeout=120,
            )
        r.raise_for_status()
        return r.json()

    def drive_create_folder(self, name: str, parent_id: str = "", account: str = "") -> dict:
        r = self._client.post(
            "/drive/folder",
            json={"name": name, "parent_id": parent_id, "account": account},
        )
        r.raise_for_status()
        return r.json()

    def drive_delete(self, file_id: str, account: str = "") -> bool:
        r = self._client.delete(f"/drive/files/{file_id}", params={"account": account})
        r.raise_for_status()
        return r.json().get("ok", False)

    # ── Contacts ─────────────────────────────────────────────────────────

    def contacts_search(self, q: str = "", limit: int = 20) -> list[dict]:
        r = self._client.get("/contacts/search", params={"q": q, "limit": limit})
        r.raise_for_status()
        return r.json()

    def contacts_profile(self, contact_id: str) -> dict:
        r = self._client.get(f"/contacts/{contact_id}/profile")
        r.raise_for_status()
        return r.json()

    def favorites(self) -> list[str]:
        r = self._client.get("/contacts/favorites")
        r.raise_for_status()
        return r.json().get("favorites", [])

    def favorite_add(self, contact_id: str) -> list[str]:
        r = self._client.post(f"/contacts/favorites/{contact_id}")
        r.raise_for_status()
        return r.json().get("favorites", [])

    def favorite_remove(self, contact_id: str) -> list[str]:
        r = self._client.delete(f"/contacts/favorites/{contact_id}")
        r.raise_for_status()
        return r.json().get("favorites", [])

    # ── Ambient / Dictation / LLM ──────────────────────────────────────

    def ambient_start(self) -> dict:
        r = self._client.post("/ambient/start")
        r.raise_for_status()
        return r.json()

    def ambient_stop(self) -> dict:
        r = self._client.post("/ambient/stop")
        r.raise_for_status()
        return r.json()

    def ambient_status(self) -> dict:
        r = self._client.get("/ambient/status")
        r.raise_for_status()
        return r.json()

    def ambient_notes(self, limit: int = 50, q: str = "") -> list[dict]:
        r = self._client.get("/ambient/notes", params={"limit": limit, "q": q})
        r.raise_for_status()
        return r.json()

    def ambient_note(self, date: str) -> dict:
        r = self._client.get(f"/ambient/notes/{date}")
        r.raise_for_status()
        return r.json()

    def dictation_start(self) -> dict:
        r = self._client.post("/dictation/start")
        r.raise_for_status()
        return r.json()

    def dictation_stop(self) -> dict:
        r = self._client.post("/dictation/stop")
        r.raise_for_status()
        return r.json()

    def dictation_status(self) -> dict:
        r = self._client.get("/dictation/status")
        r.raise_for_status()
        return r.json()

    def ambient_transcript(self, limit: int = 50) -> dict:
        r = self._client.get("/ambient/transcript", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    def voice_config(self) -> dict:
        r = self._client.get("/voice/config")
        r.raise_for_status()
        return r.json()

    def voice_config_update(self, **kwargs) -> dict:  # type: ignore[type-arg]
        r = self._client.put("/voice/config", json=kwargs)
        r.raise_for_status()
        return r.json()

    # ── Search ───────────────────────────────────────────────────────────

    def search(
        self,
        q: str,
        sources: list[str] | None = None,
        limit: int = 50,
        from_addr: str = "",
        before: str = "",
        after: str = "",
        has_attachment: bool = False,
        is_unread: bool = False,
    ) -> dict:
        payload: dict = {"q": q, "limit": limit}
        if sources is not None:
            payload["sources"] = sources
        if from_addr:
            payload["from_addr"] = from_addr
        if before:
            payload["before"] = before
        if after:
            payload["after"] = after
        if has_attachment:
            payload["has_attachment"] = True
        if is_unread:
            payload["is_unread"] = True
        r = self._client.post("/search", json=payload)
        r.raise_for_status()
        return r.json()

    def autocomplete(self, draft: str, **kwargs) -> str | None:  # type: ignore[type-arg]
        r = self._client.post("/autocomplete", json={"draft": draft, **kwargs})
        r.raise_for_status()
        return r.json().get("completion")

    def llm_status(self) -> dict:
        r = self._client.get("/llm/status")
        r.raise_for_status()
        return r.json()

    def llm_warmup(self) -> dict:
        r = self._client.post("/llm/warmup")
        r.raise_for_status()
        return r.json()

    # ── AI ───────────────────────────────────────────────────────────────

    def ai_briefing(self) -> dict:
        r = self._client.post("/ai/briefing", timeout=60)
        r.raise_for_status()
        return r.json()

    def ai_triage(self, conversations: list) -> dict:  # type: ignore[type-arg]
        r = self._client.post("/ai/triage", json={"conversations": conversations})
        r.raise_for_status()
        return r.json()

    def ai_summarize(self, thread_id: str, messages: list) -> dict:  # type: ignore[type-arg]
        r = self._client.post(
            "/ai/summarize",
            json={"thread_id": thread_id, "messages": messages},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def ai_extract_actions(self, text: str) -> dict:
        r = self._client.post("/ai/extract-actions", json={"text": text})
        r.raise_for_status()
        return r.json()

    # ── Accounts ─────────────────────────────────────────────────────────

    def accounts(self) -> dict:
        r = self._client.get("/accounts")
        r.raise_for_status()
        return r.json()

    def add_account(self) -> dict:
        r = self._client.post("/accounts/add", timeout=120)
        r.raise_for_status()
        return r.json()

    def reauth_account(self, email: str) -> dict:
        r = self._client.post("/accounts/reauth", json={"email": email}, timeout=120)
        r.raise_for_status()
        return r.json()

    # ── Notifications ────────────────────────────────────────────────────

    def notification_config(self) -> dict:
        r = self._client.get("/notifications/config")
        r.raise_for_status()
        return r.json()

    def update_notification_config(self, cfg: dict) -> bool:
        r = self._client.put("/notifications/config", json=cfg)
        r.raise_for_status()
        return r.json().get("ok", False)

    def test_notification(self, title: str, body: str = "") -> bool:
        r = self._client.post("/notifications/test", json={"title": title, "body": body})
        r.raise_for_status()
        return r.json().get("sent", False)
