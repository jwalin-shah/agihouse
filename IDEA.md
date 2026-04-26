# Ambient Co-Pilot — Track 1: Ambient Agents

> AGI House × Even Realities Hackathon — April 26, 2026  
> Build a proactive agent that notices before you ask.

---

## The Idea

A multi-agent ambient intelligence system that runs **proactively in the background**, watches
multiple context streams simultaneously, and surfaces exactly the right thing on the G2 HUD
at exactly the right moment — without you asking.

The core insight: **silence is the feature**. Most ambient agents fail by talking too much.
This one has a Claude-powered arbitrator that reads everything and decides when to stay quiet.

---

## The Big Realization: The Agent Runtime Already Exists

The `inbox/` directory in this repo is a fully production-built Python system that has been
running for months. It IS the agent runtime. We are not building agents from scratch.

```
inbox_server.py  — FastAPI on port 9849, already running on the laptop
├── Google Calendar     → /calendar/events  (full CRUD, OAuth tokens already set up)
├── iMessage            → /conversations    (SQLite direct read)
├── Gmail (multi-acct)  → /conversations    (OAuth, multi-account routing)
├── Apple Reminders     → /reminders        (SQLite + AppleScript)
├── GitHub notifications→ /github/          (already wired)
├── Ambient transcription → /ambient/       (MLX Whisper — LOCAL, M-series MacBook)
├── Memory store        → SQLite-backed MemoryStore (already built)
├── Scheduler           → SQLite-backed SchedulerStore + departure alerts
└── MCP server          → inbox_mcp_server.py exposes all tools to Claude
```

**The ambient audio is already running locally** via `mlx-whisper` on the MacBook's GPU — no
OpenAI API key, no latency from network round-trips. It runs as a `launchctl` daemon in the
background. The rolling transcript buffer is available at `GET /ambient/transcript`.

**What we add to the inbox server for the hackathon — three things:**
1. `WebSocket /g2/ws` — the G2 phone WebView connects here and receives HUD commands
2. `POST /g2/signal` — demo control panel calls this to inject scenario signals
3. `g2_agent_loop()` — an asyncio background task that reads existing endpoints, runs the
   arbitrator (Claude Sonnet), and pushes HUD commands to connected WebSocket clients

The G2 Vite app is then ~80 lines: connect to the WebSocket, receive `{line1, line2}`,
call `bridge.rebuildPageContainer`. That's it.

---

## The Blackboard Architecture

Multiple specialist agents run independently and in parallel. Each one writes "signals" to a
shared blackboard with a priority score and a TTL (time to live). A Claude-powered arbitrator
wakes up whenever the blackboard changes, reads all active signals, and decides what — if
anything — should surface on the HUD.

```
┌──────────────────────────────────────────────────────────────────┐
│                     BLACKBOARD  (shared state)                   │
│         { agentId, priority, category, data, ttl, timestamp }[]  │
└───────────────────────────┬──────────────────────────────────────┘
                            │  read / write
         ┌──────────────────┼───────────────────────────┐
         ▼                  ▼              ▼             ▼
   ┌──────────┐      ┌──────────┐   ┌──────────┐  ┌──────────┐
   │ Calendar │      │  Audio/  │   │  Time /  │  │  IMU /   │
   │  Agent   │      │  ASR     │   │ Pattern  │  │ Context  │
   │  (60s)   │      │ Agent    │   │  Agent   │  │  Agent   │
   └──────────┘      └──────────┘   └──────────┘  └──────────┘
         │ writes signals with priority + TTL
         ▼
   ┌──────────────────────┐
   │  Claude Arbitrator   │  ← reads ALL active signals
   │  (fires on change)   │  ← decides: show? what? how?
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │    HUD Renderer      │  → 576×288 monochrome push to G2
   └──────────────────────┘
```

---

## Where Things Actually Run

### Three physical devices, three clear roles

