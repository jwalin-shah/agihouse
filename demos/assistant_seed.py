"""
Synthetic personal-context dataset → TL store.

Builds a clean, named, story-driven dataset where every demo query has a
satisfying answer. Use instead of real-inbox ingest when on stage:

    python3 demos/assistant_seed.py
    python3 demos/assistant_query.py followups
    python3 demos/assistant_query.py upcoming
    python3 demos/assistant_query.py followups "interview"

The cast & stories are designed so:
  • upcoming events × messages bridge fires cleanly (each event has 2+ msgs)
  • followups returns a varied set (not one chatty contact dominating)
  • topic-filtered followups for "interview", "dinner", "hackathon" all hit
  • cross-channel works (some people reach via email AND imessage)
"""
from __future__ import annotations
from pathlib import Path
import time, torch
from datetime import datetime, timedelta, timezone

OUT_PATH = Path(__file__).parent / "assistant_store.pt"
SELF_ID  = "me@self"
EMB_DIM  = 384  # match MiniLM's dim so query.py works unchanged


# ── Cast ──────────────────────────────────────────────────────────────────────
PEOPLE = {
    SELF_ID:                 "me",
    "sarah@boardy.com":      "Sarah Chen",         # network connector / intro source
    "alex@modernroots.io":   "Alex Park",          # eng manager hiring
    "priya@measure.ai":      "Priya Rao",          # founder, met at dinner
    "daniel@cv.events":      "Daniel Boardman",    # cerebral valley host
    "omar@measurelabs.com":  "Omar Hassan",        # boardy intro
    "ratnam@gmail.com":      "Ratnam Shah",        # cousin
    "mihir@gmail.com":       "Mihir Shah",         # brother
    "soham@gmail.com":       "Soham Patel",        # friend
    "+14155550101":          "Dad",                # iMessage only
    "+14085551122":          "Disha",              # dance practice friend
    "+17168032645":          "Tarun Reddi",        # close friend, hackathon partner
    "linkedin@noreply.com":  "LinkedIn Jobs",      # bot
}

# event_id → (summary, start_offset_days, attendees, organizer)
# Negative offsets = future, positive = past.
EVENTS = [
    ("dinner-mar-12",  "Founders dinner @ Sarah's",     -2,
     ["sarah@boardy.com", "priya@measure.ai", "alex@modernroots.io", SELF_ID],
     "sarah@boardy.com"),
    ("cv-summit",      "Cerebral Valley AI Summit",     -5,
     ["daniel@cv.events", SELF_ID, "priya@measure.ai"],
     "daniel@cv.events"),
    ("alex-1on1",      "1:1 with Alex (Modern Roots)",  -3,
     ["alex@modernroots.io", SELF_ID], "alex@modernroots.io"),
    ("omar-intro",     "Intro call: Omar @ Measure Labs", -7,
     ["omar@measurelabs.com", SELF_ID, "daniel@cv.events"],
     "daniel@cv.events"),
    ("agi-hackathon",  "AGI House Hackathon",            0,    # today
     [SELF_ID], SELF_ID),
    ("dance-prac",     "Dance practice w/ Disha",        2,
     ["+14085551122", SELF_ID], "+14085551122"),
    ("dad-call",       "Dad call",                       4,
     ["+14155550101", SELF_ID], SELF_ID),
    ("tarun-coffee",   "Coffee with Tarun",              1,
     ["+17168032645", SELF_ID], "+17168032645"),
]

