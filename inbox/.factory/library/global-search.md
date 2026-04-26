# Global Search Implementation Notes

## Overview
Global search (Ctrl+\\) opens a `SearchScreen` modal overlay that queries all inbox sources simultaneously and lets the user jump to any result.

## Key Components

### services.search_all()
- Entry point: `search_all(query, sources, limit, gmail_services, cal_services) -> dict`
- Returns `{query, total, results: [{source, id, title, snippet, timestamp, metadata}]}`
- Snippet max 150 chars; `_make_snippet()` centers on match position
- Results sorted by `timestamp` desc (most recent first)
- Source dispatchers: `_search_imessage`, `_search_gmail`, `_search_notes`, `_search_reminders`, `_search_calendar`
- iMessage: SQLite LIKE on `message.text`, returns `chat_id` as `id`
- Gmail: uses native Gmail API `q=` parameter for server-side search
- Notes: SQLite LIKE on `ZTITLE1 OR ZSNIPPET`
- Reminders: SQLite LIKE on `ZTITLE OR ZNOTES`, only incomplete reminders
- Calendar: Google Calendar API `q=` parameter, ±30 days past / +180 days future

### POST /search Endpoint
- Request: `{q: str, sources: list[str] = ["all"], limit: int = 50}`
- Passes `gmail_services` and `cal_services` from server state
- Returns the dict from `search_all` directly

### InboxClient.search()
- `search(q, sources=None, limit=50) -> dict`
- Only includes `sources` in payload if not None

### SearchScreen (ModalScreen)
- Opened via `action_search()` bound to `ctrl+backslash`
- Input debounce: 300ms timer, cancels prior timer on each change
- Worker: `_run_search()` with `@work(thread=True, exit_on_error=False)`
- Uses `self.app.call_from_thread()` (not `self.call_from_thread()`) due to pyright type stubs
- Dismisses with `None` on Escape, with selected `dict` on Enter or ListView.Selected
- Shows per-source counts in status bar: e.g., "3 results  notes:2 reminders:1"

### Navigation (_on_search_result)
- Switches to the appropriate tab via `Tabs.active`
- Uses `call_after_refresh` to select the item after the tab renders
- `_select_search_result(source, item_id, metadata)` walks the ListView children
- Gracefully handles missing items: `self.notify(...)` + stays on tab

## Key Bindings
- `ctrl+backslash` — open search overlay (`Ctrl+/` is not a valid Textual binding name)

## Test Coverage
- `tests/test_search.py` — 21 tests: snippet helper, all 5 source searchers, search_all edge cases
- `tests/test_server_endpoints.py` — 4 new tests: response shape, empty query, source filter passthrough, default sources
- `tests/test_client.py` — 4 new tests: basic search, sources param, default no-sources, empty result
- `tests/test_inbox_app.py` — 6 new tests: overlay opens, Esc dismisses, item renders, show results, navigation

## Gotchas
- `Ctrl+/` is not a recognized Textual key binding — use `ctrl+backslash` instead
- `call_from_thread` is not in Textual's type stubs for `ModalScreen`; use `self.app.call_from_thread()`
- Gmail and Calendar searches require live services; mocked in tests
- iMessage search returns `chat_id` as `id` (not message rowid) so navigation can find the conversation
