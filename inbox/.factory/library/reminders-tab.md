# Reminders Tab

## Implementation

The Reminders tab is fully implemented in `inbox.py` with backend support in `inbox_server.py`, `inbox_client.py`, and `services.py`.

### Key Components

- **ReminderItem(ListItem)**: Widget showing title (bold), due date (formatted or "No date"), list name, flagged/priority indicators, and notes snippet
- **on_key handler**: Single-key shortcuts (c=complete, e=edit, d=delete, f=filter) only active when compose is NOT focused and reminders tab is active
- **DetailView reminder rendering**: Shows title, completion status, due date, list name, priority, flagged, and notes

### Data Flow

- `reminders_data: list[dict]` and `reminder_lists: list[dict]` stored on InboxApp
- Fetched alongside calendar/notes in `_collect_auxiliary_data()` via `client.reminders()` and `client.reminder_lists()`
- `_populate()` accepts optional `reminders` and `reminder_lists` params (backward compatible)
- Poll and refresh both include reminders data

### Key Actions

- `action_complete_reminder()`: Calls `client.reminder_complete(reminder_id)`, clears detail view, refreshes
- `action_edit_reminder()`: Sets `_editing_reminder`, loads title into compose input
- `action_delete_reminder()`: Calls `client.reminder_delete(reminder_id)`, clears detail view, refreshes
- `action_filter_reminder_list()`: Cycles through `reminder_lists` names, wrapping back to "" (all lists)
- `_create_reminder(text)`: Called from compose input on reminders tab
- `_do_edit_reminder()`: Called when compose submitted with `_editing_reminder` set

### State Preservation

- `_rem_list_filter: str` — current list filter ("" = all)
- `_editing_reminder: dict | None` — reminder being edited
- `active_reminder: dict | None` — currently selected reminder
- All preserved across tab switches

### Tab State Preservation

When switching between tabs, the TUI preserves per-tab state via `_tab_state: dict[str, dict]`:
- **Messaging tabs** (all/imessage/gmail): saves `active_conv` and `messages` (cached message list)
- **Calendar tab**: saves `active_event`
- **Reminders tab**: saves `active_reminder` and `rem_list_filter`

On tab switch:
1. `_save_tab_state()` saves the current tab's state before switching
2. `_render_sidebar()` rebuilds the sidebar for the new tab
3. `_restore_tab_state()` restores the previously saved state (active selection, messages, detail)
4. `_restore_sidebar_selection()` highlights the previously selected item in the ListView

If a tab has no saved state (e.g., first visit), `_restore_tab_state` is a no-op — it doesn't clear existing content.

### AppleScript Retry Logic

All reminder mutations (`reminder_complete`, `reminder_create`, `reminder_edit`, `reminder_delete`) use `_run_applescript_with_retry()` which:
- Retries up to `APPLESCRIPT_RETRIES` (2) times with `APPLESCRIPT_RETRY_DELAY` (1.0s) delay
- Catches both AppleScript "fail" returns and subprocess exceptions
- Logs debug messages on retry attempts and logs failures via `_log_service_failure` after all retries exhausted

### Empty State

When no reminders exist (or filter yields empty), shows "All caught up! 🎉" message.
Status bar shows "0 reminders" with filter hint.

### Regression Tests

- Notes tab (Ctrl+5) still works
- Account auth (Ctrl+A, Ctrl+Shift+A) still works
- Tab switching preserves all data and filter state
