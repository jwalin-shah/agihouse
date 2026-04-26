# Inbox — Project Context

## What this is
A terminal TUI (Python + Textual + Rich) that unifies iMessage, Gmail, Google Calendar, Google Sheets, and Apple Notes into one inbox. Client-server architecture: a local FastAPI server handles all data access, the TUI is a thin HTTP client. Agents can also hit the server API directly.

## Run it
```bash
cd ~/projects/inbox
uv run python inbox.py          # starts server automatically + TUI
uv run python inbox_server.py   # server only (for agent access)
```

Server port is configurable via `INBOX_SERVER_PORT` (default 9849). Client honors `INBOX_SERVER_URL` (default `http://127.0.0.1:$INBOX_SERVER_PORT`). Setting either lets a second copy run alongside the primary instance — see Worktree dev workflow.

## Worktree dev workflow
Primary checkout `~/projects/inbox` (port 9849) is the daily-driver inbox — keep it running. All in-progress development happens in a separate git worktree on its own branch and port so the primary never gets disrupted.

Layout:
```
~/projects/inbox/                         — main branch, port 9849, daily driver
~/projects/inbox-<topic>/                 — feature-branch worktree, port 9850+, active dev
~/projects/inbox/.claude/worktrees/dev/   — worktree-dev branch (legacy, for agent runs)
```

Create a new dev worktree:
```bash
cd ~/projects/inbox
git worktree add ~/projects/inbox-<topic> -b feat/<topic> main
```

