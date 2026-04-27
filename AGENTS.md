# agihouse — ambient agent for Even Realities G2

Ambient agent that listens through G2 glasses, transcribes via cloud ASR,
extracts structured actions, and surfaces output on the lens.

## Architecture (audio path)

```
G2 mic ─── PCM ───▶ /audio (trigger_server.py)
                     │
                     ▼
               GlassesAudioPipeline (audio_pipeline.py)
                     │
        VAD gate (vad.py — Silero, on-device)
        RMS amplitude gate (drops ambient/distant speech)
        Groq whisper-large-v3 (cloud ASR)
                     │
                     ▼  fan-out per transcript:
        ┌────────────┴────────────┐
        ▼                         ▼
  recall.py                 event_extractor.py
  (iMessage/Gmail)          (Groq llama-3.3-70b-versatile)
  → 🎙 echo on lens         → action JSON
  → name-based recall            │
                                 ▼
                          actions.py (DISPATCH)
                          → real macOS Reminders / iMessage
                          → calendar/notes/memories sandbox
                          → lens proposal with emoji prefix
                          → actions.jsonl audit trail
```

## File layout

| File | Purpose |
|---|---|
| `trigger_server.py` | FastAPI server. Routes: `/audio` (G2 mic in), `/push` (lens out), `/events` (SSE), `/recall`, `/tick` |
| `audio_pipeline.py` | VAD + amplitude gate + Groq ASR + fan-out to recall & extractor |
| `event_extractor.py` | Groq llama-3.3-70b → structured action JSON. Conservative; first-person filter |
| `actions.py` | Action registry. Real macOS Reminders, iMessage (allow-listed), sandbox calendar/notes/memories |
| `recall.py` | Name → one-line memory digest (uses inbox's services.py) |
| `vad.py` | Silero VAD speech-segment gate (local, no network) |
| `output.py` | `notify(text)` — pushes to /push for SSE → lens |
| `audit.py` | Policy gate + `mark_fired` audit trail |
| `policy.yaml` | Allow/deny rules per action |
| `imessage_send.py` | AppleScript iMessage send (used by `actions.send_imessage`) |
| `g2-renderer/` | Vite + TypeScript app loaded on G2. Subscribes to /events SSE, renders to lens |

## Data files

| File | Type | Notes |
|---|---|---|
| `contacts.json` | seed | Known people. Used for handle resolution |
| `calendar.json` | seed + state | Sandbox calendar; live-mutated by add_calendar_event |
| `state.db` | gitignored | SQLite action history + sandbox reminders/calendar/notes/memories |
| `reminders.json` | gitignored | Mirror of macOS Reminders writes |
| `notes.json` | gitignored | Sandbox notes |
| `memories.jsonl` | gitignored | Append-only durable observations |
| `actions.jsonl` | gitignored | Append-only audit of every action proposal/fire |
| `audit.log` | gitignored | Policy gate decisions |

## Action types (extractor schema)

| Action | Effect |
|---|---|
| `create_reminder` | Real macOS Reminder via AppleScript |
| `list_reminders` | Read real macOS Reminders |
| `send_imessage` | Real iMessage (allow-listed handles only) else dry-run |
| `add_calendar_event` / `list_calendar` | Sandbox `calendar.json` |
| `add_note` / `list_notes` | Sandbox `notes.json` |
| `remember_fact` / `list_memories` | Append/read `memories.jsonl` |
| `answer_question` | Synthesize one-line answer using calendar+reminders+memories |
| `send_email` | Always dry-run (no Gmail write) |

## Environment

Required env vars (loaded via `source ~/.secrets/officeqa-local.env`):

- `GROQ_API_KEY` — ASR + extractor + answer_question
- `NVIDIA_API_KEY` — alternate extractor backend (optional)
- `GOOGLE_CLOUD_API_KEY` — for maps (optional, not yet wired)
- `ANTHROPIC_API_KEY` — unused currently

Never paste keys in this repo or in code. Keys belong in `~/.secrets/`.

## Run

```bash
cd ~/projects/agihouse
set -a; source ~/.secrets/officeqa-local.env; set +a
.venv/bin/python -m uvicorn trigger_server:app --host 127.0.0.1 --port 9876

# Frontend (separate terminal)
cd g2-renderer && npm run dev

# Glasses (simulator)
evenhub-simulator -g http://localhost:5173

# Glasses (phone, same WiFi)
cd g2-renderer && ./node_modules/.bin/evenhub qr --url http://<lan-ip>:5173
```

## Adding an action (for teammates / your agent)

1. Add a function to `actions.py` returning `dict`. Call `_log(result)` and `_propose_to_lens(action, summary, fired)`.
2. Register it in `DISPATCH` at the bottom of `actions.py`.
3. Add the action name + payload shape to `event_extractor.py`'s SYSTEM prompt.
4. Add an emoji in `_emoji_for()` (most emojis render on G2; 🎙 / U+1F399 does not).
5. Restart trigger_server.

## Known issues

- **Speaker grounding** — no diarization yet. Amplitude gate + first-person filter catches most non-wearer speech but isn't reliable. Voice fingerprinting (resemblyzer) is the proper fix.
- **Gmail OAuth** — token expired; re-auth via inbox to enable Gmail/Calendar reads.
- **Lens font glyphs** — 🎙 (U+1F399) and ⏰ (U+23F0) sometimes warn; most other emojis render fine.
- **Tensor recall** — `tensor_recall.py` is active only when `demos/assistant_store.pt` has been built.


<claude-mem-context>
# Memory Context

# [agihouse] recent context, 2026-04-26 5:45pm PDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 20 obs (5,496t read) | 34,542t work | 84% savings

### Apr 26, 2026
6367 5:35p 🔵 AGIHouse — /memory/edges Confirms Confidence Boosting: 0.78 → 0.93 After Second Confirmation
6368 5:36p 🔵 AGIHouse — Audit Summary Reveals 119 Real Events with Rich Suppression Reasons and Action Breakdown
6369 " 🔵 AGIHouse — SQLite WAL Mode Files (state.db-shm, state.db-wal) Not Gitignored
6370 " ✅ AGIHouse — .gitignore Updated to Cover SQLite WAL Companion Files (state.db-*)
6371 5:38p ✅ AGIHouse — Final PR Commit e01bfb3: 2,488 Insertions Across 30 Files, All Changes Folded In
6372 " ✅ AGIHouse — PR Branch Force-Pushed: Remote Updated from aed756f to e01bfb3
6373 " 🔵 AGIHouse — /health Endpoint Returns ok=true with inbox_available=true Despite Gmail OAuth Warning
6374 " 🔵 AGIHouse — Both Servers Live and Healthy; Only AGENTS.md Has Remaining Unstaged Changes
6375 5:39p 🔵 AGIHouse — iMessage AppleScript Send Confirmed Working: "OK: sent" to +15551234567
6376 " 🔵 AGIHouse — AppleScript Messages Introspection Blocked (-10000); Direct Send Works But Service Query Fails
6377 5:40p 🟣 AGIHouse — imessage_send.py Gains SMS Fallback via AGIHOUSE_ALLOW_SMS_FALLBACK Env Var
6378 " 🟣 AGIHouse — imessage_send.py SMS Fallback Validated: Live Send Returns "sent via imessage" + 2 Tests Pass
6380 5:42p ✅ AGIHouse — actions.py: Demo Handle +15551234567 Hardcoded as Default Live iMessage Target
6381 " ✅ AGIHouse README — Documents Hardcoded Demo Handle and contacts.json Replacement Warning
6382 " ✅ AGIHouse contacts.json — Sanjay Sai Added as Demo Collaborator Contact
6383 5:43p ✅ AGIHouse — Sanjay's Handle Added to DEMO_LIVE_IMESSAGE_HANDLES Allowlist in actions.py
6384 " 🔴 AGIHouse Tests — test_send_passes_handle_and_text_as_args Fails: msg Changed from "sent" to "sent via imessage"
6385 " ✅ AGIHouse — All 21 Tests Pass After imessage_send Test Fix; Full Validation Suite Green
6386 " 🔴 AGIHouse Tests — test_imessage_send Updated for New SMS Fallback API Signature
6387 5:44p ✅ AGIHouse — All 21 Tests Pass, Full Validation Suite Green After SMS Fallback Fixes

Access 35k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>
