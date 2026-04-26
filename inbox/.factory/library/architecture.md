# Architecture

How the system works — components, relationships, data flows, invariants.

## System Overview

Client-server architecture running entirely on localhost. A **FastAPI server** (`inbox_server.py`, port 9849) wraps a **data access layer** (`services.py`) that integrates iMessage, Gmail, Google Calendar, Google Drive, Apple Notes, Apple Reminders, and GitHub. A **Textual TUI** (`inbox.py`) acts as a thin HTTP client via `inbox_client.py` — it never accesses data sources directly. External agents can also hit the server API. The server auto-starts when the TUI launches and persists as a background process.

```
┌──────────────┐   HTTP    ┌──────────────────┐        ┌─────────────────────┐
│  TUI (inbox) │ ───────→  │  FastAPI Server   │ ────→  │  Data Sources       │
│  inbox.py    │ ←───────  │  inbox_server.py  │ ←────  │  (SQLite, APIs,     │
│              │           │  (port 9849)      │        │   AppleScript)      │
└──────────────┘           └──────────────────┘        └─────────────────────┘
                                  ↑
                           ┌──────┴──────┐
                           │  Agents /   │
                           │  Scripts    │
                           └─────────────┘
```

## Components

### Data Access Layer (services.py)

The central data module — every integration's read/write logic lives here (primarily as functions, plus service classes for ambient/dictation/LLM helpers). Key responsibilities:

- **Data models**: `Contact`, `Msg`, `CalendarEvent`, `Note`, `Reminder`, `GitHubNotification`, `DriveFile` — all `@dataclass` types used throughout the stack.
- **iMessage**: `imsg_contacts(limit)` reads `~/Library/Messages/chat.db` via read-only SQLite, joining `chat`, `message`, `handle`, and `chat_message_join` tables. `imsg_thread(chat_id, limit)` fetches messages for a conversation. `imsg_send(contact, text)` sends via AppleScript (`osascript`), handling both 1:1 and group chats by `contact.guid`.
- **Gmail**: `gmail_contacts(service, account_email, limit)` lists inbox messages deduplicated by `threadId`. `gmail_thread(service, msg_id, thread_id)` fetches full thread with HTML→text conversion via `_decode_body()` / `_html_to_text()` / `_clean_email_body()`. `gmail_send(service, contact, body)` sends replies preserving `In-Reply-To` and `threadId`.
- **Calendar**: `calendar_events(cal_services, date)` queries all calendars across all accounts for a given day. `calendar_create_event()`, `calendar_update_event()`, `calendar_delete_event()` for mutations. `parse_quick_event(text)` parses natural-ish "Title 2pm-3pm @ Location" format.
- **Notes**: `notes_list(limit)` reads from `NoteStore.sqlite` via read-only SQLite. `note_body(title)` fetches full body via AppleScript (because protobuf parsing is impractical).
- **Reminders**: `_reminders_dbs()` scans all non-empty DBs in `~/Library/Group Containers/group.com.apple.reminders/`. `reminders_list()` and `reminders_lists()` read via SQLite. `reminder_complete(title)` and `reminder_create()` mutate via AppleScript.
- **GitHub**: `github_notifications()`, `github_mark_read()`, `github_mark_all_read()`, `github_pulls()` — all use `httpx` against `api.github.com`. Token sourced from `gh auth token` CLI first, then `github_token.txt` fallback.
- **Google Drive**: `drive_files()`, `drive_upload()`, `drive_create_folder()`, `drive_delete()`, `drive_get()` — standard Google Drive v3 API calls.
- **Auth**: `google_auth_all()` loads all tokens from `tokens/*.json`, returns `(gmail_svcs, cal_svcs, drive_svcs)` dicts keyed by email. `add_google_account()` runs OAuth browser flow. Legacy `token.json` auto-migrates to `tokens/` with scope re-auth if needed.
- **Contacts**: Global `_contacts = ContactBook()` singleton initialized via `init_contacts()` at server startup. Used by `imsg_contacts()` and `imsg_thread()` to resolve phone numbers to display names.

### Server (inbox_server.py)

FastAPI application with async handlers that delegate to `services.py` via `asyncio.to_thread()` (since all data access is synchronous).

