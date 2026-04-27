"""
Killer query: "Unanswered questions to me, ranked by sender engagement
(× optional topic similarity, × recency)."

This is a 3-hop relational query with one fuzzy leg:
  hop 1  Question(m) ∧ Incoming(m)            — m is a question to me
  hop 2  ¬∃ m'. Outgoing(m') ∧ Thread(m', t)  — I never replied in thread t = thread(m)
  hop 3  Engagement(sender_of(m))             — sender's total messages to me (relational aggregate)
  fuzzy  cos(TopicEmb[m], embed(query))       — topic similarity

Output prints provenance: which hops contributed, the exact thread/sender, the
sender engagement count, the topic similarity. Judges can audit every result.
"""
from __future__ import annotations
from pathlib import Path
import sys
import torch

STORE = Path(__file__).parent / "assistant_store.pt"


def load_store():
    return torch.load(STORE, weights_only=False)


def followups(store, topic_query: str | None = None, k: int = 8):
    Sent     = store["Sent"]            # sparse [P, P, M]
    Thread   = store["Thread"]          # sparse [M, T]
    Question = store["Question"]        # dense  [M]
    Incoming = store["Incoming"]        # dense  [M]
    Outgoing = store["Outgoing"]        # dense  [M]
    MsgDate  = store["MsgDate"]         # dense  [M], days ago
    Topic    = store["TopicEmb"]        # dense  [M, d]
    me       = store["people"][store["self_id"]]
    msg_meta = store["msg_meta"]
    inv_p    = {v: k for k, v in store["people"].items()}
    inv_m    = {v: k for k, v in store["msgs"].items()}

    P, _, M = Sent.shape

    # ── Hop 2: which threads contain ANY outgoing message from me? ────────────
    Thread_dense = Thread.to_dense()              # [M, T]
    thread_has_reply = (Outgoing.unsqueeze(1) * Thread_dense).sum(dim=0).clamp(max=1)  # [T]
    msg_thread_replied = (Thread_dense * thread_has_reply.unsqueeze(0)).sum(dim=1).clamp(max=1)  # [M]

    # candidate msg score (relational legs only):
    # is a question, incoming, and its thread has no reply
    candidate = Question * Incoming * (1.0 - msg_thread_replied)   # [M]

    # ── Hop 3: sender engagement = total msgs sender has sent to me ───────────
    # Engagement[p] = sum over m of Sent[p, me, m]
    Sent_to_me = torch.sparse.sum(Sent.coalesce(), dim=2).to_dense()  # [P, P]
    Engagement = Sent_to_me[:, me]                                     # [P]
    log_eng = torch.log1p(Engagement)                                  # damp heavy hitters

    # Per candidate msg, find its sender (one p where Sent[p, me, m] = 1)
    Sent_pme = torch.zeros(P, M)
    Sent_idx = Sent.coalesce().indices()           # [3, nnz]
    Sent_val = Sent.coalesce().values()
    mask = (Sent_idx[1] == me)
    p_idx = Sent_idx[0][mask]; m_idx = Sent_idx[2][mask]
    Sent_pme[p_idx, m_idx] = Sent_val[mask]
    sender_log_eng_per_m = (Sent_pme * log_eng.unsqueeze(1)).sum(dim=0)   # [M]
    sender_id_per_m      = Sent_pme.argmax(dim=0)                          # [M]
    has_sender           = (Sent_pme.sum(dim=0) > 0).float()               # [M]

    # ── Recency (gentle decay over 60 days) ───────────────────────────────────
    recency = torch.exp(-MsgDate / 30.0)            # [M]

    # ── Optional fuzzy topic leg ──────────────────────────────────────────────
    if topic_query:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        q = model.encode([topic_query], normalize_embeddings=True, convert_to_tensor=True)[0].cpu()
        topic_score = (Topic @ q).clamp(min=0)      # [M]
    else:
        topic_score = torch.ones(M)

    # ── Compose: this is the einsum-equivalent line ───────────────────────────
    final = candidate * has_sender * sender_log_eng_per_m * recency * topic_score   # [M]

    # Dedupe by sender: keep only top msg per sender (so one chatty person
    # doesn't fill the result page).
    best_per_sender: dict[int, tuple[float, int]] = {}
    for m_i in range(M):
        if final[m_i].item() <= 0: continue
        s_i = int(sender_id_per_m[m_i])
        cur = best_per_sender.get(s_i)
        if cur is None or final[m_i].item() > cur[0]:
            best_per_sender[s_i] = (final[m_i].item(), m_i)
    ranked = sorted(best_per_sender.values(), reverse=True)[:k]
    top_vals = torch.tensor([s for s, _ in ranked])
    top_idx  = torch.tensor([m for _, m in ranked], dtype=torch.long)
    class _T: values = top_vals; indices = top_idx
    top = _T()
    print()
    print("┌─ TL followups query " + ("· topic=" + repr(topic_query) if topic_query else "") + " " + "─" * 30)
    print("│ legs:  Question ∧ Incoming ∧ ¬thread_has_reply  ⊗  log(sender_engagement)  ⊗  recency"
          + ("  ⊗  cos(topic)" if topic_query else ""))
    print("└" + "─" * 80)
    for rank, (s, m) in enumerate(zip(top.values.tolist(), top.indices.tolist()), 1):
        if s <= 0: continue
        meta = msg_meta[m]
        sender = inv_p[int(sender_id_per_m[m])]
        eng    = int(Engagement[int(sender_id_per_m[m])].item())
        topic_s = float(topic_score[m]) if topic_query else None
        days   = float(MsgDate[m])
        print(f"\n  #{rank}  score={s:.3f}   {meta.get('src','?'):8s}  {days:5.1f}d ago")
        print(f"        sender:    {sender}  (sent me {eng} msgs in window)")
        print(f"        snippet:   {meta.get('snippet','')[:100]!r}")
        legs = ["Question✓", "Incoming✓", "ThreadUnreplied✓",
                f"Engagement={eng}", f"Recency={float(recency[m]):.2f}"]
        if topic_query: legs.append(f"Topic={topic_s:.2f}")
        print(f"        provenance: {' · '.join(legs)}")


