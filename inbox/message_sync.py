from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from message_index_store import IndexedItem, MessageIndexStore
from services import IMSG_DB, _clean_body, _decode_body, _parse_email_address, google_auth_all

GMAIL_BOOTSTRAP_BATCH_SIZE = 250
GMAIL_INCREMENTAL_BATCH_SIZE = 100
IMESSAGE_PROGRESS_EVERY = 250


def _iso_from_ms(value: int | str | None) -> str:
    if not value:
        return datetime.now(UTC).isoformat()
    milliseconds = int(value)
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()


def _iso_from_apple_seconds(value: float | int | None) -> str:
    if not value:
        return datetime.now(UTC).isoformat()
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _gmail_recipients(headers: dict[str, str]) -> list[str]:
    to_raw = headers.get("To", "")
    if not to_raw:
        return []
    return [part.strip() for part in to_raw.split(",") if part.strip()]


def _gmail_item(account: str, message: dict[str, Any]) -> IndexedItem:
    payload = message.get("payload", {})
    headers = {header["name"]: header["value"] for header in payload.get("headers", [])}
    raw_from = headers.get("From", "Unknown")
    display_name, email_addr = _parse_email_address(raw_from)
    body_text = _decode_body(payload) or ""
    created_at = _iso_from_ms(message.get("internalDate"))
    labels = message.get("labelIds", [])
    subject = headers.get("Subject", "")
    sender = (
        "Me" if email_addr.lower() == account.lower() else (display_name or email_addr or "Unknown")
    )
    return IndexedItem(
        source="gmail",
        account=account,
        external_id=str(message["id"]),
        thread_id=str(message.get("threadId", message["id"])),
        kind="email",
        created_at=created_at,
        updated_at=created_at,
        ingested_at=datetime.now(UTC).isoformat(),
        sender=sender,
        recipients_json=_json(_gmail_recipients(headers)),
        subject=subject,
        snippet=(message.get("snippet") or subject)[:240],
        body_text=body_text,
        body_hash=_hash_body(body_text),
        labels_json=_json(labels),
        raw_pointer=f"gmail:{account}:{message['id']}",
        is_deleted=0,
        is_read=0 if "UNREAD" in labels else 1,
    )


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def _fetch_gmail_full_message(service: Any, message_id: str) -> dict[str, Any]:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def sync_gmail_bootstrap(store: MessageIndexStore) -> dict[str, int]:
    gmail_services, _, _, _, _, _ = google_auth_all()
    stats: dict[str, int] = {}
    for account, service in gmail_services.items():
        state = store.get_sync_state("gmail", account) or {}
        metadata = state.get("metadata") or {}
        newest_seen = int(state.get("checkpoint_value", "0") or 0)
        page_token = str(metadata.get("bootstrap_page_token") or "") or None
        count = 0
        store.mark_sync_started(
            source="gmail",
            account=account,
            checkpoint_type="internalDateMs",
            checkpoint_value=str(newest_seen),
            metadata={"bootstrap_page_token": page_token or "", "messages_processed": count},
        )
        try:
            while True:
                request = (
                    service.users()
                    .messages()
                    .list(
                        userId="me",
                        maxResults=GMAIL_BOOTSTRAP_BATCH_SIZE,
                        includeSpamTrash=False,
                        pageToken=page_token,
                    )
                )
                response = request.execute()
                messages = response.get("messages", [])
                if not messages:
                    break
                for stub in messages:
                    full_message = _fetch_gmail_full_message(service, stub["id"])
                    store.upsert_item(_gmail_item(account, full_message))
                    newest_seen = max(newest_seen, int(full_message.get("internalDate", 0) or 0))
                    count += 1
                page_token = response.get("nextPageToken")
                store.update_sync_progress(
                    source="gmail",
                    account=account,
                    checkpoint_type="internalDateMs",
                    checkpoint_value=str(newest_seen),
                    metadata={
                        "bootstrap_page_token": page_token or "",
                        "messages_processed": count,
                    },
                )
                if not page_token:
                    break
        except Exception as exc:
            store.record_sync_error(source="gmail", account=account, error=str(exc))
            raise
        store.set_sync_state(
            source="gmail",
            account=account,
            checkpoint_type="internalDateMs",
            checkpoint_value=str(newest_seen),
            full_sync=True,
            status="idle",
            metadata={"bootstrap_page_token": "", "messages_processed": count},  # nosec B105
        )
        stats[account] = count
    return stats


