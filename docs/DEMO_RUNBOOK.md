# Demo Runbook

## Goal

Show an ambient G2 agent that hears real-world speech, stays quiet when it should,
finds the right personal context in noisy data, proposes risky actions before
execution, and leaves an audit trail teammates can inspect after the demo.

## Strongest Existing Flow

1. Start backend, renderer, and simulator.
2. Open the browser renderer as the stage mirror.
3. Show diagnostics:

```bash
curl http://127.0.0.1:9876/diagnostics
```

4. Push a recall-style context card:

```bash
curl -X POST http://127.0.0.1:9876/demo/who_is_daniel
```

5. Trigger a safe write action from speech or through the extractor path. It
   should become a proposal, not immediately execute.
6. Show pending proposals:

```bash
curl http://127.0.0.1:9876/proposals
```

7. Confirm or reject, then show auditability:

```bash
curl http://127.0.0.1:9876/audit/summary
curl http://127.0.0.1:9876/actions/recent
curl http://127.0.0.1:9876/memory/edges
python audit.py
```

## Perfect-ASR Rehearsal

When the glasses/audio path is not the thing being tested, inject a transcript:

```bash
curl -X POST http://127.0.0.1:9876/transcript \
  -H "Content-Type: application/json" \
  -d '{"text":"I will text Tarun the demo tonight","event":{"action":"schedule_imessage","payload":{"handle":"Tarun","text":"demo link","send_at":"2026-04-27T20:00:00-07:00"},"confidence":0.94}}'
```

Then confirm it and show `/memory/edges`; the confirmation becomes learning
evidence for future target/context inference.

## Tensor Logic Proof

Build the noisy local store:

```bash
python demos/assistant_seed.py
```

Then show that the queries ignore similar-but-wrong data:

```bash
python demos/assistant_query.py followups interview
python demos/assistant_query.py followups tensor
python demos/assistant_query.py meetings
python demos/assistant_query.py upcoming
```

Good stage phrasing:

- "Find unanswered followups about interview."
- "Find unanswered followups about tensor."
- "Which upcoming events have matching recent messages?"
- "Which people from my meetings sent relevant messages?"

The seeded data includes distractors: same people with already-answered threads,
similar topics that are not questions, bot mail, and stale family messages.

## iMessage Reality Check

Live iMessage is intentionally opt-in:

```bash
export AGIHOUSE_LIVE_IMESSAGE_HANDLES="+15551234567"
python imessage_send.py +15551234567 "test from agihouse"
```

If the handle is not allow-listed, the action logs and renders as a dry-run. If
Messages.app is not signed in or macOS Automation permission is missing, the
send returns an error and the action log captures it.

## What Not To Add Before This PR

- Maps, routing, or location enrichment if another teammate owns it.
- More action types without audit coverage.
- More live writes before proposal-first controls are stable.

## Best Next Features

- Approval controls from the glasses, not only HTTP endpoints.
- Richer card payloads over SSE so the renderer does not infer type from text.
- A small audit viewer page for `audit.log` and `state.db/actions_log`.
- Contact resolution that maps names to iMessage handles before proposal.
- Demo fixtures that replay audio/transcripts deterministically.
- Native Even companion calendar/context APIs if the SDK exposes them in a future version.
