"""
Natural-language entry point: takes a question, asks Claude to pick a TL query
and bind a topic, then runs it. The agent only chooses *what to ask the
substrate* — the substrate (TL) is what answers, and the answer carries
provenance.

Usage:
    export ANTHROPIC_API_KEY=...
    python3 assistant_agent.py "what's on my plate this week"
    python3 assistant_agent.py "anything about job interviews i missed"
    python3 assistant_agent.py "what events do i have coming up"
"""
from __future__ import annotations
import os, sys, json, re
import anthropic

from assistant_query import load_store, followups, from_meeting_contacts, upcoming_events_with_msgs

MODEL = "claude-sonnet-4-6"

SYSTEM = """You route a personal-assistant question to one of three Tensor-Logic
queries over the user's inbox + calendar. Output ONLY a JSON object, no prose.

Available queries:

  "followups"  — Unanswered questions sent TO the user, ranked by sender
                 engagement and recency. Use when the user asks what they owe
                 a reply to, what they missed, who's waiting on them, or
                 any unanswered-questions / inbox-zero style ask.

  "upcoming"   — Recent and upcoming calendar events with the inbox messages
                 most semantically related to each. Use when the user asks
                 about meetings, events, what's on their calendar, what's
                 coming up, what's on their plate.

  "meetings"   — Messages from people the user shared a calendar meeting
                 with. Use when the user asks about contacts they've met
                 with recently or wants to follow up post-meeting.

Output schema:
  { "query": "<followups|upcoming|meetings>",
    "topic": "<short fuzzy topic string or null>",
    "rationale": "<one sentence>" }

Pick "topic" only when the user named a subject to filter on (e.g. "about
interviews", "regarding the dinner", "on the cerebral valley intro").
Otherwise topic = null.
"""


def route(question: str) -> dict:
    """Returns {query, topic, rationale}. Falls back to keyword routing if no API key."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_route(question)

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL, max_tokens=300, system=SYSTEM,
        messages=[{"role": "user", "content": question}],
    )
    txt = resp.content[0].text.strip()
    # Strip ```json fences if present
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.MULTILINE).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
        if m: return json.loads(m.group(0))
        raise


def _fallback_route(q: str) -> dict:
    ql = q.lower()
    if any(w in ql for w in ["meeting", "met with", "calendar", "event", "upcoming", "plate", "this week"]):
        return {"query": "upcoming", "topic": None, "rationale": "fallback: matched calendar keywords"}
    topic = None
    m = re.search(r"about (.+?)$|on (.+?)$|regarding (.+?)$", ql)
    if m: topic = next(g for g in m.groups() if g)
    return {"query": "followups", "topic": topic, "rationale": "fallback: defaulted to followups"}


def main():
    if len(sys.argv) < 2:
        print("usage: assistant_agent.py \"<question>\""); sys.exit(2)
    question = " ".join(sys.argv[1:])

    print(f"\n❓ {question}")
    decision = route(question)
    print(f"🤖 router: query={decision['query']!r} topic={decision.get('topic')!r}")
    print(f"   rationale: {decision.get('rationale','')}")

    store = load_store()
    q, t = decision["query"], decision.get("topic")
    if q == "followups":
        followups(store, topic_query=t, k=8)
    elif q == "upcoming":
        upcoming_events_with_msgs(store)
    elif q == "meetings":
        from_meeting_contacts(store, topic_query=t, k=8)
    else:
        print(f"unknown query: {q}")


if __name__ == "__main__":
    main()