def sync_gmail_incremental(store: MessageIndexStore) -> dict[str, int]:
    gmail_services, _, _, _, _, _ = google_auth_all()
    stats: dict[str, int] = {}
    for account, service in gmail_services.items():
        state = store.get_sync_state("gmail", account) or {}
        checkpoint = int(state.get("checkpoint_value", "0") or 0)
        page_token: str | None = None
        newest_seen = checkpoint
        count = 0
        stop = False
        store.mark_sync_started(
            source="gmail",
            account=account,
            checkpoint_type="internalDateMs",
            checkpoint_value=str(checkpoint),
            metadata={"messages_processed": 0},
        )
        try:
            while not stop:
                response = (
                    service.users()
                    .messages()
                    .list(
                        userId="me",
                        maxResults=GMAIL_INCREMENTAL_BATCH_SIZE,
                        includeSpamTrash=False,
                        pageToken=page_token,
                    )
                    .execute()
                )
                messages = response.get("messages", [])
                if not messages:
                    break
                for stub in messages:
                    full_message = _fetch_gmail_full_message(service, stub["id"])
                    internal_date = int(full_message.get("internalDate", 0) or 0)
                    if internal_date <= checkpoint:
                        stop = True
                        break
                    store.upsert_item(_gmail_item(account, full_message))
                    newest_seen = max(newest_seen, internal_date)
                    count += 1
                store.update_sync_progress(
                    source="gmail",
                    account=account,
                    checkpoint_type="internalDateMs",
                    checkpoint_value=str(newest_seen),
                    metadata={"messages_processed": count},
                )
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except Exception as exc:
            store.record_sync_error(source="gmail", account=account, error=str(exc))
            raise
        store.set_sync_state(
            source="gmail",
            account=account,
            checkpoint_type="internalDateMs",
            checkpoint_value=str(newest_seen),
            full_sync=False,
            status="idle",
            metadata={"messages_processed": count},
        )
        stats[account] = count
    return stats