- **`ServerState`**: Singleton holding `gmail_services`, `cal_services`, `drive_services` (dicts keyed by email), `conv_cache` (dict keyed by `"source:id"` → `Contact`), and `events_cache` (list of `CalendarEvent`).
- **Lifespan**: On startup, calls `init_contacts()` and `google_auth_all()` to populate `state`.
- **Conversation cache**: `GET /conversations` populates `state.conv_cache`. `POST /messages/send` requires the conversation to exist in cache (caller must fetch conversations first). Cache key format: `"{source}:{conv_id}"`.
- **Pydantic models**: `ConversationOut`, `MessageOut`, `CalendarEventOut`, `NoteOut`, `ReminderOut`, `GitHubNotificationOut`, `DriveFileOut` — serialization layer between dataclasses and JSON responses. Request models: `SendRequest`, `CreateEventRequest`, `QuickEventRequest`, `UpdateEventRequest`, `ReminderCreateRequest`, `DriveCreateFolderRequest`, `AccountRequest`.
- **Converter helpers**: `_contact_to_out()`, `_msg_to_out()`, `_event_to_out()`, `_note_to_out()`, `_reminder_to_out()`, `_gh_notif_to_out()`, `_drive_to_out()` — map dataclasses to Pydantic models.
- **Multi-account routing**: Gmail/Calendar/Drive endpoints route to the correct service based on `account` param or `contact.gmail_account` from cache.

### Client (inbox_client.py)

Synchronous HTTP client (`httpx.Client`) wrapping every server endpoint as a method on `InboxClient`.

- **Server lifecycle**: `is_server_running()` pings `/health`. `start_server()` launches `inbox_server.py` as a subprocess, redirecting stdout/stderr to `server.log`. `ensure_server(max_wait=30)` starts the server if not running and polls until healthy.
- **Method naming convention**: Mirrors endpoint paths — `conversations()`, `messages()`, `send()`, `calendar_events()`, `create_event()`, `create_quick_event()`, `notes()`, `note()`, `reminders()`, `reminder_complete()`, `github_notifications()`, `drive_files()`, etc.
- **Timeout**: Default 30s for normal requests; `add_account()` and `reauth_account()` use 120s (OAuth browser flow).

### TUI (inbox.py)

Textual `App` subclass (`InboxApp`) with a sidebar + content layout.