def from_meeting_contacts(store, topic_query: str | None = None, k: int = 8):
    """4-hop query: messages from people I've shared a meeting with, ranked by
    topic + recency. The killer multi-hop demo.

    Hops:
      1. MyEvents[e]      = Attended[me, e]
      2. CoAttended[p]    = Σ_e Attended[p,e] · MyEvents[e]   (people in my meetings)
      3. MsgFromThem[m]   = Σ_p Sent[p, me, m] · CoAttended[p]
      fuzzy: cos(TopicEmb[m], q)
    """
    if "Attended" not in store:
        print("Run assistant_ingest_calendar.py first."); return

    Sent     = store["Sent"].coalesce()
    Attended = store["Attended"].coalesce().to_dense()
    Topic    = store["TopicEmb"]
    MsgDate  = store["MsgDate"]
    me       = store["people"][store["self_id"]]
    inv_p    = {v:k for k,v in store["people"].items()}
    msg_meta = store["msg_meta"]
    event_meta = store["event_meta"]
    P, _, M = Sent.shape

    MyEvents = Attended[me, :]                         # [E]
    if MyEvents.sum() == 0:
        print("No events with you as attendee in window."); return

    co_attended = (Attended * MyEvents.unsqueeze(0)).sum(dim=1)   # [P]
    co_attended[me] = 0  # don't count self

    # Project Sent[:, me, :] to a [P, M] dense slice
    idx, val = Sent.indices(), Sent.values()
    mask = (idx[1] == me)
    p_idx, m_idx = idx[0][mask], idx[2][mask]
    Sent_pme = torch.zeros(P, M)
    Sent_pme[p_idx, m_idx] = val[mask]

    msg_score_p = co_attended.unsqueeze(1) * Sent_pme   # [P, M]
    sender_per_m = msg_score_p.argmax(dim=0)            # [M]
    msg_score   = msg_score_p.sum(dim=0)                # [M]

    # which event(s) tied this sender to me?
    # contributing_event[m] = argmax_e (Attended[sender, e] * MyEvents[e])
    contributing_event_per_m = torch.zeros(M, dtype=torch.long)
    for m in range(M):
        if msg_score[m] <= 0: continue
        s = int(sender_per_m[m])
        ee = (Attended[s, :] * MyEvents).argmax().item()
        contributing_event_per_m[m] = ee

    recency = torch.exp(-MsgDate / 30.0)
    if topic_query:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        q = model.encode([topic_query], normalize_embeddings=True, convert_to_tensor=True)[0].cpu()
        topic_score = (Topic @ q).clamp(min=0)
    else:
        topic_score = torch.ones(M)

    final = msg_score * recency * topic_score   # [M]

    # dedupe by sender
    best: dict[int, tuple[float,int]] = {}
    for m in range(M):
        if final[m].item() <= 0: continue
        s = int(sender_per_m[m])
        if s == 0: continue  # filter orphans
        if s not in best or final[m].item() > best[s][0]:
            best[s] = (final[m].item(), m)
    ranked = sorted(best.values(), reverse=True)[:k]

    print()
    print("┌─ TL meeting-contacts query " + ("· topic=" + repr(topic_query) if topic_query else "") + " ─" * 5)
    print("│ legs: Attended[me,e] ⊗ Attended[p,e] ⊗ Sent[p,me,m]"
          + ("  ⊗  cos(topic)" if topic_query else "") + "  ⊗  recency")
    print("└" + "─" * 80)

    for rank, (score, m) in enumerate(ranked, 1):
        s = int(sender_per_m[m])
        ee = int(contributing_event_per_m[m])
        ev = event_meta[ee]
        meta = msg_meta[m]
        print(f"\n  #{rank}  score={score:.3f}   {meta.get('src','?'):8s}   {float(MsgDate[m]):.1f}d ago")
        print(f"        sender:    {inv_p[s]}")
        print(f"        msg:       {meta.get('snippet','')[:90]!r}")
        print(f"        via event: {ev['summary'][:60]!r}  @ {ev['start'][:16]}")
        legs = [f"CoAttend✓({ev['summary'][:24]})", "Sent[p,me,m]✓",
                f"Recency={float(recency[m]):.2f}"]
        if topic_query: legs.append(f"Topic={float(topic_score[m]):.2f}")
        print(f"        provenance: {' · '.join(legs)}")


