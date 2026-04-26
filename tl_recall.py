"""Tensor-Logic adapter for the voice-trigger pipeline.

Exposes `tl_oneliner(query, topic)` that runs a TL query against the prebuilt
store at ~/projects/tensor/demos/assistant_store.pt and returns a single line
short enough for the G2 HUD (576x288, ~36 chars/line).

The TL store is built once via:
    cd ~/projects/tensor
    python3 demos/assistant_ingest.py
    python3 demos/assistant_ingest_calendar.py
"""
from __future__ import annotations
import sys
from pathlib import Path

TL_DIR = Path(__file__).parent / "demos"
if str(TL_DIR) not in sys.path:
    sys.path.insert(0, str(TL_DIR))

import torch  # noqa: E402

_STORE = None
_EMB = None


def _load_store():
    global _STORE
    if _STORE is None:
        _STORE = torch.load(TL_DIR / "assistant_store.pt", weights_only=False)
    return _STORE


def _short(s: str, n: int = 30) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _short_sender(canon: str, store) -> str:
    if canon == store.get("self_id"): return "me"
    if canon.startswith("+1") and len(canon) <= 13: return canon[-7:]   # last 7 of phone
    if "@" in canon: return canon.split("@")[0][:14]
    return canon[:14]


def followups_oneliner(topic: str | None = None) -> str:
    """e.g. '3 followups: dad: did they reach out abt hiring · mihir: drop me sun?'"""
    from assistant_query import followups
    store = _load_store()
    Sent     = store["Sent"].coalesce()
    Question = store["Question"]; Incoming = store["Incoming"]; Outgoing = store["Outgoing"]
    Thread   = store["Thread"].coalesce().to_dense()
    Topic    = store["TopicEmb"]; MsgDate = store["MsgDate"]
    me       = store["people"][store["self_id"]]
    P, _, M  = Sent.shape

    thread_has_reply = (Outgoing.unsqueeze(1) * Thread).sum(0).clamp(max=1)
    msg_thread_replied = (Thread * thread_has_reply.unsqueeze(0)).sum(1).clamp(max=1)
    candidate = Question * Incoming * (1.0 - msg_thread_replied)

    Sent_to_me = torch.sparse.sum(Sent, dim=2).to_dense()[:, me]
    log_eng = torch.log1p(Sent_to_me)
    Sent_pme = torch.zeros(P, M)
    idx, val = Sent.indices(), Sent.values()
    mask = idx[1] == me
    Sent_pme[idx[0][mask], idx[2][mask]] = val[mask]
    sender_eng = (Sent_pme * log_eng.unsqueeze(1)).sum(0)
    sender_id  = Sent_pme.argmax(0)
    has_sender = (Sent_pme.sum(0) > 0).float()
    recency = torch.exp(-MsgDate / 30.0)

    topic_score = torch.ones(M)
    if topic:
        global _EMB
        if _EMB is None:
            from sentence_transformers import SentenceTransformer
            _EMB = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        q = _EMB.encode([topic], normalize_embeddings=True, convert_to_tensor=True)[0].cpu()
        topic_score = (Topic @ q).clamp(min=0)

    final = candidate * has_sender * sender_eng * recency * topic_score

    # dedupe by sender
    best: dict[int, tuple[float, int]] = {}
    inv_p = {v: k for k, v in store["people"].items()}
    msg_meta = store["msg_meta"]
    for m in range(M):
        s = float(final[m]);
        if s <= 0: continue
        sid = int(sender_id[m])
        if sid == 0: continue
        if sid not in best or s > best[sid][0]:
            best[sid] = (s, m)
    ranked = sorted(best.values(), reverse=True)[:3]
    if not ranked:
        return f"No unanswered questions{f' about {topic}' if topic else ''}."
    parts = []
    for _, m in ranked:
        sid = int(sender_id[m])
        sender = _short_sender(inv_p[sid], store)
        snip   = _short(msg_meta[m].get("snippet", ""), 28)
        parts.append(f"{sender}: {snip}")
    head = f"{len(ranked)} followups" + (f" · {topic}" if topic else "")
    return head + " — " + " · ".join(parts)


def upcoming_oneliner() -> str:
    """e.g. 'Cooper 4d: intro from Boardy · WashHealth 5d: appt reminder'"""
    store = _load_store()
    if "EventTopicEmb" not in store:
        return "No calendar in TL store. Run assistant_ingest_calendar.py."
    EvEmb = store["EventTopicEmb"]; MsgEmb = store["TopicEmb"]
    EvDate = store["EventDate"]; MsgDate = store["MsgDate"]
    Incoming = store["Incoming"]; msg_meta = store["msg_meta"]; ev_meta = store["event_meta"]

    nearby = (EvDate.abs() <= 7).nonzero().squeeze(-1)
    if nearby.numel() == 0:
        nearby = EvDate.argsort()[:2]
    nearby = nearby[EvDate[nearby].abs().argsort()][:2]

    sim = MsgEmb @ EvEmb.t()
    recency = torch.exp(-MsgDate / 30.0).unsqueeze(1)
    score = sim * recency * Incoming.unsqueeze(1)

    parts = []
    for e in nearby.tolist():
        ev = ev_meta[e]
        days = float(EvDate[e])
        days_str = f"{abs(days):.0f}d{'-ago' if days > 0 else ''}"
        top = torch.topk(score[:, e], 1)
        m = int(top.indices[0])
        snip = _short(msg_meta[m].get("snippet", ""), 24)
        parts.append(f"{_short(ev['summary'], 18)} {days_str}: {snip}")
    return " · ".join(parts) if parts else "No upcoming events."