def _imessage_messages_after(last_rowid: int | None = None) -> list[sqlite3.Row]:
    if not IMSG_DB.exists():
        return []
    conn = sqlite3.connect(f"file:{Path(IMSG_DB)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        predicate = "AND m.rowid > ?" if last_rowid is not None else ""
        params: tuple[object, ...] = (last_rowid,) if last_rowid is not None else ()
        _q = (
            "SELECT m.rowid AS message_rowid, cmj.chat_id AS chat_id, m.text AS text,"
            " m.is_from_me AS is_from_me, m.date / 1000000000 + 978307200 AS ts, h.id AS sender_id"
            " FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.rowid"
            f" LEFT JOIN handle h ON h.rowid = m.handle_id WHERE m.text IS NOT NULL {predicate} ORDER BY m.rowid ASC"  # nosec B608
        )
        rows = conn.execute(_q, params).fetchall()
    finally:
        conn.close()
    return rows


def _imessage_item(row: sqlite3.Row) -> IndexedItem:
    body = _clean_body(row["text"] or "")
    created_at = _iso_from_apple_seconds(row["ts"])
    sender = "Me" if row["is_from_me"] else (row["sender_id"] or "?")
    return IndexedItem(
        source="imessage",
        account="local",
        external_id=str(row["message_rowid"]),
        thread_id=str(row["chat_id"]),
        kind="imessage",
        created_at=created_at,
        updated_at=created_at,
        ingested_at=datetime.now(UTC).isoformat(),
        sender=sender,
        recipients_json=_json([]),
        subject="",
        snippet=body[:240],
        body_text=body,
        body_hash=_hash_body(body),
        labels_json=_json([]),
        raw_pointer=f"imessage:{row['chat_id']}:{row['message_rowid']}",
        is_deleted=0,
        is_read=1 if row["is_from_me"] else 0,
    )


def sync_imessage_bootstrap(store: MessageIndexStore) -> dict[str, int]:
    state = store.get_sync_state("imessage", "local") or {}
    highest_rowid = int(state.get("checkpoint_value", "0") or 0)
    count = 0
    store.mark_sync_started(
        source="imessage",
        account="local",
        checkpoint_type="rowid",
        checkpoint_value=str(highest_rowid),
        metadata={"messages_processed": 0},
    )
    try:
        rows = _imessage_messages_after(highest_rowid or None)
        for row in rows:
            body = _clean_body(row["text"] or "")
            if not body:
                continue
            store.upsert_item(_imessage_item(row))
            highest_rowid = max(highest_rowid, int(row["message_rowid"]))
            count += 1
            if count % IMESSAGE_PROGRESS_EVERY == 0:
                store.update_sync_progress(
                    source="imessage",
                    account="local",
                    checkpoint_type="rowid",
                    checkpoint_value=str(highest_rowid),
                    metadata={"messages_processed": count},
                )
    except Exception as exc:
        store.record_sync_error(source="imessage", account="local", error=str(exc))
        raise
    store.set_sync_state(
        source="imessage",
        account="local",
        checkpoint_type="rowid",
        checkpoint_value=str(highest_rowid),
        full_sync=True,
        status="idle",
        metadata={"messages_processed": count},
    )
    return {"local": count}


def sync_imessage_incremental(store: MessageIndexStore) -> dict[str, int]:
    state = store.get_sync_state("imessage", "local") or {}
    last_rowid = int(state.get("checkpoint_value", "0") or 0)
    highest_rowid = last_rowid
    count = 0
    store.mark_sync_started(
        source="imessage",
        account="local",
        checkpoint_type="rowid",
        checkpoint_value=str(last_rowid),
        metadata={"messages_processed": 0},
    )
    try:
        rows = _imessage_messages_after(last_rowid)
        for row in rows:
            body = _clean_body(row["text"] or "")
            if not body:
                continue
            store.upsert_item(_imessage_item(row))
            highest_rowid = max(highest_rowid, int(row["message_rowid"]))
            count += 1
            if count % IMESSAGE_PROGRESS_EVERY == 0:
                store.update_sync_progress(
                    source="imessage",
                    account="local",
                    checkpoint_type="rowid",
                    checkpoint_value=str(highest_rowid),
                    metadata={"messages_processed": count},
                )
    except Exception as exc:
        store.record_sync_error(source="imessage", account="local", error=str(exc))
        raise
    store.set_sync_state(
        source="imessage",
        account="local",
        checkpoint_type="rowid",
        checkpoint_value=str(highest_rowid),
        full_sync=False,
        status="idle",
        metadata={"messages_processed": count},
    )
    return {"local": count}


def bootstrap(store: MessageIndexStore) -> dict[str, dict[str, int]]:
    result = {
        "gmail": sync_gmail_bootstrap(store),
        "imessage": sync_imessage_bootstrap(store),
    }
    store.rebuild_threads()
    return result


def incremental(store: MessageIndexStore) -> dict[str, dict[str, int]]:
    result = {
        "gmail": sync_gmail_incremental(store),
        "imessage": sync_imessage_incremental(store),
    }
    store.rebuild_threads()
    return result


def print_summary(store: MessageIndexStore, limit: int) -> None:
    for row in store.list_threads(limit=limit, actionable_only=True, newest_only=True):
        print(
            f"{row['latest_item_at']} | {row['source']} | {row['actionability']} | "
            f"{row['urgency']} | {row['summary']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize raw inbox sources into a local index."
    )
    parser.add_argument("mode", choices=["bootstrap", "incremental", "summary"])
    parser.add_argument("--db", default="", help="Override index database path.")
    parser.add_argument("--limit", type=int, default=20, help="Summary row limit.")
    args = parser.parse_args()

    store = MessageIndexStore(Path(args.db).expanduser() if args.db else None)
    if args.mode == "bootstrap":
        print(bootstrap(store))
    elif args.mode == "incremental":
        print(incremental(store))
    else:
        print_summary(store, args.limit)


if __name__ == "__main__":
    main()