- **Widget hierarchy**: `Header` → `Horizontal#main` → (`Vertical#sidebar` [Tabs + ListView#contact-list], `Vertical#content` [Static#status, MessageView#messages, DetailView#detail-view, Horizontal#compose-area [Input#compose]]) → `Footer`.
- **Custom widgets**: `ConversationItem(ListItem)` renders iMessage/Gmail entries with source icons and unread badges. `EventItem(ListItem)` renders calendar events with time ranges. `NoteItem(ListItem)` renders note titles with folder/snippet. `MessageView(Static)` uses `reactive[list[dict]]` — recomposes on change, renders messages as Rich `Panel` widgets (right-aligned cyan for self, left-aligned green for others). `DetailView(Static)` uses `reactive[dict | None]` — renders calendar event details or note bodies.
- **Tab navigation**: 8 tabs (All, iMessage, Gmail, Calendar, Notes, Reminders, GitHub, Drive) via `Tabs` widget. `_active_filter` tracks current filter. `_toggle_views()` swaps between `MessageView` and `DetailView` based on tab. `_render_sidebar()` re-populates `ListView` based on filter.
- **Threading model**: `@work(thread=True)` decorator for all I/O operations (`boot()`, `_bg_refresh()`, `_bg_poll()`, `_load_thread()`, `_load_note()`, `_do_send()`, `_create_quick_event()`, `_do_delete_event()`, `_do_add_account()`, `_do_reauth()`). Results pushed back to main thread via `self.call_from_thread()`.
- **Polling**: `POLL_INTERVAL = 10` seconds. `_poll_refresh()` → `_bg_poll()` does a lightweight check — fetches conversations, compares unread counts and IDs, only fully refreshes sidebar if data changed.
- **Optimistic send**: On `Input.Submitted`, immediately appends a synthetic "Me" message to `MessageView.messages`, then fires `_do_send()` in background. For iMessage, `_reload_after_send()` retries thread reload up to 3 times (1s, 2s, 3s delays) until the sent message appears in the SQLite DB.
- **Key bindings**: `Ctrl+1-8` (tab switch), `Ctrl+R` (refresh), `Ctrl+A` (add account), `Ctrl+Shift+A` (re-auth), `Ctrl+N` (new event), `Ctrl+D` (delete event), `Ctrl+Shift+6` (ambient toggle), `Escape` (clear compose), `Ctrl+Q` (quit).

### Contacts (contacts.py)

`ContactBook` class with `load()` and `resolve(identifier)` methods.

- **`load_contact_map()`**: Scans all macOS AddressBook SQLite databases (`~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`). Single-pass query joins `ZABCDRECORD` with `ZABCDPHONENUMBER` and `ZABCDEMAILADDRESS`. Builds a `dict[str, str]` mapping every normalized phone variant and email (lowercased) to display name.
- **`_phone_variants(phone)`**: Generates match variants for inconsistent storage — e.g. `"5551234567"` → `["5551234567", "+5551234567", "15551234567", "+15551234567"]`.
- **`resolve(identifier)`**: Direct lookup, then tries phone variants. Falls back to raw identifier.

### Ambient Notes (ambient_notes.py)

Writes structured markdown to an Obsidian vault at `~/vault/`.

- **`append_to_daily(content)`**: Appends to `~/vault/daily/YYYY-MM-DD.md` with `## HH:MM` headers. Creates the daily note with `# YYYY-MM-DD` title if it doesn't exist.
- **`save_note(raw_transcript, summary, topics)`**: Formats ambient captures with summary, action items as `- [ ]` checkboxes, topic tags as `#hashtags`, and raw transcript in a collapsed `> [!note]- Transcript` callout.
- **`list_daily_notes(limit)`** and **`read_daily_note(date)`**: List/read daily note files.
- **`get_recent_captures(limit)`**: Parses today's daily note for `## HH:MM` sections, returns structured `{timestamp, summary}` dicts.

### Ambient Daemon (ambient_daemon.py)

Standalone background process for continuous audio capture → ASR → extraction → notes.

- **`main()`**: Checks `whisper_stream_available()`, sets up `SIGINT`/`SIGTERM` signal handlers, creates `AmbientService(on_note=on_note)`, starts it, then sleeps in a loop.
- **`on_note(raw_transcript, summary)`**: Callback that optionally runs `services.extract()` for topic extraction, then calls `save_note()`.
- **Designed for `launchctl`**: Can be auto-started via macOS LaunchAgent plist.
- **Dependencies**: `services.AmbientService` (audio capture + ASR), `services.whisper_stream_available` (dictation availability check), `services.extract` (topic extraction).

## Data Sources

| Source | Access Method | Path / Endpoint |
|--------|-------------|-----------------|
| iMessage | SQLite (read-only) | `~/Library/Messages/chat.db` |
| AddressBook | SQLite (read-only) | `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb` |
| Apple Notes | SQLite (read-only) for listing, AppleScript for body | `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite` |
| Apple Reminders | SQLite (read-only) for listing, AppleScript for mutations | `~/Library/Group Containers/group.com.apple.reminders/Container_v1/Stores/Data-*.sqlite` |
| Gmail | Google API via OAuth | `tokens/*.json` for credentials |
| Google Calendar | Google API via OAuth | Same tokens as Gmail |
| Google Drive | Google API via OAuth | Same tokens as Gmail (full `drive` scope) |
| GitHub | REST API (`api.github.com`) | `gh auth token` CLI or `github_token.txt` |
| Ambient Notes | Filesystem (markdown) | `~/vault/daily/YYYY-MM-DD.md` |

## Data Flow Patterns

### 1. Read path: TUI → Server → Service → Data Source

```
TUI (worker thread)                Server                    Service
───────────────────    HTTP GET    ──────────────    sync     ──────────────
conversations()     ──────────→    /conversations  ───→      imsg_contacts()
                                                   ───→      gmail_contacts()
                    ←─────────     JSON response   ←───      Contact[]
```

All server handlers use `asyncio.to_thread()` to run synchronous service functions without blocking the event loop. TUI uses `@work(thread=True)` to keep the UI responsive.

### 2. Conversation cache for send operations

The server maintains `state.conv_cache` (keyed by `"source:id"`). `GET /conversations` populates it. `POST /messages/send` looks up the `Contact` object from cache to get routing info (`contact.guid` for iMessage, `contact.gmail_account` + `contact.reply_to` for Gmail). Callers must fetch conversations before sending.

### 3. Polling model (10s interval)

TUI polls `GET /conversations` every 10 seconds via `_bg_poll()`. It compares unread counts and conversation IDs — only triggers a full sidebar re-render if data changed. Calendar and notes are also refreshed on change detection.

### 4. Optimistic UI for message sending

On send, the TUI immediately appends a synthetic message to the view, then fires the actual send in a background thread. For iMessage, after a successful send, it retries fetching the thread (at 1s, 2s, 3s intervals) until the message appears in the SQLite DB, then replaces the optimistic message with the real one.

### 5. Multi-account routing

Google services are keyed by email address. `google_auth_all()` returns three dicts: `gmail_svcs[email]`, `cal_svcs[email]`, `drive_svcs[email]`. Endpoints accept an optional `account` param; if omitted, they default to the first available account. Gmail conversations carry `gmail_account` in their `Contact` object for correct service routing on send.

## Key Invariants

These architectural invariants must be preserved by all workers:

1. **Server is the single source of truth** for data access — no component other than `services.py` touches data sources directly.
2. **TUI is a thin client** — it only talks to the server via `InboxClient`. No SQLite, no API calls, no AppleScript.
3. **All mutations go through server endpoints** — send message, create/update/delete event, complete reminder, etc.
4. **Conversation cache must be populated before sending** — `POST /messages/send` will 404 if the conv isn't in `state.conv_cache`.
5. **Contact resolution happens at server startup** — `init_contacts()` runs in the `lifespan` handler; `ContactBook` is a global singleton in `services.py`.
6. **Google auth is multi-account** — all tokens live in `tokens/` directory, keyed by email. Legacy `token.json` auto-migrates.
7. **Google scopes are uniform** — every account gets `gmail.readonly` + `gmail.send` + `calendar` + `drive` scopes.
8. **AppleScript is used for all Apple mutations** — Notes body retrieval, Reminders complete/create, iMessage send. SQLite access is strictly read-only.
9. **iMessage send uses `osascript` via `subprocess`** — group chats use `chat.guid`, 1:1 uses `handle.id` looked up from the DB.
10. **GitHub token resolution**: tries `gh auth token` CLI first, falls back to `github_token.txt`. No OAuth flow.
11. **Apple timestamps use Apple epoch** (2001-01-01) — `services.py` converts with `APPLE_EPOCH + timedelta(seconds=value)`.
12. **Flattened module structure** — all core logic in top-level `.py` files, no nested packages for the main app (audio/llm were flattened into services).

## Planned Architecture Additions

The following features are planned to be added on top of the current architecture:

- **3 new TUI tabs**: Reminders, GitHub, and Drive — each with `ListItem` subclasses and appropriate detail views, following the `EventItem`/`NoteItem` pattern.
- **Calendar multi-view**: Day, week, and agenda views with navigation controls, replacing the single-day list.
- **Gmail CRUD**: Archive, delete, star, and compose new emails — new server endpoints and TUI actions.
- **Global search**: New `GET /search?q=...` endpoint that searches across all sources; TUI overlay with result rendering.
- **Command palette**: TUI overlay with fuzzy matching for actions, conversations, and navigation (similar to VS Code's Ctrl+P).
- **Dual LLM stack**: 0.8B model (Qwen3.5-0.8B-MLX-4bit) for fast extraction + 3-4B model for higher-quality tasks. Lazy loading, serialized inference to avoid GPU contention.
- **macOS native notifications**: `pyobjc` (`NSUserNotification` / `UNUserNotificationCenter`) for unread messages, calendar reminders, GitHub mentions.
- **Unified contact profiles**: Merge iMessage, Gmail, and GitHub identities into a single contact view with cross-source activity.
- **Voice pipeline**: Ambient audio capture (mlx-whisper) + dictation mode (whisper-stream C++ binary with keyboard injection via `pyobjc` CGEvent).
- **Configurable settings**: Notifications, keybindings, theme, poll intervals — persisted in `~/.config/inbox/config.toml`, loaded at startup.
