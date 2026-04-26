"""Demo harness — rehearse the on-stage flow without live Google/iMessage.

Runs canned scenarios end-to-end through `notify()` so the user can practice
the demo even when tokens, Wi-Fi, or test data aren't cooperating.
"""

from __future__ import annotations

import os
import sys
import time

from anthropic import Anthropic

from context import ContextItem, render_for_prompt
from llm import synthesize_nudge
from output import notify

_client: Anthropic | None = None
_MODEL = "claude-haiku-4-5-20251001"

RECALL_SYSTEM = """You are an ambient assistant whispering into a heads-up display.

Given a person's name and recent messages with them, produce ONE calm, useful
sentence (max ~22 words) reminding the user who this person is and the latest
open thread. Voice is calm, present, helpful. No greetings, no preamble, no emoji.

Pattern when a reply is owed: "<Person> — last text <when> about <thing>; you
haven't replied."
Pattern otherwise: state the most relevant recent fact.
If context is empty or irrelevant, output the single token "skip"."""


def _client_singleton() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def synthesize_recall(person: str, context_items: list[ContextItem]) -> str | None:
    """Return a one-line HUD digest about `person`, or None to skip."""
    user = f"""Person: {person}

Recent messages with them:
{render_for_prompt(context_items)}

Write the whisper now."""

    resp = _client_singleton().messages.create(
        model=_MODEL,
        max_tokens=80,
        system=[
            {
                "type": "text",
                "text": RECALL_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text or text.lower() == "skip":
        return None
    return text


def _header(name: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n[scenario] {name}\n{bar}", flush=True)


# --- scenarios ------------------------------------------------------------


def scenario_leaving_for_meeting() -> None:
    _header("leaving_for_meeting")
    items = [
        ContextItem(
            source="gmail",
            sender="partner@sequoiacap.com",
            timestamp="2026-04-24T16:32",
            snippet="Looking forward to tomorrow — can you send the latest deck before the sync?",
        ),
        ContextItem(
            source="imessage",
            sender="Daniel Park",
            timestamp="2026-04-25T08:14",
            snippet="prepped the demo, deck v7 is in the drive — break a leg today",
        ),
    ]
    departure_line = "Leave in 12 min"
    nudge = synthesize_nudge(
        event_summary="Investor sync with Sequoia",
        starts_in_minutes=50,
        location="Sequoia HQ, Menlo Park",
        departure_line=departure_line,
        context_block=render_for_prompt(items),
    )
    notify(departure_line)
    if nudge:
        notify(nudge)


def scenario_who_is_this_daniel() -> None:
    _header("who_is_this_daniel")
    items = [
        ContextItem(
            source="imessage",
            sender="Daniel Park",
            timestamp="2026-04-21T19:42",
            snippet="deck ready by Friday?",
        ),
        ContextItem(
            source="gmail",
            sender="daniel.park@example.com",
            timestamp="2026-04-19T11:08",
            snippet="Subject: Sequoia intro — happy to make the warm intro, lmk what to send.",
        ),
    ]
    line = synthesize_recall("Daniel Park", items)
    if line:
        notify(line)


def scenario_who_is_this_sarah() -> None:
    _header("who_is_this_sarah")
    items = [
        ContextItem(
            source="imessage",
            sender="Sarah Chen",
            timestamp="2026-04-22T12:55",
            snippet="thursday lunch?",
        ),
    ]
    line = synthesize_recall("Sarah Chen", items)
    if line:
        notify(line)


def scenario_commitment_followup() -> None:
    _header("commitment_followup")
    transcript = "yeah I'll send you the deck tonight"
    print(f"(heard) {transcript}", flush=True)
    commitment = "send deck tonight to Daniel Park"
    notify(f"Noted: {commitment}.")


def scenario_calibrated_silence() -> None:
    """Show the agent deliberately staying quiet on an ambiguous trigger.

    Pare-Bench frame: the model with the highest task success was the one that
    proposed least often. This scenario rehearses that posture on stage.
    """
    _header("calibrated_silence")
    transcript = "we should grab coffee sometime"
    print(f"(heard) {transcript}", flush=True)
    print("(decision) ambiguous commitment — no time, no person, no action verb on a calendar.", flush=True)
    print("(decision) confidence below threshold → STAY SILENT.", flush=True)
    # Intentionally no notify(). The HUD remains empty.
    print("(HUD) <empty>", flush=True)


SCENARIOS = {
    "leaving_for_meeting": scenario_leaving_for_meeting,
    "who_is_this_daniel": scenario_who_is_this_daniel,
    "who_is_this_sarah": scenario_who_is_this_sarah,
    "commitment_followup": scenario_commitment_followup,
    "calibrated_silence": scenario_calibrated_silence,
}


def _list() -> None:
    print("scenarios:")
    for name in SCENARIOS:
        print(f"  {name}")
    print("\nusage: python demo.py <scenario_name>")
    print("       python demo.py --all")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _list()
        return 0
    arg = argv[1]
    if arg == "--all":
        names = list(SCENARIOS)
        for i, name in enumerate(names):
            SCENARIOS[name]()
            if i < len(names) - 1:
                time.sleep(4)
        return 0
    fn = SCENARIOS.get(arg)
    if fn is None:
        print(f"unknown scenario: {arg}", file=sys.stderr)
        _list()
        return 1
    fn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