```
LAPTOP                            PHONE                      G2 GLASSES
──────                            ─────                      ──────────
inbox_server.py (port 9849) WiFi  G2 Vite app                HUD surface
├─ Calendar, Gmail, iMessage ───► (loads from laptop:5173)   576×288 mono
├─ MLX Whisper (LOCAL, no API)    SDK bridge ─── BLE ──────► shows output
├─ MemoryStore, SchedulerStore    receives {line1, line2}
├─ G2 WebSocket server            calls rebuildPageContainer
├─ G2 agent loop (asyncio task)   sends IMU data back
├─ Claude Sonnet (arbitrator)
Vite dev server (port 5173)
G2 Simulator (demo screen, side)
Demo control panel (port 4000)
```

### The exact connection path

```
Phone WebView ──── WiFi ────► ws://laptop-ip:9849/g2/ws  (inbox_server WebSocket)
Phone fetches app ──────────► http://laptop-ip:5173       (Vite dev server)
inbox_server reads ─────────► local SQLite DBs + Google APIs + MLX Whisper
inbox_server calls ─────────► Claude Sonnet API           (arbitration only)
```

### Why this is almost no new work

The inbox server already:
- Authenticates with Google Calendar, Gmail, Drive
- Reads iMessage SQLite, Apple Reminders, Contacts
- Runs local MLX Whisper transcription in the background (no API key or cloud cost)
- Has a MemoryStore for cross-session context
- Has a SchedulerStore for departure alerts and follow-up reminders
- Exposes all of this via clean REST endpoints

We add three things to `inbox_server.py`: a WebSocket endpoint, a signal injection endpoint,
and an asyncio agent loop task. Everything else already exists.

### Ambient transcription is already local

`mlx-whisper` runs on the MacBook's M-series GPU. The `ambient_daemon.py` is already
designed as a macOS `launchctl` service. The inbox server auto-starts it when `ambient_autostart`
is true in voice config. The transcript rolls in `GET /ambient/transcript`. There is no OpenAI
Whisper API call needed — the laptop IS the ASR engine.

---

## What the G2 + SDK Can Actually See

### Native SDK signals (always available, no external APIs needed)

| Signal | Source | What you get | How |
|---|---|---|---|
| **Microphone audio** | G2 mic | Raw PCM, 16 kHz, 16-bit mono | `bridge.audioControl(true)` → `audioEvent.audioPcm` (Uint8Array) |
| **IMU / head motion** | G2 IMU | Accelerometer x/y/z | `bridge.imuControl(true, ImuReportPace.P500)` → `sysEvent.imuData` |
| **Wearing detection** | G2 + phone | Boolean: is user wearing glasses right now | `bridge.onDeviceStatusChanged` → `status.isWearing` |
| **Battery level** | G2 | 0–100% | `bridge.onDeviceStatusChanged` → `status.batteryLevel` |
| **Is charging** | G2 | Boolean | `bridge.onDeviceStatusChanged` → `status.isCharging` |
| **Is in case** | G2 | Boolean | `bridge.onDeviceStatusChanged` → `status.isInCase` |
| **User identity** | Even account | uid, display name, avatar URL, country | `bridge.getUserInfo()` |
| **Input gestures** | Temple touchpad / R1 ring | Single tap, double tap, swipe up, swipe down | `bridge.onEvenHubEvent` → `sysEvent` / `listEvent` / `textEvent` |
| **App lifecycle** | Host app | Foreground enter, foreground exit, abnormal exit | `sysEvent.eventType` 4 / 5 / 6 |
| **Launch source** | Host app | Did user open from app menu or glasses menu | `bridge.onLaunchSource` → `'appMenu'` or `'glassesMenu'` |
| **Local storage** | Even Hub app | Key-value string persistence across restarts | `bridge.setLocalStorage` / `bridge.getLocalStorage` |
| **Background state** | SDK | State snapshot across phone background/foreground | `setBackgroundState` / `onBackgroundRestore` |

### Via `app.json` permissions (phone sensors, declared up-front)

| Permission | What it unlocks | Useful for |
|---|---|---|
| `location` | Phone GPS — lat/lng, speed, heading | Location-triggered agents, geofencing |
| `g2-microphone` | G2's built-in mic (hardware) | Audio capture natively on glasses |
| `phone-microphone` | Phone's mic (separate from G2) | Fallback audio or dual-mic capture |
| `album` | Access to phone photo library | Image display in image containers |
| `camera` | Phone camera (G2 has no camera) | Phone-side vision features |
| `network` | External HTTP calls from WebView | Any API call |

