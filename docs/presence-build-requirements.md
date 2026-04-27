# Presence Build Requirements

Presence is a private ambient memory companion for Even Realities G2. It listens for meaningful moments, writes linked memories, creates calendar timeline entries, and answers short questions on the glasses.

## Required For MVP

### Text Model API

Needed for:
- memory classification
- summaries
- short HUD answers

Preferred for the hackathon:

```bash
OPENROUTER_API_KEY=
OPENROUTER_SITE_URL=http://<MAC_LAN_IP>:5173
OPENROUTER_APP_NAME=Presence G2
OPENAI_TEXT_MODEL=openai/gpt-5.4-nano
```

Create the key at https://openrouter.ai/keys.

Optional direct OpenAI setup:

Environment variables:

```bash
OPENAI_API_KEY=
OPENAI_STT_MODEL=gpt-4o-mini-transcribe
```

Do not commit real keys. Store them only in local `.env`.

OpenRouter does not provide the OpenAI transcription endpoint. For no-OpenAI-credit demos, use browser speech recognition for voice capture and OpenRouter for memory reasoning.

### Google Calendar API

Needed for the memory timeline.

Use OAuth, not a service account, for the hackathon demo. We need to create events inside the user's real Google Calendar account.

Required credential values:

```bash
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8787/auth/google/callback
GOOGLE_CALENDAR_ID=
GOOGLE_MEMORY_CALENDAR_NAME=Presence Memory
```

OAuth scopes:

```text
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.readonly
```

If we want the app to create the dedicated calendar automatically, add:

```text
https://www.googleapis.com/auth/calendar
```

For quickest demo setup, manually create a calendar named `Presence Memory`, copy its calendar ID, and only request `calendar.events`.

### Local Obsidian Vault

No API key required.

Use a local folder as the canonical memory vault:

```bash
OBSIDIAN_VAULT_PATH=/Users/charvikusuma/Documents/Tarun/er_hack/vault
OBSIDIAN_VAULT_NAME=Presence
```

Calendar events should include an `obsidian://` deep link to the matching memory note:

```text
obsidian://open?vault=Presence&file=Daily/2026-04-26/2026-04-26-15
```

The markdown file is the source of truth. Google Calendar is the timeline UI.

### Even Realities G2

Needed for the final demo:
- Even Realities app on phone
- G2 paired to the phone
- Even Hub dev portal login
- Dev Mode enabled in the phone app
- local testing through QR or packaged `.ehpk`

No separate Even API key is needed for local G2 SDK usage.

## Optional But Useful

## iPhone / WebView Data Access

Even Hub apps are web apps running inside the Even Realities phone app WebView. The app can use the Even SDK bridge plus normal browser APIs, but it cannot freely read private iPhone data.

Directly available through the Even SDK:

```text
G2 microphone audio       via bridge.audioControl()
G2 IMU motion             via bridge.imuControl()
G2/R1 input events        press, double press, up, down
device info               model, serial, battery, wearing, charging, in-case
Even user profile         uid, name, avatar, country
scoped local storage      bridge.setLocalStorage/getLocalStorage
lifecycle events          foreground, background, abnormal exit
```

Available through browser/WebView APIs with permission or limits:

```text
location                  navigator.geolocation, user permission required
browser microphone        getUserMedia, if WebView allows it
language/locale           navigator.language
timezone                  Intl.DateTimeFormat().resolvedOptions().timeZone
screen size               window.innerWidth/innerHeight
network fetch             allowed only to whitelisted domains with valid CORS
local web storage         localStorage / IndexedDB inside the WebView
```

Not directly available from a WebView:

```text
Apple Health / Fitness
iPhone Calendar app data
iPhone Contacts
iMessage / SMS
call history
arbitrary files on iPhone
Obsidian vault files on iPhone
background always-on execution guarantees
raw Bluetooth access
G2 camera data, because G2 has no camera
audio output on G2
```

Ways to access unavailable data:

```text
Google Calendar/Gmail/etc.     OAuth to external APIs
Apple Health/Contacts/Calendar native iOS app with native permissions
Obsidian vault                  write files from local Mac backend, or later native/iCloud integration
fitness/activity               mock for MVP, native later
```

For the hackathon MVP, assume:

```text
phone WebView = G2 bridge + lightweight UI + optional geolocation
Mac backend   = STT, memory writer, retrieval, markdown vault
Obsidian      = opens the Mac-side vault folder
```

### Location Context

Browser location can be enough for demo if permission is granted.

Environment variables only needed if we use reverse geocoding:

```bash
GOOGLE_MAPS_API_KEY=
```