# --- routing -------------------------------------------------------------
# Two layers:
#   1. Fast regex on a normalized (punct-stripped, lowercased) transcript.
#   2. If no regex hit, semantic match against canonical trigger phrases
#      using the same MiniLM model already loaded for topic embeddings.
#      Catches typos, weird Whisper punctuation, and paraphrases.
import re

# Strip punctuation + collapse whitespace for the regex layer.
_NORM = re.compile(r"[^a-z0-9 ]+")
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", _NORM.sub(" ", s.lower())).strip()

# Permissive regexes against the *normalized* string (so punctuation is gone).
RE_PLATE     = re.compile(r"\b(whats|what is|what s) (on my )?(plate|calendar|coming up|schedule|agenda)\b|\bwhat do i have (this week|today|tomorrow|coming|next)\b")
RE_FOLLOWUPS = re.compile(r"\b(any |my )?(follow ?ups?|unanswered|waiting on me|missed messages?)\b|\bwhat am i (missing|forgetting)\b|\bwho is waiting\b")
RE_FOLLOW_TOPIC = re.compile(r"\b(?:follow ?ups?|anything|messages?) (?:about|on|regarding|re) (.+?)(?:$| from| this| today)")

# Semantic-fallback canonical phrases (one per intent class).
_INTENT_PHRASES = {
    "upcoming": [
        "what's on my plate this week",
        "what do i have coming up",
        "what's on my calendar",
        "what's my schedule",
        "what meetings do i have",
        "what's coming up next",
    ],
    "followups": [
        "what followups do i have",
        "what questions haven't i answered",
        "who is waiting on me",
        "what messages did i miss",
        "what am i forgetting to reply to",
        "any unanswered messages",
    ],
}
_SEM_THRESHOLD = 0.55  # cosine threshold for accepting a semantic match
_PHRASE_EMBS = None


def _semantic_route(text: str) -> str | None:
    """Embed transcript and pick best intent if any class beats the threshold."""
    global _EMB, _PHRASE_EMBS
    if _EMB is None:
        from sentence_transformers import SentenceTransformer
        _EMB = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    if _PHRASE_EMBS is None:
        _PHRASE_EMBS = {}
        for intent, phrases in _INTENT_PHRASES.items():
            _PHRASE_EMBS[intent] = _EMB.encode(
                phrases, normalize_embeddings=True, convert_to_tensor=True
            ).cpu()
    q = _EMB.encode([text], normalize_embeddings=True, convert_to_tensor=True)[0].cpu()

    best_intent, best_score = None, 0.0
    for intent, embs in _PHRASE_EMBS.items():
        s = float((embs @ q).max())
        if s > best_score:
            best_intent, best_score = intent, s
    if best_score < _SEM_THRESHOLD:
        return None
    return best_intent


# Layer 3: fuzzy token match — handles whisper typos like "pholow" / "folow" /
# "agenda". Per-keyword fuzzy hit triggers an intent.
from rapidfuzz import fuzz

_FUZZY_KEYWORDS = {
    "followups": ["followups", "follow up", "unanswered", "waiting on me",
                  "missed messages", "forgetting"],
    "upcoming":  ["plate", "calendar", "agenda", "schedule", "meetings",
                  "coming up"],
}
_FUZZY_THRESHOLD = 80  # rapidfuzz partial_ratio (0-100); 80 ≈ one char off / typo


def _fuzzy_route(n: str) -> str | None:
    best_intent, best_score = None, 0
    for intent, kws in _FUZZY_KEYWORDS.items():
        for kw in kws:
            s = fuzz.partial_ratio(kw, n)
            if s > best_score:
                best_intent, best_score = intent, s
    return best_intent if best_score >= _FUZZY_THRESHOLD else None


def maybe_tl_oneliner(transcript: str) -> str | None:
    """Return a HUD-ready string if transcript triggers a TL query, else None.

    Layer 1: regex on normalized (punct-stripped) text — fastest.
    Layer 2: fuzzy keyword match — handles whisper typos.
    Layer 3: MiniLM semantic match — handles paraphrases.
    """
    n = _norm(transcript)
    if not n: return None

    m = RE_FOLLOW_TOPIC.search(n)
    if m:
        topic = m.group(1).strip().rstrip(".,!?")
        if topic and len(topic) <= 40:
            return followups_oneliner(topic=topic)

    if RE_FOLLOWUPS.search(n): return followups_oneliner()
    if RE_PLATE.search(n):     return upcoming_oneliner()

    if len(n) > 120: return None
    if not re.search(r"\b(what|who|when|where|how|any|my|i|do|tell)\b", n):
        return None

    # Layer 2 — fuzzy keyword
    intent = _fuzzy_route(n)
    if intent is None:
        # Layer 3 — semantic
        intent = _semantic_route(n)
    if intent == "upcoming":  return upcoming_oneliner()
    if intent == "followups": return followups_oneliner()
    return None


if __name__ == "__main__":
    # Smoke test
    for q in ["what's on my plate this week",
              "any followups",
              "anything about job interviews"]:
        out = maybe_tl_oneliner(q)
        print(f"\n>>> {q}\n    {out}")