### Via external APIs (called from laptop backend or WebView fetch)

| Signal | API | What you get |
|---|---|---|
| **Calendar events** | Google Calendar API | Upcoming meetings, attendees, location, last talking points |
| **Speech → text** | OpenAI Whisper | Full transcription of ambient audio |
| **Insight extraction** | Claude Haiku (fast + cheap) | Action items, names, topics mentioned in conversation |
| **Arbitration** | Claude Sonnet | Reads blackboard, decides what to show and when |
| **Weather** | Open-Meteo (free, no key) | Current conditions, UV index, precipitation |
| **Traffic / ETA** | Google Maps API | Commute time, departure nudge |
| **News / topics** | Any RSS or news API | Briefing before meetings on relevant topics |
| **Smart home** | Home Assistant / IFTTT | State of home devices |

### What the SDK explicitly does NOT expose

These don't exist — don't try to build around them:

- No camera on G2 (confirmed in PDF and SDK)
- No audio output / speaker on G2
- No direct Bluetooth access from WebView
- No CSS, no DOM, no flexbox — HUD is absolute-positioned pixel containers only
- No font size control, no bold/italic
- No text alignment
- No background colors
- No z-index — declaration order determines overlap
- No arbitrary pixel drawing (except via `updateImageRawData` into image containers)

---

## The Agents — What Each One Watches

Each agent is a poller inside the `g2_agent_loop` asyncio task in `inbox_server.py`.
They call existing server state / service functions — no new data connectors to build.

### Agent 1: Calendar Agent
**Source**: `calendar_events(state.cal_services)` — already authenticated, already working  
**Trigger**: ≤10 minutes until next event  
**Signal**: title, attendees (resolved via existing ContactBook), location, minutes away  
**Priority**: scales 6→9 as minutes drop; 9 at 2 min = always surfaces  
**Bonus**: the inbox server already runs `_process_departure_alerts()` — there's a scheduler
loop that checks calendar and can fire reminders. The G2 agent loop can read from the same
calendar functions.

### Agent 2: Ambient Transcript Agent
**Source**: `state.ambient.get_transcript(max_segments=10)` — rolling MLX Whisper buffer  
**Trigger**: new transcript segment contains an action-item keyword or name  
**Signal**: insight extracted by Claude Haiku from the raw transcript text  
**Priority**: Claude Haiku assigns 1–5 based on relevance  
**Key fact**: the ambient service is already running on the laptop mic. The G2 mic is a
bonus second source — but the laptop ambient already captures everything in the room.

### Agent 3: iMessage / Gmail Urgency Agent
**Source**: `GET /conversations` — already reading iMessage SQLite and Gmail  
**Trigger**: unread message from a VIP contact, or message containing urgent keywords  
**Signal**: "msg from [name]" — 28 chars max for HUD  
**Priority**: VIP contacts get 7, regular unread get 2  
**Key fact**: ContactBook is already loaded at server startup. VIP list is a config file.

### Agent 4: Reminders / Tasks Agent
**Source**: `reminders_list()` — reads Apple Reminders SQLite directly  
**Trigger**: reminder due within 30 minutes  
**Signal**: reminder title, due time  
**Priority**: 6 for due soon, 8 for overdue  
**Key fact**: the scheduler already handles Google Tasks follow-ups. Apple Reminders are
read via the same SQLite path that's been working for months.

### Agent 5: GitHub Notifications Agent
**Source**: `github_notifications()` — existing httpx call to api.github.com  
**Trigger**: new PR review request or CI failure  
**Signal**: repo name + type (PR, CI, mention)  
**Priority**: review request = 5, CI fail = 7, other = 2  
**Easily suppressed**: work hours only, or only when no meeting is imminent