# Messages: (msg_id, source, from_id, to_ids, thread_id, body, days_ago, has_question, my_reply_in_thread)
MSGS = [
    # ── Job interview thread (Alex) — open ASK, no reply ─────────────────
    ("m_alex_1", "gmail", "alex@modernroots.io", [SELF_ID], "th_alex_job",
     "Loved meeting at the founders dinner. Are you free for a final interview round next Tuesday? We'd love to make this work.",
     2, True, False),
    ("m_alex_2", "gmail", "alex@modernroots.io", [SELF_ID], "th_alex_job",
     "Also — what's your salary expectation? Let me know so I can prep the offer.",
     1, True, False),

    # ── Boardy/Omar intro — open ASK ────────────────────────────────────
    ("m_daniel_intro", "gmail", "daniel@cv.events", [SELF_ID, "omar@measurelabs.com"], "th_omar_intro",
     "Excited to connect you both — Jwalin is building agentic memory infra, Omar is exploring it for Measure Labs. Take it from here?",
     7, True, False),
    ("m_omar_followup", "gmail", "omar@measurelabs.com", [SELF_ID], "th_omar_intro",
     "Hey, picking this up — when works for a quick chat about what you're building? Tuesday or Wednesday?",
     5, True, False),

    # ── Cerebral Valley (Priya) — answered, irrelevant for followup ─────
    ("m_priya_1", "gmail", "priya@measure.ai", [SELF_ID], "th_priya_cv",
     "Great chatting at CV Summit. Sending the deck we discussed.",
     5, False, True),
    ("m_me_priya", "gmail", SELF_ID, ["priya@measure.ai"], "th_priya_cv",
     "Thanks! Got it. Let me read and circle back.",
     5, False, True),

    # ── Founders dinner — Sarah recap ──────────────────────────────────
    ("m_sarah_1", "imessage", "sarah@boardy.com", [SELF_ID], "th_sarah_dinner",
     "So good having you at dinner! Did Alex follow up about the role?",
     2, True, False),

    # ── Dad — random unanswered ─────────────────────────────────────────
    ("m_dad_1", "imessage", "+14155550101", [SELF_ID], "th_dad",
     "Did they reach out about the hiring thing yet?",
     17, True, False),
    ("m_dad_2", "imessage", "+14155550101", [SELF_ID], "th_dad",
     "When's the deadline for that interview?",
     19, True, False),

    # ── Hackathon stuff — friends asking ────────────────────────────────
    ("m_mihir_1", "imessage", "mihir@gmail.com", [SELF_ID], "th_mihir_hack",
     "Hows hacking going? Are you gonna win?",
     1, True, False),
    ("m_soham_1", "imessage", "soham@gmail.com", [SELF_ID], "th_soham_dinner",
     "What should we do for dessert tonight? Mihir's coming over.",
     1, True, False),

    # ── Disha — dance plan, partially open ─────────────────────────────
    ("m_disha_1", "imessage", "+14085551122", [SELF_ID], "th_disha",
     "Hiii when do you want to do our next dance practice?",
     3, True, False),

    # ── Tarun — coffee plan, open ─────────────────────────────────────
    ("m_tarun_1", "imessage", "+17168032645", [SELF_ID], "th_tarun_coffee",
     "Yo are we still on for coffee tomorrow? What time works for you?",
     1, True, False),
    ("m_tarun_2", "imessage", "+17168032645", [SELF_ID], "th_tarun_hack",
     "Hows the hackathon going? Did the tensor logic demo work?",
     0, True, False),

    # ── LinkedIn bot — should NOT show up as followup ──────────────────
    ("m_linkedin_1", "gmail", "linkedin@noreply.com", [SELF_ID], "th_linkedin",
     "Forward Deployed Engineer at HappyRobot: matches your profile.",
     6, False, False),

    # ── Ratnam — recent thread, replied (negative example) ─────────────
    ("m_ratnam_1", "gmail", "ratnam@gmail.com", [SELF_ID], "th_ratnam",
     "Hey Jwalin, want to do a video call this weekend?",
     8, True, True),
    ("m_me_ratnam", "gmail", SELF_ID, ["ratnam@gmail.com"], "th_ratnam",
     "Yes that would be great. Saturday afternoon?",
     8, False, True),
]


# ── Embedder ──────────────────────────────────────────────────────────────────
def embed_all(texts: list[str]) -> torch.Tensor:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return model.encode(texts, batch_size=64, normalize_embeddings=True,
                        show_progress_bar=False, convert_to_tensor=True).cpu()