def upcoming_events_with_msgs(store, k: int = 5):
    """For each upcoming event, surface the messages that semantically match it.
    This is the bridge across two modalities — calendar + inbox — that no single
    vector DB or SQL query gives you in one shot.

    For each event e (EventDate < 0, future):
       relevance(m, e) = cos(TopicEmb[m], EventTopicEmb[e]) · recency(m)
       top-3 m per e
    """
    if "EventTopicEmb" not in store:
        print("Run assistant_ingest_calendar.py first."); return

    EvEmb    = store["EventTopicEmb"]                  # [E, d]
    MsgEmb   = store["TopicEmb"]                       # [M, d]
    EvDate   = store["EventDate"]                      # [E]
    MsgDate  = store["MsgDate"]                        # [M]
    Incoming = store["Incoming"]
    msg_meta = store["msg_meta"]
    event_meta = store["event_meta"]

    # Recent (last 7d) or upcoming events
    nearby = (EvDate.abs() <= 7).nonzero().squeeze(-1)
    if nearby.numel() == 0:
        # fallback: most recent regardless
        nearby = EvDate.argsort()[:k]
    # Sort by absolute distance from now
    nearby = nearby[EvDate[nearby].abs().argsort()][:k]
    upcoming = nearby

    sim = MsgEmb @ EvEmb.t()                            # [M, E]
    recency = torch.exp(-MsgDate / 30.0).unsqueeze(1)   # [M, 1]
    score = sim * recency * Incoming.unsqueeze(1)       # [M, E]

    print()
    print("┌─ TL upcoming-events × msgs query " + "─" * 30)
    print("│ legs: future_event[e]  ⊗  cos(MsgEmb[m], EventEmb[e])  ⊗  recency  ⊗  Incoming")
    print("└" + "─" * 80)

    for e in upcoming.tolist():
        ev = event_meta[e]
        days_until = -float(EvDate[e])
        print(f"\n📅 {ev['summary'][:60]}    in {days_until:.1f}d  ({ev['start'][:16]})")
        col = score[:, e]
        top = torch.topk(col, 3)
        for s, m in zip(top.values.tolist(), top.indices.tolist()):
            if s <= 0: continue
            mm = msg_meta[m]
            print(f"   • sim={s:.3f}  {mm.get('src','?')}  {float(MsgDate[m]):.1f}d ago  "
                  f"from {mm.get('from','?')[:30]}: {mm.get('snippet','')[:70]!r}")


if __name__ == "__main__":
    store = load_store()
    args = sys.argv[1:]
    mode = "followups"
    if args and args[0] in ("followups", "meetings", "upcoming"):
        mode = args.pop(0)
    topic = " ".join(args) if args else None
    if mode == "meetings":
        from_meeting_contacts(store, topic_query=topic, k=8)
    elif mode == "upcoming":
        upcoming_events_with_msgs(store)
    else:
        followups(store, topic_query=topic, k=8)