### Agent 6: IMU / Wearing Agent (from G2 hardware)
**Source**: G2 SDK — `bridge.onDeviceStatusChanged` + `bridge.imuControl`  
**Trigger**: glasses just put on, or head comes back up after phone-down period  
**Signal**: "glasses on" → trigger a morning/context brief from all other agents  
**This is the only agent that requires G2 hardware specifically**  
All others run entirely on the laptop reading existing data sources.

---

## The Arbitrator — The Intelligence Layer

Runs on every blackboard update (debounced 2s to avoid rapid-fire calls).

Reads all active signals, serializes them with priority + TTL remaining + category, and sends
to Claude Sonnet with a system prompt that enforces:

- **Silence preference**: only surface if something is genuinely time-sensitive
- **Recency bias**: prefer newer signals over stale ones
- **Single output**: only one thing on the HUD at a time
- **Glanceability**: output capped at 28 chars per line, 2 lines max
- **No repeats**: already-shown signals are marked and deprioritized

The arbitrator returns: `{ show: boolean, line1: string, line2: string }`.

If `show = false`, nothing changes on the HUD. This is the correct answer most of the time.

---

## HUD Design

The G2 display is 576×288 monochrome (green on black). Users glance for under 2 seconds.

### Layout for ambient signals

```
┌─────────────────────────────────────────────────────────┐  y=0
│  ● MEETING IN 3 MIN                                     │  y=20   (headline, bright)
│  Sarah Chen + 2 others                                  │  y=80   (detail, dim border)
│                                                         │
│                                                         │
└─────────────────────────────────────────────────────────┘  y=288
```

Rules:
- Top line: category icon (Unicode) + headline. Max 28 chars.
- Bottom line: detail. Max 28 chars.
- Auto-dismiss after 8 seconds (SDK `time` field)
- No persistent UI between signals — HUD is blank when nothing is urgent

### Unicode signal icons (fits within firmware font set)

| Category | Icon | Example |
|---|---|---|
| Meeting | `●` | `● MEETING IN 3 MIN` |
| Insight | `◆` | `◆ Action: send deck` |
| Reminder | `▶` | `▶ Standup window` |
| Motion | `△` | `△ Head up — 2 msgs` |
| Weather | `○` | `○ Rain in 20 min` |

---

## Demo Strategy

Per the PDF, the HUD can't be projected. The solution is two screens on the laptop side by side.

### Two-screen layout on the laptop

```
┌────────────────────────────────┐  ┌──────────────────────────────────────┐
│   SCREEN LEFT                  │  │   SCREEN RIGHT                       │
│   Demo Control Panel           │  │   G2 Simulator                       │
│   (browser tab, localhost:4000)│  │   (evenhub-simulator process)        │
│                                │  │                                      │
│  ┌──────────┐  ┌──────────┐   │  │  ┌────────────────────────────────┐  │
│  │📅 Meeting │  │🎙 Action  │   │  │  │                                │  │
│  │  in 5min │  │  Item    │   │  │  │  ● MEETING IN 5 MIN             │  │
│  └──────────┘  └──────────┘   │  │  │  Sarah Chen + 2 others         │  │
│  ┌──────────┐  ┌──────────┐   │  │  │                                │  │
│  │🚶 Walking │  │📍 Arrived │   │  │  │                                │  │
│  │  to work │  │  at HQ   │   │  │  └────────────────────────────────┘  │
│  └──────────┘  └──────────┘   │  │         G2 576×288 mono display      │
│  ┌──────────┐  ┌──────────┐   │  │                                      │
│  │🌅 Morning │  │  ▶ PLAY  │   │  │                                      │
│  │  Startup │  │ Timeline │   │  │                                      │
│  └──────────┘  └──────────┘   │  │                                      │
│                                │  │                                      │
│  BLACKBOARD LIVE               │  │                                      │
│  ● [calendar] p=9 "Mtg 5min"  │  │                                      │
│  ◆ [audio]    p=3 "send deck" │  │                                      │
│  → arbitrator: showing cal    │  │                                      │
└────────────────────────────────┘  └──────────────────────────────────────┘
         judges see both screens simultaneously
```

### What the control panel contains

**Scenario buttons** — each injects a realistic pre-defined signal sequence into the blackboard.
The arbitrator receives them exactly as if a real agent fired. It doesn't know the difference.

