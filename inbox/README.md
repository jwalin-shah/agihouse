# Inbox — Unified Communication & Productivity TUI

A privacy-first terminal UI that consolidates iMessage, Gmail, Google Calendar, Apple Notes, Apple Reminders, GitHub notifications, and Google Drive into a single keyboard-driven interface.

## Features

**Multi-source unified inbox:**
- **iMessage** — Direct access to SMS/iChat conversations with contact resolution
- **Gmail** — Full inbox, thread support, unsubscribe utilities
- **Google Calendar** — Event viewing and quick event creation ("Meeting 2pm-3pm @ Office")
- **Apple Notes** — Direct SQLite access to on-device notes
- **Apple Reminders** — Multi-account reminders (iCloud, local) with completion tracking
- **GitHub** — Notifications, PR review requests, notification management
- **Google Drive** — File search, upload, folder creation, multi-account support

**Productivity:**
- **Ambient listening** — Background audio capture with real-time transcription (MLX Whisper) → auto-extraction → Obsidian vault integration
- **Dictation mode** — Streaming speech-to-text with keyboard injection (low-latency C++ binary)
- **AI autocomplete** — Constrained generation (Outlines + MLX) for composing messages
- **Optimistic UI** — Messages appear instantly, confirmed in background

**Architecture:**
- **Client-server** — FastAPI backend handles all data access; TUI and agents are thin HTTP clients
- **Local-first ML** — Qwen 3.5 0.8B (MLX 4-bit), MLX Whisper, no cloud dependencies
- **Tab-based navigation** — Preserve selection context when switching tabs (Textual TUI)
- **Multi-account Gmail** — Route sends through correct account OAuth token

## Quick Start

**Requirements:**
- Python 3.10+
- macOS (iMessage, Notes, Reminders, Dictation use platform APIs)
- uv package manager

**Install & run:**
```bash
git clone https://github.com/jwalin-shah/inbox
cd inbox
uv run python inbox.py          # Starts server + TUI automatically
```

**Server-only (for agent access):**
```bash
uv run python inbox_server.py   # Runs on localhost:9849
```

## API Reference

All endpoints available at `localhost:9849`. Optional token-based auth via `INBOX_SERVER_TOKEN` env var.

**Conversations & Messages:**
```
GET  /conversations?source=all|imessage|gmail&limit=50
GET  /messages/{source}/{conv_id}?thread_id=...
POST /messages/send  {"conv_id", "source", "text"}
POST /messages/gmail/{msg_id}/unsubscribe
POST /messages/gmail/bulk-unsubscribe  {"msg_ids": [str]}
```

**Calendar:**
```
GET  /calendar/events?date=YYYY-MM-DD
POST /calendar/events  {"summary", "start", "end", "attendees", ...}
POST /calendar/events/quick  {"text": "Meeting 2pm-3pm @ Office"}
PUT  /calendar/events/{id}
DELETE /calendar/events/{id}
```

**Notes & Reminders:**
```
GET  /notes?limit=50
GET  /notes/{id}
GET  /reminders?list_name=...&limit=100
POST /reminders  {"title", "list_name", "due_date"}
POST /reminders/{id}/complete
```

**GitHub:**
```
GET  /github/notifications?all=false
POST /github/notifications/{id}/read
POST /github/notifications/read-all
GET  /github/pulls?repo=owner/name
```

**Google Drive:**
```
GET  /drive/files?q=...&limit=20&account=...
POST /drive/upload  (multipart: file + folder_id + account)
DELETE /drive/files/{id}?account=...
```

**Ambient & Dictation:**
```
POST /ambient/start
POST /ambient/stop
GET  /ambient/status
POST /dictation/start
POST /dictation/stop
POST /autocomplete  {"draft", "messages", "max_tokens", "temperature"}
```

## Key Bindings (TUI)

| Key | Action |
|---|---|
| **Ctrl+1-5** | Switch tabs: All, iMessage, Gmail, Calendar, Notes |
| **Ctrl+6** | Toggle ambient listening |
| **Ctrl+7** | GitHub tab |
| **Ctrl+R** | Refresh all data |
| **Ctrl+N** | New calendar event |
| **Ctrl+A** | Add Google account (opens browser) |
| **Tab** | Accept AI autocomplete |
| **r** (GitHub) | Mark notification as read |
| **Shift+R** (GitHub) | Mark all as read |
| **o** (GitHub) | Open URL in browser |
| **Ctrl+Q** | Quit |

## Architecture

```
services.py          — Data layer (Gmail, Calendar, Drive, iMessage, Notes, Reminders, GitHub)
inbox_server.py      — FastAPI wrapper (port 9849)
inbox_client.py      — HTTP client library
inbox.py             — Textual TUI with tab state preservation
contacts.py          — macOS AddressBook SQLite queries
ambient_notes.py     — Obsidian vault writer
ambient_daemon.py    — Background audio capture + ASR + extraction
```

**Data sources:**
- iMessage: `~/Library/Messages/chat.db` (read-only SQLite)
- Notes: `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`
- Reminders: `~/Library/Group Containers/group.com.apple.reminders/Container_v1/Stores/Data-*.sqlite`
- AddressBook: `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`
- Gmail/Calendar/Drive: Google OAuth
- GitHub: Personal access token

## Development

```bash
uv run ruff check --fix .   # Lint
uv run pyright              # Type check
uv run pytest               # Tests (stubs ML/macOS deps)
```

## Design Decisions

- **Client-server split** — Enables both TUI and agents (e.g., Claude Code) to access data
- **Server-side conversation cache** — Eliminates redundant DB queries
- **Optimistic sends** — Messages appear instantly, confirmed asynchronously
- **Local ML stack** — No API calls for ASR or LLM, full privacy
- **Tab context preservation** — Returning to a tab shows the same conversation/event selection
- **Multi-account Gmail routing** — Each account has its own OAuth token, sends route correctly

## Privacy

All data processing happens on-device. No telemetry, no cloud syncing. Credentials stored locally:
- `credentials.json` — Google OAuth client secret (never committed)
- `tokens/` — Per-account Google OAuth tokens (auto-managed)
- `github_token.txt` — GitHub personal access token (never committed)

## License

MIT