# ── Build store ───────────────────────────────────────────────────────────────
def main():
    people = {pid: i for i, pid in enumerate(PEOPLE.keys())}
    me = people[SELF_ID]

    msg_ids = {m[0]: i for i, m in enumerate(MSGS)}
    thread_ids: dict[str, int] = {}
    def tid(t):
        if t not in thread_ids: thread_ids[t] = len(thread_ids)
        return thread_ids[t]

    sent_triples, thread_pairs = [], []
    bodies, days_ago, msg_meta = [], [], []
    incoming, outgoing, question = [], [], []

    for m_id, source, from_id, to_ids, thread, body, days, q, _replied in MSGS:
        m = msg_ids[m_id]
        bodies.append(body); days_ago.append(float(days))
        thread_pairs.append((m, tid(thread)))
        is_outgoing = (from_id == SELF_ID)
        if is_outgoing:
            for t in to_ids:
                if t in people: sent_triples.append((me, people[t], m))
            outgoing.append(m)
        else:
            if from_id in people:
                sent_triples.append((people[from_id], me, m))
            incoming.append(m)
            if q: question.append(m)
        msg_meta.append({
            "src": source,
            "from": PEOPLE.get(from_id, from_id) if not is_outgoing else "me",
            "to":   "me" if not is_outgoing else (PEOPLE.get(to_ids[0], to_ids[0]) if to_ids else "?"),
            "ts":   (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
            "snippet": body,
        })

    # Calendar tensors
    event_map = {e[0]: i for i, e in enumerate(EVENTS)}
    attended_triples, organized_triples = [], []
    ev_days, ev_summaries, ev_meta = [], [], []
    for ev_id, summary, days_off, attendees, organizer in EVENTS:
        e = event_map[ev_id]
        ev_days.append(float(days_off))
        ev_summaries.append(summary)
        for a in attendees:
            if a in people: attended_triples.append((people[a], e))
        if organizer in people: organized_triples.append((people[organizer], e))
        ev_meta.append({"summary": summary,
                        "start": (datetime.now(timezone.utc) + timedelta(days=-days_off)).isoformat()[:19],
                        "organizer": organizer, "attendees": attendees})

    print(f"[seed] embedding {len(bodies)} msgs + {len(ev_summaries)} events…")
    msg_embs = embed_all(bodies)
    ev_embs  = embed_all(ev_summaries)

    P, M, T, E = len(people), len(MSGS), len(thread_ids), len(EVENTS)

    def sparse(triples, shape):
        if not triples:
            return torch.sparse_coo_tensor(torch.empty((len(shape), 0), dtype=torch.long),
                                            torch.empty(0), shape).coalesce()
        idx = torch.tensor(triples, dtype=torch.long).t()
        return torch.sparse_coo_tensor(idx, torch.ones(idx.shape[1]), shape).coalesce()

    def onehot(idxs, n):
        v = torch.zeros(n); v[idxs] = 1.0; return v

    store = {
        "Sent":     sparse(sent_triples, (P, P, M)),
        "Thread":   sparse(thread_pairs, (M, T)),
        "Question": onehot(question, M),
        "Incoming": onehot(incoming, M),
        "Outgoing": onehot(outgoing, M),
        "MsgDate":  torch.tensor(days_ago, dtype=torch.float32),
        "TopicEmb": msg_embs,
        "Attended":      sparse(attended_triples,  (P, E)),
        "Organized":     sparse(organized_triples, (P, E)),
        "EventDate":     torch.tensor(ev_days, dtype=torch.float32),
        "EventTopicEmb": ev_embs,
        "Events":   event_map,
        "people":   people,
        "msgs":     msg_ids,
        "threads":  thread_ids,
        "msg_meta": msg_meta,
        "event_meta": ev_meta,
        "self_id":  SELF_ID,
        "emb_dim":  EMB_DIM,
    }
    torch.save(store, OUT_PATH)
    print(f"[seed] wrote {OUT_PATH}  (P={P}  M={M}  T={T}  E={E})")
    print(f"[seed] cast: {', '.join(name for pid, name in PEOPLE.items() if pid != SELF_ID)}")


if __name__ == "__main__":
    main()