For MVP, store coarse location only:

```text
AGI House, Hillsborough
```

### Fitness / Health Context

Skip for MVP unless we build a native bridge. Apple Health / Google Fit are not easy from an Even Hub WebView app.

For demo, simulate this context:

```json
{
  "activity": "walking",
  "energy": "low",
  "focus": "building"
}
```

### Speaker / Owner Voice Detection

MVP options:

1. Simple owner gate by push-to-talk/tap confirmation.
2. Browser-side or server-side speaker embedding if time allows.
3. Use transcription diarization for speaker labels, but still require owner confirmation for private answers.

Recommended hackathon behavior:

```text
If question voice is unknown, show:
"Memory locked
Unknown voice"
```

## Memory Storage Design

Keep three layers:

```text
raw transcript windows
  -> 5-minute structured snippets
  -> hourly canonical memory note
  -> Google Calendar event
```

Directory layout:

```text
vault/
  Daily/
    2026-04-26/
      2026-04-26-15.md
      2026-04-26-16.md
  People/
    Tarun.md
    Alex.md
  Topics/
    Presence.md
    Even G2.md
  Index/
    calendar-events.json
    memory-index.json
```

Hourly memory format:

```md
---
type: memory-hour
date: 2026-04-26
hour: 15
calendar_event_id: abc123
category: work
importance: 0.82
---

# 2026-04-26 15:00

## Summary
Discussed [[Presence]], a private ambient memory companion for [[Even G2]].

## People
- [[Tarun]]
- [[AGI House]]

## Decisions
- Submit under [[Agents with Memory]]
- Use Google Calendar as the memory timeline

## Tasks
- [ ] Build STT pipeline
- [ ] Write calendar event sync

## Promises
- Send demo link before 7 PM

## Links
- [[Even G2]]
- [[Hackathon]]
```

Calendar description format:

```md
Presence memory for 2026-04-26 15:00

Summary:
Discussed Presence, a private ambient memory companion for Even G2.

People:
- [[Tarun]]
- [[AGI House]]

Decisions:
- Submit under [[Agents with Memory]]

Open in Obsidian:
obsidian://open?vault=Presence&file=Daily/2026-04-26/2026-04-26-15
```

Google Calendar will not make Obsidian-style `[[links]]` bidirectional by itself. The links become bidirectional inside Obsidian because the same markdown text exists in the vault. Calendar is for browsing the timeline; Obsidian is for the graph.

## Calendar Color Categories

Use one dedicated calendar for all memories. Use event colors for category.

Initial categories:

```text
work/meeting      blue
personal          pink
health/activity   green
idea/insight      yellow
promise/task      red
ambient/passive   gray
```

If event color APIs are slow or inconsistent, encode category in the title:

```text
[Promise] Send deck link
[Meeting] AGI House team chat
[Idea] Presence memory graph
```

## Async Agents

### Memory Writer Agent

Inputs:
- transcript chunks
- speaker labels
- timestamp
- optional location/calendar context

Responsibilities:
- detect importance
- extract tasks, promises, people, topics
- write 5-minute snippets
- roll up hourly markdown
- sync calendar event
- update retrieval index

### Ambient Buddy Agent

Inputs:
- live transcript
- current state
- latest memories
- current calendar event
- owner/unknown speaker state

Responsibilities:
- sleep when no useful signal exists
- wake on speech/question/meeting/context change
- show proactive suggestions
- answer short questions
- refuse unknown speaker memory access

HUD output rule:

```json
{
  "state": "sleeping|listening|thinking|noticing|answering|locked",
  "hud": "max 3 short lines",
  "full": "longer answer for phone/debug UI",
  "confidence": 0.0
}
```

## Implementation Order

1. Scaffold Vite + Even SDK app.
2. Add HUD state screens: sleeping, listening, thinking, noted, answer, locked.
3. Build local backend with `/transcribe`, `/memory/write`, `/memory/ask`, `/calendar/sync`.
4. Add OpenAI STT and `gpt-5.4-nano-2026-03-17` calls.
5. Write markdown vault files.
6. Add Google OAuth and calendar event creation.
7. Add memory retrieval over hourly notes.
8. Add unknown-speaker lock behavior.
9. Test in simulator and capture screenshots for demo.

## Needed From User

- OpenAI API key.
- Google Cloud OAuth client ID and secret.
- Google account to create/use the `Presence Memory` calendar.
- Confirm local Obsidian vault path and vault name.
- G2 hardware access or simulator-only fallback.
- Whether we should use real Google Calendar during demo or mock it first and connect OAuth after core flow works.
