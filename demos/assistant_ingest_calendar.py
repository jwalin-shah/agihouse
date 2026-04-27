"""
Augment assistant_store.pt with Calendar tensors:

  Events[e]                       — int id (event uid)
  Attended[p, e]                  sparse {0,1}  — person attended event
  Organized[p, e]                 sparse {0,1}  — person organized event
  EventDate[e]                    dense  float  — days-ago (positive = past, negative = future)
  EventTopicEmb[e, d]             dense  float  — MiniLM embedding of summary+description

Also augments people map with any new attendees.
"""
from __future__ import annotations
from pathlib import Path
import time
import torch
from datetime import datetime

from assistant_resolve import build_resolver, SELF_HANDLES
from assistant_calendar import fetch_all_events

STORE = Path(__file__).parent / "assistant_store.pt"
SELF_ID = "me@self"


def _ts(s: str) -> float:
    s = s.replace("Z", "+00:00")
    if len(s) == 10: s += "T00:00:00+00:00"  # all-day event
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def main():
    store = torch.load(STORE, weights_only=False)
    print(f"[cal-ingest] loaded existing store: {len(store['people'])} people, {len(store['msgs'])} msgs")

    resolver = build_resolver()

    print("[cal-ingest] loading embedder...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    events = fetch_all_events()
    print(f"[cal-ingest] {len(events)} events")

    people: dict[str,int] = store["people"]
    def pid(canon: str) -> int:
        if canon not in people: people[canon] = len(people)
        return people[canon]

    event_map: dict[str,int] = {}
    attended_triples: list[tuple[int,int]] = []
    organized_triples: list[tuple[int,int]] = []
    days_ago: list[float] = []
    summaries: list[str] = []
    event_meta: list[dict] = []

    now = time.time()
    for ev in events:
        e = len(event_map); event_map[ev["id"]] = e
        days_ago.append((now - _ts(ev["start"])) / 86400.0)
        summaries.append((ev["summary"] + "\n" + (ev.get("description") or ""))[:1000])
        event_meta.append({
            "summary": ev["summary"], "start": ev["start"],
            "organizer": ev["organizer"], "attendees": ev["attendees"],
        })
        org_canon = SELF_ID if ev["organizer"] in SELF_HANDLES else (resolver.canon(ev["organizer"]) if ev["organizer"] else "")
        if org_canon:
            organized_triples.append((pid(org_canon), e))
            attended_triples.append((pid(org_canon), e))  # organizer attends by default
        for a in ev["attendees"]:
            a_canon = SELF_ID if a in SELF_HANDLES else resolver.canon(a)
            if a_canon:
                attended_triples.append((pid(a_canon), e))

    print(f"[cal-ingest] {len(event_map)} events, {len(attended_triples)} attendance edges")

    P = len(people); E = len(event_map)
    def sparse(triples, shape):
        if not triples:
            return torch.sparse_coo_tensor(torch.empty((len(shape),0), dtype=torch.long),
                                            torch.empty(0), shape).coalesce()
        idx = torch.tensor(triples, dtype=torch.long).t()
        val = torch.ones(idx.shape[1])
        return torch.sparse_coo_tensor(idx, val, shape).coalesce()

    embs = model.encode(summaries, batch_size=64, normalize_embeddings=True,
                        show_progress_bar=False, convert_to_tensor=True).cpu()

    # Resize Sent to match new P (we may have added attendees not seen as senders)
    Sent = store["Sent"].coalesce()
    if Sent.shape[0] != P or Sent.shape[1] != P:
        idx, val = Sent.indices(), Sent.values()
        store["Sent"] = torch.sparse_coo_tensor(idx, val, (P, P, Sent.shape[2])).coalesce()

    store["Events"]        = event_map
    store["Attended"]      = sparse(attended_triples,  (P, E))
    store["Organized"]     = sparse(organized_triples, (P, E))
    store["EventDate"]     = torch.tensor(days_ago, dtype=torch.float32)
    store["EventTopicEmb"] = embs
    store["event_meta"]    = event_meta
    store["people"]        = people

    torch.save(store, STORE)
    print(f"[cal-ingest] wrote {STORE}  (P={P}, E={E})")


if __name__ == "__main__":
    main()
