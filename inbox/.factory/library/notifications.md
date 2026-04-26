# Notifications

## Shipped

- `send_notification(title, body, source)` in `services.py` — sends macOS desktop notifications via `UNUserNotificationCenter` (pyobjc) with osascript fallback; respects config.
- `load_notification_config()` / `save_notification_config(cfg)` — read/write `~/.config/inbox/notifications.json` with auto-create on first use.
- Config schema: `enabled`, `sources` (imessage/gmail/calendar/github), `quiet_hours` (enabled/start/end with overnight range support).
- `POST /notifications/test {title, body}` — verify notifications work end-to-end.
- `GET /notifications/config` / `PUT /notifications/config` — read/write config via API.
- Client: `notification_config()`, `update_notification_config(cfg)`, `test_notification(title, body)`.
- TUI bell indicator: `bell_indicator` reactive on `InboxApp`; `_update_bell_indicator()` sums iMessage + Gmail + GitHub unread and sets `🔔 N` or `""`. Updated in `_populate()` on every poll cycle.
- `_check_and_fire_notifications()` — called in `_populate` before state update; fires desktop notifications for new iMessage/Gmail/GitHub unreads and upcoming calendar events (15 min window). Uses `_prev_*_unread` baselines (−1 on boot = no fire on first poll). Calendar events deduplicated via `_notified_events` set.

## Design Notes

- `_fire_notification` routes through `POST /notifications/test` to keep the TUI as a thin client; the server applies config/quiet-hours logic.
- pyobjc path uses `importlib.import_module` to avoid static import errors on non-macOS systems.
- "Clicking notification focuses TUI" is out of scope — macOS UNUserNotification click callbacks require a persistent app delegate which is incompatible with the current server architecture.

## Tests

- `tests/test_notifications.py` — 17 tests covering config roundtrip, quiet hours, source filtering, osascript dispatch, and server endpoints.
- `tests/test_inbox_app.py` — 10 new tests covering bell indicator (zero/nonzero/sum), `_populate` integration, and `_check_and_fire_notifications` triggers (iMessage, GitHub mention, calendar upcoming, dedup).