| Button | What it injects | Expected HUD outcome |
|---|---|---|
| `📅 Meeting in 5 min` | Calendar signal, priority 8, attendees + title | Meeting brief appears |
| `📅 Meeting in 2 min` | Same, priority 9 (urgency bump) | Brief reappears, more urgent tone |
| `🎙 Action Item` | Audio insight: "send the deck before standup" | Insight surfaces |
| `🚶 Walking to work` | Location + IMU: movement detected, no destination yet | HUD stays silent — correct |
| `📍 Arrived at HQ` | Geofence trigger: entered office boundary | Work mode brief |
| `🌅 Morning startup` | Wearing detection + calendar pull + weather | Day briefing |
| `🤫 Noise only` | Low-priority audio signal, no real insight | Arbitrator stays silent — show this |
| `▶ Play Timeline` | Auto-plays a 60s scripted sequence (see below) | Full arc, hands-free |
| `⬛ Reset` | Clears blackboard, blanks HUD | Clean slate |

**Live blackboard panel** — below the buttons, a real-time feed showing:
- Every active signal (agent, priority, category, TTL countdown)
- Arbitrator's last decision and reasoning
- Whether the HUD was updated or stayed silent

This is the "brain visible" panel — judges can see the system thinking.

### The 60-second scripted timeline (`▶ Play Timeline`)

One button. Runs automatically. You narrate over it.

```
t=0s   Glasses put on → wearing detection fires
t=3s   Morning context loads → calendar has 2 events today
t=6s   HUD: "● STANDUP 9AM / Sarah, Jake, Priya"      (8 min away)
t=12s  Low-priority noise → arbitrator stays silent      ← call this out
t=18s  Walking detected → IMU sees motion, no trigger   ← silence again
t=25s  Meeting closes in → priority jumps to 9
t=28s  HUD: "● NOW  Standup / Room 2B"
t=34s  Mic hears "send the deck" → Whisper → Claude
t=38s  HUD: "◆ ACTION: Send deck / before standup"
t=44s  Two signals compete → arbitrator picks higher one ← the key moment
t=50s  All signals expire → HUD goes blank
t=58s  EOD nudge fires → HUD: "▶ Wrap up / open items?"
t=62s  Done
```

### The "silence wins" moment — the most important demo beat

At `t=44s`, inject two signals simultaneously via the control panel (or auto-timeline):
- Calendar: meeting in 1 min (priority 9)
- Audio insight: someone said something mildly interesting (priority 3)

The live blackboard panel shows both signals. The arbitrator picks the calendar one.
The low-priority signal gets logged as "suppressed." The HUD shows only the meeting.

This is the moment that wins Track 1. The judging criterion is "judgment under ambiguity —
knowing when to speak, when to stay silent." A system that shows everything is not ambient.
A system that chooses is.

### Handing the glasses to a judge

When a judge puts the glasses on, you control what they see via the control panel.
- They put them on → press `🌅 Morning startup` → they see a context brief immediately
- Ask them to tap the temple → press `📅 Meeting in 2 min` just before they tap
- They see the HUD update as if the system just noticed something

They experience it as magic. You're just clicking a button they can't see.

### Why injecting fake signals is fine to admit

Every real ambient agent system has a test harness. The pipeline — mic → Whisper → Claude →
blackboard → arbitrator → HUD — is fully real and running. You're just controlling the timing
of inputs, not faking the processing. The arbitrator genuinely doesn't know the signal came
from a button press. That's the point — the architecture is clean enough that simulation
is indistinguishable from reality.

---

## Tech Stack

