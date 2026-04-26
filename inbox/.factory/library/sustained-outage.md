# Sustained Outage Resilience

## Problem
When the server is down for an extended period, the TUI could crash because:
1. Textual workers with `exit_on_error=True` (the default) terminate the app on any unhandled exception
2. Repeated connection failures during polling could accumulate errors without recovery
3. If the server boot fails, polling never starts, so the TUI can't auto-recover

## Solution
1. **`exit_on_error=False`** on all `@work(thread=True)` decorators — prevents Textual from killing the app on worker exceptions
2. **Top-level try/except** in `_bg_poll` and `_bg_refresh` — catches any unexpected exceptions that slip through inner handlers
3. **`_consecutive_errors` counter** — tracks sustained outage, shows persistent "Server unreachable — press Ctrl+R to retry" after `_SUSTAINED_OUTAGE_THRESHOLD` (3) failures
4. **Polling starts even on boot failure** — so the TUI auto-recovers when the server comes back
5. **`action_refresh` resets `_consecutive_errors`** — Ctrl+R always tries fresh regardless of prior poll failures

## Key Code Locations
- `inbox.py`: `InboxApp._SUSTAINED_OUTAGE_THRESHOLD`, `InboxApp._consecutive_errors`
- `inbox.py`: `_bg_poll()` — consecutive error tracking and threshold override
- `inbox.py`: `boot()` — starts polling even on failure
- `inbox.py`: `action_refresh()` — resets `_consecutive_errors = 0`
- `tests/test_inbox_app.py`: 10 tests in "Sustained outage resilience" section
