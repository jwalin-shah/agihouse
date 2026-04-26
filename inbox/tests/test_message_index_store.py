from message_index_store import IndexedItem, MessageIndexStore


def _item(
    *,
    source: str,
    account: str,
    external_id: str,
    thread_id: str,
    sender: str,
    subject: str = "",
    body: str = "",
    created_at: str = "2026-04-18T00:00:00+00:00",
    is_read: int = 0,
) -> IndexedItem:
    return IndexedItem(
        source=source,
        account=account,
        external_id=external_id,
        thread_id=thread_id,
        kind="email" if source == "gmail" else "imessage",
        created_at=created_at,
        updated_at=created_at,
        ingested_at=created_at,
        sender=sender,
        recipients_json="[]",
        subject=subject,
        snippet=subject or body[:50],
        body_text=body,
        body_hash=f"hash-{external_id}",
        labels_json="[]",
        raw_pointer=f"{source}:{external_id}",
        is_deleted=0,
        is_read=is_read,
    )


def test_upsert_item_replaces_existing_row(tmp_path):
    store = MessageIndexStore(tmp_path / "index.sqlite3")
    original = _item(
        source="gmail",
        account="a@example.com",
        external_id="m1",
        thread_id="t1",
        sender="Recruiter",
        subject="Initial",
    )
    updated = _item(
        source="gmail",
        account="a@example.com",
        external_id="m1",
        thread_id="t1",
        sender="Recruiter",
        subject="Updated subject",
    )

    store.upsert_item(original)
    store.upsert_item(updated)
    store.rebuild_threads()

    rows = store.list_threads(limit=5)
    assert len(rows) == 1
    assert rows[0]["latest_subject"] == "Updated subject"


def test_rebuild_threads_marks_human_reply_as_actionable(tmp_path):
    store = MessageIndexStore(tmp_path / "index.sqlite3")
    store.upsert_item(
        _item(
            source="gmail",
            account="a@example.com",
            external_id="m1",
            thread_id="t1",
            sender="Me",
            subject="Reaching out",
            created_at="2026-04-18T00:00:00+00:00",
            is_read=1,
        )
    )
    store.upsert_item(
        _item(
            source="gmail",
            account="a@example.com",
            external_id="m2",
            thread_id="t1",
            sender="Mehak Bhatia",
            subject="Consulting opportunity",
            body="Would you be open to a short call?",
            created_at="2026-04-18T01:00:00+00:00",
            is_read=0,
        )
    )

    store.rebuild_threads()
    rows = store.list_threads(limit=5, actionable_only=True)
    assert len(rows) == 1
    assert rows[0]["actionability"] == "reply"
    assert rows[0]["needs_reply"] == 1
    assert rows[0]["topic"] == "opportunity"


def test_rebuild_threads_classifies_otp_as_ignore(tmp_path):
    store = MessageIndexStore(tmp_path / "index.sqlite3")
    store.upsert_item(
        _item(
            source="imessage",
            account="local",
            external_id="1",
            thread_id="chat-1",
            sender="22395",
            body="Your verification code is: 995228",
        )
    )

    store.rebuild_threads()
    rows = store.list_threads(limit=5)
    assert len(rows) == 1
    assert rows[0]["noise_class"] == "otp"
    assert rows[0]["actionability"] == "ignore"


def test_sync_state_tracks_status_and_metadata(tmp_path):
    store = MessageIndexStore(tmp_path / "index.sqlite3")

    store.mark_sync_started(
        source="gmail",
        account="a@example.com",
        checkpoint_type="internalDateMs",
        checkpoint_value="100",
        metadata={"bootstrap_page_token": "page-2", "messages_processed": 25},
    )
    running = store.get_sync_state("gmail", "a@example.com")
    assert running is not None
    assert running["status"] == "running"
    assert running["metadata"]["bootstrap_page_token"] == "page-2"

    store.set_sync_state(
        source="gmail",
        account="a@example.com",
        checkpoint_type="internalDateMs",
        checkpoint_value="200",
        full_sync=True,
        status="idle",
        metadata={"bootstrap_page_token": "", "messages_processed": 50},
    )

    states = store.list_sync_states()
    assert len(states) == 1
    assert states[0]["status"] == "idle"
    assert states[0]["checkpoint_value"] == "200"
    assert states[0]["metadata"]["messages_processed"] == 50


def test_list_threads_supports_waiting_on_and_recent_views(tmp_path):
    store = MessageIndexStore(tmp_path / "index.sqlite3")
    store.upsert_item(
        _item(
            source="gmail",
            account="a@example.com",
            external_id="r1",
            thread_id="reply-thread",
            sender="Recruiter",
            subject="Interview tomorrow",
            body="Can you confirm your availability?",
            created_at="2026-04-18T01:00:00+00:00",
            is_read=0,
        )
    )
    store.upsert_item(
        _item(
            source="gmail",
            account="a@example.com",
            external_id="t1",
            thread_id="track-thread",
            sender="Billing",
            subject="Billing follow up",
            body="Your billing case is under review and we will get back to you soon.",
            created_at="2026-04-18T02:00:00+00:00",
            is_read=1,
        )
    )
    store.rebuild_threads()

    waiting = store.list_threads(
        limit=10,
        actions=("track",),
        has_open_loop=True,
        newest_only=True,
        sort_mode="recent",
    )
    assert len(waiting) == 1
    assert waiting[0]["thread_id"] == "track-thread"

    recent = store.list_threads(limit=10, newest_only=True, sort_mode="recent")
    assert [row["thread_id"] for row in recent] == ["track-thread", "reply-thread"]