| Layer | Choice | Why / Status |
|---|---|---|
| **Agent runtime** | `inbox_server.py` (FastAPI, port 9849) | Already built, already running |
| **ASR** | `mlx-whisper` (local, M-series GPU) | Already running as ambient daemon, NO API key |
| **Calendar** | `calendar_events()` in `services.py` | Already OAuth'd, already working |
| **Messages** | `imsg_contacts()` + Gmail services | Already working, multi-account |
| **Reminders** | `reminders_list()` SQLite direct | Already working |
| **GitHub** | `github_notifications()` httpx | Already working |
| **Memory** | `MemoryStore` SQLite | Already built |
| **G2 WebSocket** | FastAPI WebSocket in inbox_server | NEW — ~30 lines to add |
| **G2 agent loop** | asyncio background task | NEW — ~100 lines to add |
| **Insight extraction** | Claude Haiku (`claude-haiku-4-5-20251001`) | NEW — cheap, fast per-transcript call |
| **Arbitration** | Claude Sonnet (`claude-sonnet-4-6`) | NEW — reads all signals, decides HUD |
| **G2 Vite app** | Vite + TypeScript + Even Hub SDK | NEW — ~80 lines, just WebSocket + HUD push |
| **Demo control panel** | Plain HTML + fetch (localhost:4000) | NEW — zero deps, big buttons |
| **Dev tunnel** | ngrok | Fallback if hackathon WiFi is flaky |
| **Simulator** | `@evenrealities/evenhub-simulator` | Already in repo deps |

---

## File Structure

```
inbox/                          ← EXISTING — the agent runtime (don't rebuild this)
├── inbox_server.py             ← FastAPI port 9849, ADD g2 routes here
├── services.py                 ← all data connectors (calendar, imsg, whisper, etc.)
├── ambient_daemon.py           ← local MLX Whisper, already running
├── memory_store.py             ← SQLite memory, already built
├── scheduler.py                ← departure alerts, follow-ups, already built
└── g2_agent.py                 ← NEW: asyncio agent loop + blackboard + arbitrator
                                   (add as import to inbox_server.py lifespan)

g2-app/                         ← NEW: the G2 Vite app (~80 lines total)
├── src/
│   ├── main.ts                 ← waitForBridge → connect WebSocket → receive HUD commands
│   └── hud.ts                  ← rebuildPageContainer wrapper
├── app.json
└── package.json

demo/                           ← NEW: demo control panel (open in browser, localhost:4000)
├── index.html                  ← big scenario buttons + live blackboard panel
└── scenarios.json              ← pre-defined signal payloads for each button
```

