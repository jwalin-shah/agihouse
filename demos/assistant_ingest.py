"""
Ingest 60 days of Gmail + iMessage from ~/projects/inbox/.inbox_index.sqlite3
into a TL store.

Schema built:
  Sent[p_from, p_to, m]    sparse {0,1}
  Thread[m, t]             sparse {0,1}        — m belongs to thread t
  Question[m]              dense  {0,1}        — m contains a question
  Incoming[m]              dense  {0,1}        — m was received by me
  Outgoing[m]              dense  {0,1}        — m was sent by me
  MsgDate[m]               dense  float        — days-ago at ingest time
  TopicEmb[m, d]           dense  float        — MiniLM embedding of body

Plus metadata: people_id_map, msg_id_map, thread_id_map, msg_meta (sender, ts, snippet).

Saves to demos/assistant_store.pt
"""
from __future__ import annotations
import sqlite3
import json
import re
import os
import time
from pathlib import Path
import torch

from assistant_resolve import build_resolver, norm_handle, norm_email

INBOX_DB   = os.path.expanduser("~/projects/inbox/.inbox_index.sqlite3")
OUT_PATH   = Path(__file__).parent / "assistant_store.pt"
DAYS       = 60
SELF_GMAIL = {"jshah1331@gmail.com", "jwalinshah13@gmail.com", "jwalinsshah@gmail.com"}
SELF_ID    = "me@self"  # canonical id for the user across all channels

EMAIL_IN_NAME = re.compile(r"<([^>]+)>")


def parse_sender_email(s: str) -> str:
    """Gmail sender field is 'Name <email>' or bare email."""
    if not s: return ""
    m = EMAIL_IN_NAME.search(s)
    return norm_email(m.group(1) if m else s)


def parse_recipients(json_str: str) -> list[str]:
    try:
        arr = json.loads(json_str or "[]")
    except Exception:
        return []
    out = []
    for r in arr:
        if "<" in r:
            m = EMAIL_IN_NAME.search(r)
            if m: out.append(norm_email(m.group(1)))
        elif r:
            out.append(norm_email(r))
    return out


def has_question(text: str) -> bool:
    if not text: return False
    # cheap heuristic: '?' present, plus an interrogative word nearby
    if "?" not in text: return False
    return bool(re.search(r"\b(what|when|where|why|who|how|can|could|would|should|is|are|did|do|does)\b",
                          text.lower()))


