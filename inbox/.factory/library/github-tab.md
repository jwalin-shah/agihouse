# GitHub Tab Implementation Notes

## Overview
The GitHub tab (Ctrl+7) displays notifications from the GitHub API, using existing server endpoints.

## Key Components

### NotificationItem Widget
- Extends `ListItem` like `ReminderItem` and `ConversationItem`
- PR review requests visually distinguished with 🔀 magenta icon
- Issues use 🐛, Releases use 📦, other notifications use 🔔
- Unread notifications show bold title + yellow dot (●)
- Shows repo name, reason (human-readable), and timestamp

### Key Handlers (GitHub tab only, compose not focused)
- `r` — mark selected notification as read
- `shift+r` (R) — mark all notifications as read
- `o` — open notification URL in system browser (`webbrowser.open`)

### Data Flow
- `client.github_notifications()` → `_collect_auxiliary_data()` → `_populate()`
- GitHub data stored in `app.github_data: list[dict]`
- Active notification tracked in `app.active_notification: dict | None`

### Tab State Preservation
- `_save_tab_state("github")` saves `active_notification`
- `_restore_tab_state("github")` restores it

### Badge Count
- Shown in status bar when GitHub tab is active: "X notifications · Y unread"
- `_update_github_badge()` attempts to update Tab label (may not render in all Textual versions)

### DetailView
- GitHub notifications identified by having `reason` + `repo` fields (no `summary`, `completed`, or `list_name`)
- Shows repo, reason, type, read/unread status, timestamp, URL
- Includes keyboard shortcut hints

## Test Harness Considerations
- `HarnessInboxApp` overrides both `on_mount()` AND `boot()` — Textual may call parent's `on_mount` which triggers `boot()`
- Always set `client.github_notifications.return_value = []` on mock clients
- Worker thread methods (`_do_mark_notification_read`, `_do_mark_all_notifications_read`) are hard to test via `run_test()` due to async timing
