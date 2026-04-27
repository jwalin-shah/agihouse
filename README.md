# agihouse

Ambient agent for Even Realities G2 glasses.

## What it does

- Ingests mic audio from glasses via `POST /audio`.
- Runs local VAD + cloud ASR.
- Extracts structured actions from transcript text.
- Applies policy + proposal-first runtime before action execution.
- Pushes updates to the lens UI through SSE (`/events`).

## Where state lives

The phone/glasses are thin clients. They stream PCM audio to the laptop and render
SSE pushes from the laptop. The laptop owns the private runtime:

- audio pipeline, ASR, extraction, policy, and action dispatch
- pending proposals in memory
- durable action history in `state.db` (`actions_log`, reminders, calendar, notes, memories)
- scheduled iMessage jobs in `state.db` (`scheduled_imessages`)
- append-only policy decisions in `audit.log`
- optional tensor demo store in `demos/assistant_store.pt`

Nothing durable is intentionally stored on the phone by this app.

## Quick start

1. Install dependencies:

```bash
cd ~/projects/agihouse
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

2. Export environment:

```bash
set -a; source ~/.secrets/officeqa-local.env; set +a
```

3. Run backend:

```bash
.venv/bin/python -m uvicorn trigger_server:app --host 127.0.0.1 --port 9876
```

4. Run frontend:

```bash
cd g2-renderer
npm install
npm run dev
```

5. Run simulator:

```bash
evenhub-simulator -g http://localhost:5173
```

## Safe demo mode

- Proposal-first is enabled by default for write actions (`AGIHOUSE_PROPOSAL_ONLY=1`).
- To force non-executing behavior everywhere, set `mode: dry_run` in `policy.yaml`.
- Real iMessage sends require an explicit allow-list:

```bash
export AGIHOUSE_LIVE_IMESSAGE_HANDLES="+15551234567,person@example.com"
```

The seeded Tarun (`+15551234567`) and Sanjay Sai (`+15551234568`) demo handles
are allow-listed in code so the demo path works without extra env setup. Replace
them in `contacts.json` with real test handles before using this outside rehearsal.

Messages.app must be signed into iMessage on the laptop. The first live send may
prompt macOS Automation permission for Terminal/Python to control Messages.
- Use diagnostics to verify state:

```bash
curl http://127.0.0.1:9876/health
curl http://127.0.0.1:9876/diagnostics
```

### Glasses controls

- Single click: previous HUD item.
- Long press: next HUD item.
- Double click: exit the app and stop audio capture.

The browser simulator has richer demo controls: proposal confirm/reject buttons,
scheduled-send status, audit counts, and canned scenario buttons. The real G2
display stays compact because it is rendered through a 576x288 text container.

### Proposal workflow

- List pending proposals:

```bash
curl http://127.0.0.1:9876/proposals
```

- Confirm a proposal:

```bash
curl -X POST http://127.0.0.1:9876/proposals/<proposal_id>/confirm
```

- Reject a proposal:

```bash
curl -X POST http://127.0.0.1:9876/proposals/<proposal_id>/reject \
  -H "Content-Type: application/json" \
  -d '{"reason":"user_rejected"}'
```

Scheduled iMessages use the same proposal flow. A confirmed
`schedule_imessage` stores a laptop-side job in `state.db`; the server's
background scheduler sends it through Messages.app when due.

### Auditability

Every considered action goes through `audit.gate()` and writes a JSONL row to
`audit.log`. Every dispatched action also writes a structured row to
`state.db/actions_log`.

```bash
curl http://127.0.0.1:9876/audit/summary
curl http://127.0.0.1:9876/actions/recent
curl http://127.0.0.1:9876/memory/edges
curl http://127.0.0.1:9876/memories
curl http://127.0.0.1:9876/scheduled-imessages
python audit.py
```

### Perfect-ASR testing

Use `/transcript` to rehearse behavior as if the glasses heard perfectly. Omit
`event` to run the extractor; include `event` to test the action/proposal path
deterministically.

```bash
curl -X POST http://127.0.0.1:9876/transcript \
  -H "Content-Type: application/json" \
  -d '{"text":"I will text Tarun the demo tonight","event":{"action":"schedule_imessage","payload":{"handle":"Tarun","text":"demo link","send_at":"2026-04-27T20:00:00-07:00"},"confidence":0.94}}'
```

### Live ambient nudges

The server can also run the calendar/departure loop in the same process as the
G2 audio listener:

```bash
export AGIHOUSE_AMBIENT_TICK=1
export AGIHOUSE_AMBIENT_TICK_SECONDS=60
```

Conversation-derived write actions still become proposals. Time-based calendar
nudges can surface automatically when `ambient.tick()` sees an event or departure
window.

### Tensor logic demo

The tensor demo is local to the laptop and uses `demos/assistant_store.pt`.

```bash
python demos/assistant_seed.py
python demos/assistant_query.py followups
python demos/assistant_query.py upcoming
python demos/assistant_query.py meetings interview
```

Voice triggers such as "any followups about interview", "upcoming event context",
or "meeting contact messages" produce a one-line HUD summary through
`tensor_recall.py` when the store exists.

## Common failure modes

- `inbox_available: false` in `/health` or `/diagnostics`: inbox imports/deps are missing.
- No lens updates: backend up but no active `/events` subscriber.
- No transcript actions: missing `GROQ_API_KEY` or ASR/extractor response below threshold.
- Actions suppressed: check `audit.log` for policy decision reasons.
- iMessage dry-runs: handle is not in `AGIHOUSE_LIVE_IMESSAGE_HANDLES`, Messages is not signed in, or macOS Automation permission has not been granted.
- Tensor trigger is silent: `demos/assistant_store.pt` has not been built or `sentence-transformers` is not installed for seeded embedding generation.