def main():
    print("[ingest] loading resolver...")
    resolver = build_resolver()

    print("[ingest] loading embedder (MiniLM-L6-v2, ~80MB first time)...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    EMB_DIM = model.get_sentence_embedding_dimension()

    print(f"[ingest] reading inbox sqlite (last {DAYS} days)...")
    con = sqlite3.connect(f"file:{INBOX_DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute(f"""
        SELECT source, account, external_id, thread_id, created_at,
               sender, recipients_json, subject, snippet, body_text
        FROM items
        WHERE created_at >= datetime('now', '-{DAYS} days')
          AND is_deleted = 0
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()
    con.close()
    print(f"[ingest] {len(rows)} rows")

    # Build id maps
    people:  dict[str, int] = {SELF_ID: 0}
    msgs:    dict[str, int] = {}
    threads: dict[str, int] = {}

    def pid(canon: str) -> int:
        if canon not in people: people[canon] = len(people)
        return people[canon]

    def mid(uid: str) -> int:
        if uid not in msgs: msgs[uid] = len(msgs)
        return msgs[uid]

    def tid(t: str) -> int:
        if t not in threads: threads[t] = len(threads)
        return threads[t]

    sent_triples:   list[tuple[int,int,int]] = []
    thread_pairs:   list[tuple[int,int]]     = []
    question_idx:   list[int] = []
    incoming_idx:   list[int] = []
    outgoing_idx:   list[int] = []
    days_ago:       list[float] = []
    bodies:         list[str] = []
    msg_meta:       list[dict] = []  # parallel to msgs index

    now_ts = time.time()
    skipped = 0

    for source, account, external_id, thread_id, created_at, sender, rec_json, subject, snippet, body in rows:
        uid = f"{source}:{account}:{external_id}"
        m = mid(uid)
        # pad
        while len(bodies) <= m:
            bodies.append(""); days_ago.append(0.0); msg_meta.append({})

        # direction & participants
        if source == "gmail":
            from_email = parse_sender_email(sender)
            to_emails  = parse_recipients(rec_json)
            is_outgoing = from_email in SELF_GMAIL or account == from_email
            if is_outgoing:
                from_canon = SELF_ID
                tos = [resolver.canon(e) for e in to_emails if e and e not in SELF_GMAIL]
            else:
                from_canon = resolver.canon(from_email) if from_email else ""
                tos = [SELF_ID]
            if not from_canon or not tos:
                skipped += 1; continue
            for t in tos:
                if not t: continue
                sent_triples.append((pid(from_canon), pid(t), m))
        elif source == "imessage":
            is_outgoing = (sender == "Me")
            other = norm_handle(sender) if not is_outgoing else None
            # Without recipients_json we can't recover the other side of an outgoing iMessage
            # cleanly; the inbox project doesn't store it. Skip outgoing iMessages for now —
            # the followups demo only needs incoming anyway.
            if is_outgoing:
                outgoing_idx.append(m)
                # still record body so embedding exists, but no edge
                bodies[m] = (subject + "\n" + (body or snippet or "")).strip()
                days_ago[m] = (now_ts - _ts(created_at)) / 86400.0
                msg_meta[m] = {"src": source, "from": "me", "to": "?", "ts": created_at,
                                "snippet": (snippet or body or "")[:120]}
                continue
            if not other: skipped += 1; continue
            from_canon = resolver.canon(other)
            sent_triples.append((pid(from_canon), pid(SELF_ID), m))
        else:
            skipped += 1; continue

        # message-level features
        text = ((subject or "") + "\n" + (body or snippet or "")).strip()
        bodies[m] = text
        days_ago[m] = (now_ts - _ts(created_at)) / 86400.0

        if is_outgoing: outgoing_idx.append(m)
        else: incoming_idx.append(m)

        if has_question(text) and not is_outgoing:
            question_idx.append(m)

        thread_pairs.append((m, tid(thread_id or uid)))

        msg_meta[m] = {
            "src": source,
            "from": sender if not is_outgoing else "me",
            "to":   "me" if not is_outgoing else (tos[0] if source == "gmail" else "?"),
            "ts": created_at,
            "snippet": (snippet or body or "")[:120],
        }

    print(f"[ingest] mapped: {len(people)-1} people, {len(msgs)} msgs, {len(threads)} threads (skipped {skipped})")
    print(f"[ingest]   incoming={len(incoming_idx)}  outgoing={len(outgoing_idx)}  questions={len(question_idx)}")

    print("[ingest] embedding bodies (batched)...")
    t0 = time.time()
    embs = model.encode(bodies, batch_size=64, normalize_embeddings=True,
                        show_progress_bar=False, convert_to_tensor=True)
    print(f"[ingest]   embedded {len(bodies)} msgs in {time.time()-t0:.1f}s")

    P, M, T = len(people), len(msgs), len(threads)

    def sparse(triples, shape):
        if not triples:
            return torch.sparse_coo_tensor(torch.empty((len(shape), 0), dtype=torch.long),
                                            torch.empty(0), shape).coalesce()
        idx = torch.tensor(triples, dtype=torch.long).t()
        val = torch.ones(idx.shape[1])
        return torch.sparse_coo_tensor(idx, val, shape).coalesce()

    def onehot(idx_list, n):
        v = torch.zeros(n)
        v[idx_list] = 1.0
        return v

    store = {
        "Sent":     sparse(sent_triples, (P, P, M)),
        "Thread":   sparse(thread_pairs, (M, T)),
        "Question": onehot(question_idx, M),
        "Incoming": onehot(incoming_idx, M),
        "Outgoing": onehot(outgoing_idx, M),
        "MsgDate":  torch.tensor(days_ago, dtype=torch.float32),
        "TopicEmb": embs.cpu(),
        "people":   people,
        "msgs":     msgs,
        "threads":  threads,
        "msg_meta": msg_meta,
        "self_id":  SELF_ID,
        "emb_dim":  EMB_DIM,
    }
    torch.save(store, OUT_PATH)
    print(f"[ingest] wrote {OUT_PATH}")


def _ts(s: str) -> float:
    """ISO8601 → unix seconds."""
    from datetime import datetime
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


if __name__ == "__main__":
    main()