Run the dev copy on an alt port (won't collide with 9849):
```bash
cd ~/projects/inbox-dev
./dev.sh                  # TUI+server on 9850 (sets INBOX_SERVER_PORT/URL)
./dev.sh inbox_server.py  # server only
```

`dev.sh` defaults to `INBOX_SERVER_PORT=9850` and `INBOX_SERVER_URL=http://127.0.0.1:9850`. Override per worktree if needed:

```bash
INBOX_SERVER_PORT=9851 ./dev.sh
INBOX_SERVER_PORT=9851 ./dev.sh inbox_server.py
```

If the server URL is not overridden explicitly, `dev.sh` derives `INBOX_SERVER_URL` from `INBOX_SERVER_PORT`.

Tear down when merged:
```bash
git worktree remove ~/projects/inbox-<topic>
git branch -d feat/<topic>
```

Caveats:
- macOS data-source paths (iMessage, Notes, Reminders, AddressBook) are shared across worktrees — both copies read the same SQLite DBs. Avoid concurrent mutations from both UIs.
- Google token files in `tokens/` live per-checkout (gitignored). The dev worktree starts with no tokens — re-auth via `Ctrl+A` or copy `tokens/` over from primary if you want shared accounts.
- MCP backend (`mcp_backend.py`) defaults to 9849. To point an agent at the dev server, set `INBOX_SERVER_URL=http://127.0.0.1:9850` in its env.

## Primary vs Dev MCP routing

Primary should keep using `http://127.0.0.1:9849`.

When testing a dev worktree, point the assistant client at the dev backend instead:

```json
{
  "env": {
    "INBOX_SERVER_URL": "http://127.0.0.1:9850",
    "INBOX_SERVER_TOKEN": "${INBOX_SERVER_TOKEN}"
  }
}
```

Rules:
- never reuse a primary MCP config when you intend to test dev
- if an agent is supposed to exercise dev, verify both `cwd` and `INBOX_SERVER_URL`
- if requests appear to "work" but reflect the wrong data, assume the client is still pointed at 9849 first

## Architecture
```
services.py       — data access layer (iMessage, Gmail, Calendar, Sheets, Docs, Notes, Reminders, GitHub, Drive, auth, LLM, audio)
inbox_server.py   — FastAPI server wrapping services.py (port 9849)
inbox_client.py   — sync HTTP client for the server API
mcp_backend.py    — MCP server backend for Claude integration
mcp_server.py     — MCP server (stdio-based protocol)
inbox.py          — Textual TUI (thin client, auto-starts server)
scheduler.py      — background scheduler for recurring tasks (reminders, email cleanup, etc.)
contacts.py       — reads macOS AddressBook SQLite DBs, resolves phone→name
ambient_notes.py  — writes ambient captures to Obsidian vault (~/vault/daily/)
ambient_daemon.py — background daemon for audio capture → ASR → extraction → notes
memory_store.py   — persistent memory storage for conversations and state
tokens/           — per-account Google OAuth tokens (auto-created)
credentials.json  — Google OAuth client secret (never commit)
github_token.txt  — GitHub personal access token (never commit)
```

## Utility scripts
```
unsubscribe_interactive.py     — interactive unsubscribe helper (single email)
unsubscribe_bulk.py            — batch unsubscribe from list of message IDs
unsubscribe_all_newsletters.py — unsubscribe from all newsletters in inbox
organize_inbox.py              — tag emails by category (tag-only, no archiving)
organize-inbox-helper.sh       — wrapper: auto-starts server, then organizes inbox
oci_retry.sh                   — retry utility for OCI operations
```

**Email organization workflow**:
```bash
./organize-inbox-helper.sh
# or directly: uv run python organize_inbox.py
```
- Tags emails by category (Finance, Jobs, Newsletters, Promotions)
- Does NOT archive, delete, or modify anything except labels
- Requires: labels to exist, server running, `gmail.settings.basic` scope (re-auth: Ctrl+Shift+A in TUI)

## Server authentication
Optional token-based auth via `INBOX_SERVER_TOKEN` environment variable. If set, all requests must include `Authorization: Bearer <token>` header.

## API endpoints (localhost:9849)
```
GET  /health
GET  /conversations?source=all|imessage|gmail&limit=50
GET  /messages/{source}/{conv_id}?thread_id=...
POST /messages/send  {"conv_id", "source", "text"}
POST /messages/gmail/{msg_id}/unsubscribe
POST /messages/gmail/bulk-unsubscribe  {"msg_ids": [str]}
POST /search  {"q", "sources": [str], "limit"}
GET  /gmail/labels?account=...
POST /gmail/batch-modify  {"msg_ids": [str], "add_label_ids": [str], "remove_label_ids": [str], "account"}
POST /gmail/filters  {"from_filter", "subject_filter", "add_label_ids": [str], "remove_label_ids": [str], "account"}
POST /preflight/write  {"operation", "args"}  # validates write op before execution
GET  /calendar/events?date=YYYY-MM-DD
POST /calendar/events  {"summary", "start", "end", "attendees", ...}
POST /calendar/events/quick  {"text": "Meeting 2pm-3pm @ Office"}
PUT  /calendar/events/{id}
DELETE /calendar/events/{id}
GET  /notes?limit=50
GET  /notes/{id}
GET  /reminders?list_name=...&show_completed=false&limit=100
GET  /reminders/lists
POST /reminders  {"title", "list_name", "due_date", "notes"}
POST /reminders/{id}/complete
PUT  /reminders/{id}  {"title", "due_date", "notes"}
DELETE /reminders/{id}
GET  /github/notifications?all=false
POST /github/notifications/{id}/read
POST /github/notifications/read-all
GET  /github/pulls?repo=owner/name
GET  /drive/files?q=...&shared=false&limit=20&account=...&folder_id=...
GET  /drive/files/{id}?account=...
POST /drive/upload  (multipart: file + folder_id + account)
POST /drive/folder  {"name", "parent_id", "account"}
DELETE /drive/files/{id}?account=...
GET  /sheets?q=...&limit=20&account=...
POST /sheets  {"title", "sheets", "account"}
GET  /sheets/{id}
DELETE /sheets/{id}?account=...
GET  /sheets/{id}/values/{range}?account=...
PUT  /sheets/{id}/values/{range}?account=...  {"values": [[...]], "value_input": "USER_ENTERED"}
POST /sheets/{id}/values/{range}/append?account=...  {"values": [[...]]}
DELETE /sheets/{id}/values/{range}?account=...
POST /sheets/{id}/values/batch-get?account=...  {"ranges": [...]}
POST /sheets/{id}/values/batch-update?account=...  {"data": [...], "value_input": "USER_ENTERED"}
POST /sheets/{id}/tabs?account=...  {"title", "rows", "cols"}
DELETE /sheets/{id}/tabs/{sheet_id}?account=...
PATCH /sheets/{id}/tabs/{sheet_id}?account=...&title=NewTitle
POST /sheets/{id}/tabs/{sheet_id}/copy?account=...  {"dest_spreadsheet_id"}
POST /sheets/{id}/format?account=...  {"requests": [...]}
GET  /docs?q=...&limit=20&account=...
POST /docs  {"title", "account"}
GET  /docs/{id}
DELETE /docs/{id}?account=...
GET  /docs/{id}/text
POST /docs/{id}/text  {"text", "index"}
GET  /docs/{id}/export?format=text/plain|application/pdf|text/html&account=...
POST /ambient/start
POST /ambient/stop
GET  /ambient/status
GET  /ambient/notes?limit=50&q=search
GET  /ambient/notes/{id}
POST /dictation/start
POST /dictation/stop
POST /autocomplete  {"draft", "messages", "max_tokens", "temperature", "mode"}
GET  /llm/status
POST /llm/warmup
GET  /accounts
POST /accounts/add
POST /accounts/reauth  {"email": "..."}
GET  /notifications/config
PUT /notifications/config  {enabled, sources, quiet_hours}
POST /notifications/test  {"title", "body"}
```

## Key bindings (TUI)
- **Ctrl+1-5** — switch tabs: All, iMessage, Gmail, Calendar, Notes
- **Ctrl+6** — toggle ambient listening (start/stop)
- **Ctrl+7** — switch to GitHub tab
- **Ctrl+R** — refresh all data
- **Ctrl+A** — add Google account (opens browser)
- **Ctrl+Shift+A** — re-auth current account (new scopes)
- **Ctrl+N** — new calendar event
- **Ctrl+D** — delete selected event
- **r** — mark selected GitHub notification as read (GitHub tab only)
- **Shift+R** — mark all GitHub notifications as read (GitHub tab only)
- **o** — open notification URL in browser (GitHub tab only)
- **Tab** — accept autocomplete suggestion (in compose input)
- **Ctrl+Q** — quit
- **Vim mode** (when no input focused):
  - **j** — cursor down
  - **k** — cursor up
  - **g** — jump to top
  - **Shift+G** — jump to bottom
  - **/** — search
  - **?** — vim help

## Google Calendar
- Creates, reads, updates, deletes calendar events
- **Recurring events**: supported via `recurrence` parameter (RRULE format, e.g., `["RRULE:FREQ=WEEKLY;BYDAY=MO"]`)
- **Reminders**: supported via `reminders` parameter (e.g., `{"useDefault": false, "overrides": [{"method": "email", "minutes": 15}]}`)
- Tracks `recurring_event_id` to support updates to recurring series
- Multi-account: queries all authed accounts on refresh

## Google Sheets
- Uses the same OAuth token as Gmail/Calendar (full `spreadsheets` scope added)
- Full CRUD on spreadsheets: create, list, get metadata, trash (recoverable)
- **Range operations**: read (A1 notation), write, append, clear, batch read/write
- **Sheet tab management**: add, delete, rename, copy to another spreadsheet
- **Formatting**: raw `batchUpdate` requests for cell formatting, borders, colors, formulas
- Multi-account: routes by `account` param, queries all accounts for list
- Agents can perform any operation available in Sheets API (values, formatting, tabs, formulas)
- All mutations return operation stats (cells updated, etc.)

## Google Docs
- Uses the same OAuth token as Gmail/Calendar (full `documents` scope added)
- Create, read, update, delete documents
- **Text operations**: insert and append text
- Multi-account: routes by `account` param, queries all accounts for list

## Google Drive
- Uses the same OAuth token as Gmail/Calendar (full `drive` scope)
- Upload files, create folders, list/search/delete files
- Multi-account: queries all authed accounts, routes by `account` param
- Delete = trash (recoverable), not permanent delete
- Supports folder filtering via `folder_id` parameter

## Gmail advanced
- **Label management**: list labels, batch modify labels on messages, create filters
- **Cross-source search**: `/search` endpoint queries iMessage, Gmail, Calendar, Notes with unified results
- **Batch operations**: apply/remove multiple labels across many messages
- **Thread enrichment**: Gmail threads normalized with AI-generated summaries and action item extraction

## GitHub
- Personal access token in `github_token.txt` (needs `notifications` + `repo` scopes)
- Notifications: displayed in TUI with type icons (🔀 PR review, 🐛 issue, 📦 release, 🔔 other) plus unread indicator; mark-read individual or all
- Pull requests: searches for PRs requesting review from the authed user
- Token file is gitignored

## Apple Reminders
- Reads from all Reminders SQLite DBs in `~/Library/Group Containers/group.com.apple.reminders/`
- Multiple DBs = multiple accounts (iCloud, local, etc.) — scans all non-empty ones
- Timestamps use Apple epoch (2001-01-01), same as Notes
- Mutations (complete, create) via AppleScript — SQLite is read-only
- Lists come from `ZREMCDBASELIST`, reminders from `ZREMCDREMINDER`

## Multi-account Google
- Each Google account gets its own token in `tokens/`
- Legacy `token.json` auto-migrates to `tokens/` on first run
- All accounts queried on refresh, contacts tagged with `gmail_account`
- Sends route through the correct account's service
- Scopes: `gmail.readonly` + `gmail.send` + `calendar` + `drive` + `spreadsheets` + `documents` (full read/write)
- **Note**: New `documents` scope added for Docs integration; users must re-auth once via `/accounts/reauth`
- Token locking via `token.json.lock` prevents concurrent access conflicts
- **Account routing**: Write endpoints use account-specific service helpers (_get_cal_service_for_account, _get_drive_service_for_account, etc.) with fallback logic when account not specified

## MCP Server
- **mcp_server.py** — stdio-based MCP server for Claude integration
- **mcp_backend.py** — backend client for the MCP server, calls inbox API
- Exposes all inbox functionality (conversations, calendar, reminders, search, Gmail operations) via MCP tools
- Agents can use the MCP interface directly without hitting the server HTTP API

## Scheduler
- **scheduler.py** — background task scheduler for recurring operations (cleanup, reminders, etc.)
- Persistent task state stored in `.inbox_scheduler.sqlite3`
- Runs concurrently with server, survives restarts

## LLM + Audio stack
- **LLM**: Qwen3.5-0.8B-MLX-4bit (~500MB) — shared singleton for extraction + autocomplete
- **Outlines model**: Cached singleton wrapping the base model — reused for all constrained generation calls
- **Constrained gen**: Outlines with mlx-lm backend — FSM token masking for valid JSON
- **Ambient ASR**: mlx-whisper (whisper-base.en-mlx) — chunk-based, MLX-native
- **Dictation ASR**: whisper-stream C++ binary — streaming 500ms step, low latency
- **Ambient notes**: Written to Obsidian vault at `~/vault/daily/` as markdown; captures logged with preview
- **Keyboard injection**: pyobjc CGEvent (dictation mode, needs Accessibility permission)
- **Ambient auto-start**: Ambient listening starts automatically on server startup (gracefully fails if dependencies unavailable)
- **Gemini API**: Optional LLM backend via `gemini_api_key.txt` for fallback or specific tasks

## External integrations
- **Google Maps** (optional): API key in `google_maps_key.txt` for location-based features

## Key design decisions
- **Client-server split** — server handles data, TUI and agents are both clients
- **Server-side conv cache** — server caches conversations so `/messages/send` just needs conv_id + text
- **Query by chat.rowid, not handle.id** — handles group chats correctly
- **contact.guid** holds the iMessage chat GUID for sending to groups via AppleScript
- **Contact.id for iMessage** = `chat.rowid` (integer as string), NOT a phone number
- **Contact.reply_to for Gmail** = actual email address; `contact.id` is the Gmail message ID
- **Phone matching** generates variants for inconsistent iMessage/AddressBook storage
- **Gmail HTML→text** — strips HTML tags, quoted replies, signatures, tracking URLs
- **Optimistic send** — message appears in TUI immediately, confirmed in background
- **Notes via SQLite** for listing, AppleScript for full body (protobuf parsing is too complex)
- **Flattened module structure** — LLM and audio logic integrated into main services rather than nested directories
- **Tab state preservation** — switching between tabs preserves active conversation/event selection so returning to a tab shows the same context
- **Vim mode** — optional navigation via j/k/g/G in focused lists/tables, respects focused text input (doesn't interfere with compose)
- **Preflight validation** — Write operations validated against scope/permission checks before execution to catch errors early
- **Gmail account routing policy** — When account not specified for writes, uses fallback logic to select default (prioritizes message owner account for replies)

## Data sources
- **iMessage**: `~/Library/Messages/chat.db` (read-only SQLite)
- **AddressBook**: `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`
- **Apple Notes**: `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`
- **Apple Reminders**: `~/Library/Group Containers/group.com.apple.reminders/Container_v1/Stores/Data-*.sqlite`
- **Gmail/Calendar/Drive/Sheets/Docs**: Google API via OAuth tokens in `tokens/` (token locking via `token.json.lock`)
- **GitHub**: REST API via personal access token in `github_token.txt`

## Testing
- Comprehensive test suite in `tests/` covers services, server, client, audio, LLM, contacts, ambient notes
- `conftest.py` stubs heavy ML/hardware dependencies (`mlx_lm`, `mlx_whisper`, `sounddevice`, `Quartz`, `outlines`) so tests run in CI without full deps installed
- Fixtures for temp reminders DB, vault paths, mock services

## Dev commands
```bash
uv run ruff check --fix .   # lint
uv run pyright              # type check
uv run pytest               # unit tests
```

## Agent Slash Commands

The inbox skill provides first-class slash commands for agent workflows:

```
/inbox                 — show command menu
/inbox morning-brief   — today's calendar + unread + followups + GitHub
/inbox triage          — score and prioritize inbox threads, output TSV
/inbox followup        — find threads needing reply, draft starters
/inbox batch           — bulk archive from TSV with resumable state
/inbox health          — check server, tokens, urgent threads
```

Skill router: `.claude/skills/inbox/SKILL.md`
Modes: `modes/*.md` (each is a self-contained workflow prompt)
User config: `config/priorities.yml`, `config/_profile.md` (never auto-updated)
Batch state: `batch/triage-output.tsv`, `batch/archive-state.tsv` (auto-generated, gitignored)

## Session-Start Health Check

On every session start, silently run:

```bash
curl -sf http://localhost:9849/health
```

Only surface output if something needs action:
- Non-200 / connection refused → "Server is down. Start: `uv run python inbox_server.py`"
- `/accounts` returns empty → "No Google accounts authed. Re-auth via Ctrl+A in TUI."
- Silent if all good.

Do not report "all good" on every session start — only failures.
