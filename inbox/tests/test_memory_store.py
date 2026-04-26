from memory_store import MemoryStore


def test_save_and_query_memory_entry(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    saved = store.save_entry(
        memory_type="person_preference",
        subject="Alice",
        content="Prefers concise replies.",
        source="email_thread",
        confidence=0.9,
    )

    assert saved["subject"] == "Alice"

    found = store.query_entries(query="concise", limit=5)
    assert len(found) == 1
    assert found[0]["memory_type"] == "person_preference"


def test_open_commitments_filter(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.save_entry(
        memory_type="commitment",
        subject="Tax filing",
        content="Finish draft by Friday.",
        status="open",
    )
    store.save_entry(
        memory_type="commitment",
        subject="Old task",
        content="Already done.",
        status="closed",
    )

    commitments = store.list_open_commitments()
    assert len(commitments) == 1
    assert commitments[0]["subject"] == "Tax filing"
