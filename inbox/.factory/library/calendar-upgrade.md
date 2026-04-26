# Calendar Upgrade

All six calendar features shipped in one round.

## API
- `GET /calendar/events?start=X&end=Y` — range query (single-date `?date=X` still works for backward compat).
- `CalendarEvent.attendees: list[dict]` with name/email/responseStatus extracted from Google item.
- `calendar_events(range_start, range_end)` in services.py.
- `inbox_client.calendar_events_range(start, end)` for range fetches.

## TUI bindings (Calendar tab only)
- **← / →** — navigate prev/next day (or prev/next week in week mode).
- **v** — cycle day → week → agenda → day.
- **Ctrl+G** — jump-to-date overlay (parses `YYYY-MM-DD`, `May 1`, `Apr 10, 2026`, `4/10/2026`).
- **e** — edit selected event inline (title; Enter saves via PUT, Esc cancels).
- **Ctrl+N** — new event, **Ctrl+D** — delete event.

## Views
- **Day**: single-day events from `calendar_events(date=X)`.
- **Week**: 7 columns (Mon→Sun) via range API (single call). Today highlighted with bold cyan ● prefix.
- **Agenda**: 14-day forward window with day separators.
- All modes show multiday all-day events on every spanned day.

## Status bar
`[cyan]Today · Mon, Apr 10, 2026[/]  📅 Day  [dim]3 events · v: view · ←→: nav · g: go to date · e: edit[/]`
