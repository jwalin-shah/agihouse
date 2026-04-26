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
- **TL recall** — `tl_recall.py` exists but the `assistant_store.pt` tensor isn't built. Dead path.
