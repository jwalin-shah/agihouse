from __future__ import annotations

import os
from contextlib import suppress
from typing import Any

import httpx

SERVER_URL_ENV = "INBOX_SERVER_URL"
SERVER_TOKEN_ENV = "INBOX_SERVER_TOKEN"  # nosec: B105 - env var name, not a hardcoded credential
DEFAULT_SERVER_URL = "http://127.0.0.1:9849"


class InboxBackendError(RuntimeError):
    pass


class InboxBackend:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv(SERVER_URL_ENV, DEFAULT_SERVER_URL)).rstrip("/")
        self.token = token if token is not None else os.getenv(SERVER_TOKEN_ENV, "").strip()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise InboxBackendError(
                f"Unable to reach inbox server at {self.base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = response.text
            with suppress(Exception):
                detail = response.json().get("detail", detail)
            raise InboxBackendError(f"{method} {path} failed: {detail}")
        return response.json()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def list_inbox_threads(self, limit: int = 20, account: str = "") -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/gmail/conversations",
            params={"label": "INBOX", "limit": limit, "account": account},
        )

    async def search_email(
        self,
        query: str,
        limit: int = 20,
        account: str = "",
        label: str = "",
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/gmail/search",
            params={
                "q": query,
                "limit": limit,
                "account": account,
                "label": label,
            },
        )

    async def get_email_thread(
        self,
        message_id: str,
        thread_id: str = "",
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            f"/messages/gmail/{message_id}",
            params={"thread_id": thread_id},
        )

    async def send_email_reply(
        self,
        *,
        msg_id: str,
        body: str,
        thread_id: str = "",
        to: str = "",
        subject: str = "",
        message_id_header: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/messages/gmail/reply",
            json={
                "msg_id": msg_id,
                "body": body,
                "thread_id": thread_id,
                "to": to,
                "subject": subject,
                "message_id_header": message_id_header,
                "account": account,
            },
        )

    async def archive_email_thread(self, message_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/messages/gmail/{message_id}/archive")

    async def mark_email_read(self, message_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/messages/gmail/{message_id}/read")

    async def list_message_threads(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/conversations",
            params={"source": "imessage", "limit": limit},
        )

    async def get_message_thread(self, conv_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            f"/messages/imessage/{conv_id}",
            params={"limit": limit},
        )

    async def send_imessage(self, conv_id: str, text: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/messages/send",
            json={"conv_id": conv_id, "source": "imessage", "text": text},
        )

    async def list_notes(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._request("GET", "/notes", params={"limit": limit})

    async def get_note(self, note_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/notes/{note_id}")

    async def list_reminders(
        self,
        list_name: str = "",
        show_completed: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/reminders",
            params={
                "list_name": list_name or None,
                "show_completed": str(show_completed).lower(),
                "limit": limit,
            },
        )

    async def create_reminder(
        self,
        title: str,
        list_name: str = "Reminders",
        due_date: str = "",
        notes: str = "",
        priority: int = 0,
        flagged: bool = False,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
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

    async def complete_reminder(self, reminder_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/reminders/{reminder_id}/complete")

    async def uncomplete_reminder(self, reminder_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/reminders/{reminder_id}/uncomplete")

    async def list_task_lists(self, account: str = "") -> list[dict[str, Any]]:
        params = {"account": account} if account else {}
        return await self._request("GET", "/tasks/lists", params=params)

    async def list_tasks(
        self,
        list_id: str = "@default",
        show_completed: bool = False,
        limit: int = 100,
        account: str = "",
    ) -> list[dict[str, Any]]:
        params = {
            "list_id": list_id,
            "show_completed": str(show_completed).lower(),
            "limit": limit,
        }
        if account:
            params["account"] = account
        return await self._request("GET", "/tasks", params=params)

    async def create_task(
        self,
        title: str,
        list_id: str = "@default",
        due: str = "",
        notes: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        params = {}
        if account:
            params["account"] = account
        return await self._request(
            "POST",
            "/tasks",
            json={"title": title, "list_id": list_id, "due": due, "notes": notes},
            params=params,
        )

    async def complete_task(
        self, task_id: str, list_id: str = "@default", account: str = ""
    ) -> dict[str, Any]:
        params = {"list_id": list_id}
        if account:
            params["account"] = account
        return await self._request("POST", f"/tasks/{task_id}/complete", params=params)

    async def update_task(
        self,
        task_id: str,
        list_id: str = "@default",
        title: str | None = None,
        due: str | None = None,
        notes: str | None = None,
        account: str = "",
    ) -> dict[str, Any]:
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
        return await self._request("PUT", f"/tasks/{task_id}", json=payload, params=params)

    async def delete_task(
        self, task_id: str, list_id: str = "@default", account: str = ""
    ) -> dict[str, Any]:
        params = {"list_id": list_id}
        if account:
            params["account"] = account
        return await self._request("DELETE", f"/tasks/{task_id}", params=params)

    async def departure_times(
        self,
        origin: str = "",
        mode: str = "driving",
        buffer_minutes: int = 10,
        lookahead_hours: int = 24,
    ) -> list[dict[str, Any]]:
        params = {
            "mode": mode,
            "buffer_minutes": buffer_minutes,
            "lookahead_hours": lookahead_hours,
        }
        if origin:
            params["origin"] = origin
        return await self._request("GET", "/calendar/departure-times", params=params)

    async def travel_time(
        self, origin: str, destination: str, mode: str = "driving"
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/maps/travel-time",
            params={"origin": origin, "destination": destination, "mode": mode},
        )

    async def whatsapp_contacts(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._request("GET", "/whatsapp/contacts", params={"limit": limit})

    async def whatsapp_messages(self, chat_name: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/whatsapp/messages/{chat_name}", params={"limit": limit}
        )

    async def list_scheduled(self, status: str = "pending") -> list[dict[str, Any]]:
        return await self._request("GET", "/scheduled", params={"status": status})

    async def schedule_message(
        self,
        source: str,
        conv_id: str,
        text: str,
        send_at: str,
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/scheduled",
            json={
                "source": source,
                "conv_id": conv_id,
                "text": text,
                "send_at": send_at,
                "account": account,
            },
        )

    async def cancel_scheduled(self, msg_id: int) -> dict[str, Any]:
        return await self._request("DELETE", f"/scheduled/{msg_id}")

    async def list_followups(self, status: str = "active") -> list[dict[str, Any]]:
        return await self._request("GET", "/followups", params={"status": status})

    async def create_followup(
        self,
        source: str,
        conv_id: str,
        remind_after: str,
        reminder_title: str,
        thread_id: str = "",
        reminder_list: str = "Reminders",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
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

    async def cancel_followup(self, fid: int) -> dict[str, Any]:
        return await self._request("DELETE", f"/followups/{fid}")

    async def list_task_links(
        self,
        message_id: str = "",
        message_source: str = "",
        task_id: str = "",
        task_source: str = "",
    ) -> list[dict[str, Any]]:
        params: dict = {}
        if message_id:
            params["message_id"] = message_id
            params["message_source"] = message_source
        if task_id:
            params["task_id"] = task_id
            params["task_source"] = task_source
        return await self._request("GET", "/tasks/links", params=params)

    async def link_task_to_message(
        self,
        task_id: str,
        task_source: str,
        message_id: str,
        message_source: str,
        thread_id: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
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

    async def unlink_task(self, link_id: int) -> dict[str, Any]:
        return await self._request("DELETE", f"/tasks/links/{link_id}")

    async def create_task_from_message(
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
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
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

    async def search_all(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 50,
        from_addr: str = "",
        before: str = "",
        after: str = "",
        has_attachment: bool = False,
        is_unread: bool = False,
    ) -> dict[str, Any]:
        payload: dict = {
            "q": query,
            "sources": sources or ["all"],
            "limit": limit,
        }
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
        return await self._request("POST", "/search", json=payload)

    async def list_gmail_labels(self, account: str = "") -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/gmail/labels",
            params={"account": account},
        )

    async def batch_modify_emails(
        self,
        msg_ids: list[str],
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/gmail/batch-modify",
            json={
                "msg_ids": msg_ids,
                "add_label_ids": add_labels or [],
                "remove_label_ids": remove_labels or [],
                "account": account,
            },
        )

    async def create_gmail_filter(
        self,
        from_filter: str = "",
        subject_filter: str = "",
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/gmail/filters",
            json={
                "from_filter": from_filter,
                "subject_filter": subject_filter,
                "add_label_ids": add_labels or [],
                "remove_label_ids": remove_labels or [],
                "account": account,
            },
        )

    async def create_gmail_label(
        self,
        name: str,
        visibility: str = "labelShow",
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/gmail/labels",
            params={"name": name, "visibility": visibility, "account": account},
        )

    async def check_calendar_conflicts(
        self,
        start: str,
        end: str,
        account: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/calendar/conflicts",
            json={
                "start": start,
                "end": end,
                "account": account,
            },
        )

    async def extract_memory(
        self,
        text: str,
        source: str = "manual",
        auto_save: bool = False,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/memory/extract",
            params={"text": text, "source": source, "auto_save": str(auto_save).lower()},
        )