**Total new code to write:**
- `g2_agent.py` — ~200 lines (blackboard dataclass, 6 agent pollers, arbitrator, WebSocket manager)
- `g2-app/src/main.ts` — ~80 lines (WebSocket + SDK bridge + HUD push)
- `demo/index.html` — ~150 lines (HTML + fetch calls, no framework)
- Additions to `inbox_server.py` — ~30 lines (WebSocket route + lifespan hook)
```

---

## Things Still Missing From The Plan

### 1. The inbox server already has AI endpoints — use them

Reading the `inbox/` code reveals it already has AI endpoints we can call directly instead of
rebuilding the logic:

| Endpoint | What it does | Use in G2 agent |
|---|---|---|
| `POST /ai/briefing` | Calendar + reminders + unread + local LLM summary | Call when glasses put on → morning brief on HUD |
| `POST /ai/extract-actions` | `{text}` → action items from conversation text | Feed recent transcript → extract what needs doing |
| `POST /ai/triage` | Score conversations urgent / normal / low | Messages agent uses this to gate what surfaces |
| `GET /ambient/transcript` | Rolling MLX Whisper buffer | Ambient agent reads this, no audio piping needed |

The local Qwen model (0.8B / 3B on MLX) already does action extraction and triage. Claude
Sonnet only needs to handle the final arbitration — "of all signals, what single thing to show
right now." This keeps Claude calls to one every 10–15s, not per-transcript.

---

### 2. CORS — the silent hackathon killer

The G2 WebView is a real browser (Chromium / WKWebView). Full CORS enforcement applies.
The inbox server running on the laptop needs to explicitly allow WebSocket connections from
the WebView's origin. Without this, the WebSocket silently fails and the G2 app shows nothing.

Fix: add `fastapi-cors` middleware to `inbox_server.py` allowing all origins for the `/g2/ws`
path. One import, three lines. But if you forget it, you'll spend an hour debugging.

Also: the G2 `app.json` needs a `network` permission with a whitelist:
```json
{ "name": "network", "desc": "Connects to local agent server", "whitelist": ["ws://your-ngrok-url"] }
```

The laptop IP changes per WiFi. Use ngrok from the start so the URL is stable across WiFi
changes. Bake the ngrok URL into the G2 app before the hackathon starts, not during.

---

### 3. Prompt caching on the arbitrator

The arbitrator calls Claude Sonnet every 10–15 seconds. Without caching, every call pays full
price for the system prompt. The system prompt is large (user context, blackboard format,
output rules, HUD constraints) — and it never changes within a session.

With Anthropic prompt caching, add `"cache_control": {"type": "ephemeral"}` to the system
prompt block. The cache TTL is 5 minutes. As long as the arbitrator fires within 5 minutes of
the last call (it will — it's every 10s), the system prompt is served from cache at ~10% of
the normal token cost.

This is the difference between "sustainable for an 8-hour hackathon" and a surprise API bill.

---

### 4. Claude as the agent loop — tool use pattern

Instead of polling loops writing to a blackboard, an alternative architecture:

One asyncio task runs every 30s. It creates a Claude API call with **tool use**, where the
tools are the inbox server endpoints: `get_calendar_events`, `get_recent_transcript`,
`get_unread_messages`, `get_due_reminders`. Claude actively decides which tools to call based
on context (time of day, what it last showed, what it knows about the user's schedule).

```
Claude receives: {time: "8:54am", last_shown: "weather brief 20min ago", tools: [...]}
Claude calls:    get_calendar_events()  → standup in 6 min
Claude calls:    get_recent_transcript() → nothing notable
Claude decides:  show calendar signal
```

This is genuinely more agentic — Claude is the agent, not just the arbiter. It decides what
to look at, not just what to prioritize from a pre-collected list. More impressive to judges
who know how agents work. Slightly slower (multi-turn round trip), but 30s cadence hides that.

Pick one: **polling blackboard** (faster to build, more reliable) or **tool use loop** (more
impressive, slightly more complex). For 8 hours, polling blackboard is the safe bet. If you
have 2 hours spare, swap the arbitrator for the tool use pattern — same G2 output, better story.

---

### 5. IMU as an attention gate — the killer feature

The IMU gives accelerometer x/y/z. Right now it's listed as just "motion detection." But the
real use case is an **attention state machine**:

```
HEAD STILL (low variance over 5s)  →  FOCUSED MODE  →  suppress all signals except priority ≥ 9
HEAD MOVING                        →  AMBIENT MODE   →  normal signal flow
HEAD DOWN THEN UP                  →  TRANSITION     →  flush any queued signals immediately
```

This makes the system feel like it understands what you're doing, not just what your calendar
says. A user deep in focused writing sees nothing. The same user looks up, and the queued
"meeting in 2 min" surfaces immediately.

No other team will think of this. It directly answers the judging criterion: "knowing when to
stay silent." The IMU is the physical signal that answers that question — not just logic.

Implementation: the G2 app measures rolling variance of IMU z-axis (head nod axis) over a
5-second window. Sends `{attentionState: "focused" | "ambient"}` to the inbox server WebSocket.
The arbitrator checks this before deciding to push anything to the HUD.

---

### 6. `textContainerUpgrade` for countdowns

When a meeting is approaching, the signal updates every minute:
`MEETING IN 8 MIN` → `MEETING IN 7 MIN` → ... → `MEETING IN 1 MIN` → `NOW`

Using `rebuildPageContainer` for each update causes a full redraw flicker. Use
`bridge.textContainerUpgrade` instead — in-place text update, no flicker, max 2000 chars.
This is the difference between a demo that looks like a prototype and one that looks like a product.

Only use `rebuildPageContainer` for a brand new signal type. Use `textContainerUpgrade` for
updating an existing one.

---

### 7. Background state — WebSocket reconnection

When the phone goes to background (user locks screen, switches apps), the WebSocket connection
drops. On foreground return, the G2 app needs to reconnect and re-render the last known HUD state.

The `background-state` skill handles this for app-internal state. But the WebSocket itself
needs explicit reconnect logic on `FOREGROUND_ENTER_EVENT` (event type 4). The inbox server
should also track the last HUD push so it can replay it on reconnect.

Without this, putting your phone in your pocket and taking it out resets the HUD to blank —
embarrassing during the demo when you hand the glasses to a judge.

---

### 8. Use the `--asr` template as the starting point

Don't start from `/quickstart`. Start from `/template --asr`. It already has:
- Mic capture wired (`bridge.audioControl(true)` + PCM event handling)
- Double-tap exit pattern
- Companion phone UI stub

Then strip out the STT stub and replace with WebSocket connection to the inbox server. This
saves ~30 minutes of plumbing. The G2 app doesn't need to do its own ASR — the laptop ambient
daemon is already transcribing. The G2 app just needs to stream IMU + wearing state up, and
receive HUD commands down.

---

### 9. Build order for the 8-hour hackathon

Do not build everything at once. Build in this order — each step has a working demo:

| Hour | What to build | Proof it works |
|---|---|---|
| 0:00–0:30 | Start inbox server, run ngrok, scaffold G2 app from `--asr` template | Phone scans QR, simulator shows "Connected" |
| 0:30–1:00 | Add `/g2/ws` WebSocket to inbox_server + basic HUD push | Demo panel button updates simulator |
| 1:00–1:30 | Demo control panel with 4 scenario buttons | Buttons change the simulator live |
| 1:30–2:15 | Calendar agent → blackboard → arbitrator (no Claude yet, rule-based) | Meeting signal appears on HUD automatically |
| 2:15–2:45 | `POST /ai/briefing` on wearing detection → morning brief HUD | Put glasses on → brief appears |
| 2:45–3:30 | Transcript agent reads `/ambient/transcript` → Claude Haiku extracts insights | Say something → insight surfaces |
| 3:30–4:15 | Claude Sonnet arbitrator with prompt caching | Two signals compete → right one wins |
| 4:15–5:00 | IMU attention gate — focused mode suppresses signals | Head still → silence; head up → signal |
| 5:00–5:30 | Messages + reminders agents + `textContainerUpgrade` countdown | Meeting countdown works without flicker |
| 5:30–6:30 | Full demo timeline script (▶ Play button) | 60s auto-run works end to end |
| 6:30–8:00 | Buffer: bugs, polish, slides, passing the glasses around | Demo-ready |

Stop adding features at hour 5. Hours 6–8 are for hardening and the story.

---

### 10. The cross-track "Best G2 Integration" prize

There's a $1,000 cross-track prize for the best use of G2 hardware — separate from Track 1.
This build is a strong candidate because it uses:
- `g2-microphone` permission (audio capture)
- IMU (`imuControl`) for attention gate
- Wearing detection (`onDeviceStatusChanged`)
- Custom HUD layouts with Unicode icons
- `textContainerUpgrade` for smooth countdown
- `setBackgroundState` for WebSocket reconnection

Most teams will use the HUD and maybe the mic. Using IMU + wearing detection + background
state + the update API puts this build in a different class for the integration prize.
Explicitly mention all of these in the submission form.

---

### 11. The 3-sentence pitch

> "Most AI agents have to be asked. This one watches — your calendar, your conversations,
> what's being said around you — and decides when to speak. The hard part isn't surfacing
> information. It's knowing when to stay silent."

Everything else is a demo of that claim. The silence moments are the pitch.

---

## Judging Criteria Mapped

| Criterion | How this build addresses it |
|---|---|
| Technical complexity — real ambient signals | 5 parallel asyncio agents on real data: calendar, ambient transcript, messages, reminders, IMU |
| Execution quality — stability, responsiveness | Agents on laptop = no WebView limits; WebSocket latency; local MLX Whisper = no API round-trip |
| Leverage of Even G2 form factor | IMU attention gate, wearing detection, mic — none of this works on a phone |

The key differentiator: **the arbitrator choosing silence** is demonstrable. Show judges three
signals firing simultaneously and Claude saying "not yet" — then one becomes urgent and it
surfaces. That's the "felt, not demonstrated" criterion.
