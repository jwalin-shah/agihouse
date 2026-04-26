# Command Palette Implementation Notes

## Overview
The command palette (Ctrl+P) provides a VS Code-style command launcher overlay. Fuzzy filter narrows commands as you type; NLP fallback handles natural-language queries when the LLM is loaded.

## Key Components

### command_palette.py (standalone module)
- `make_command(id, name, description, category, action)` — factory for command dicts
- `fuzzy_score(query, text)` — returns 3 (exact) / 2 (prefix) / 1 (substring) / 0 (no match)
- `filter_commands(query, commands)` — scores name×2 + description + category, returns sorted list
- `build_commands(app)` — builds the full command registry by closing over `app.action_*` methods
- `nlp_classify(query, commands)` — calls `services.generate_json` with a Pydantic schema for structured output
- `resolve_nlp(query, commands, threshold)` — wraps NLP with threshold check and fallback messages

### CommandPaletteScreen (inbox.py)
- Extends `ModalScreen[CommandDict | None]`
- CSS: centered 60-wide container with input + list + footer
- Input → `on_query_changed` → `filter_commands` → `_rebuild_list`
- Enter → run highlighted item, or NLP fallback if list is empty
- Esc → `action_dismiss` (Textual ModalScreen built-in)
- `_try_nlp` called only when query is non-empty and list is empty after filtering

### InboxApp integration
- `Binding("ctrl+p", "command_palette", "Commands")` added to BINDINGS
- `action_command_palette()` builds commands, checks `llm_is_loaded()`, pushes screen
- `_on_palette_result(result)` callback executes `result["action"]()`

## Command Categories
- **Navigate**: tab switches (All, iMessage, Gmail, Calendar, Notes, Reminders, GitHub, Drive)
- **Action**: Refresh, Quit, Toggle Ambient, Mark All GitHub Read
- **Create**: New Event, Delete Event, Jump to Date, New Reminder, Filter Reminder List, New Gmail
- **Settings**: Add Account, Re-auth Account

## NLP Behavior
- Uses `services.generate_json` with constrained Pydantic schema `_NlpResult`
- Threshold: 0.6 confidence required to execute
- LLM unavailable → graceful fallback, palette footer shows hint
- Low confidence → shows fuzzy suggestions as "try: ..."
- Only triggered when literal filter produces no results

## Test Harness Considerations
- `_HarnessApp` (in test_command_palette.py) overrides `on_mount` and `boot` to skip server
- Textual Pilot tests use `await pilot.pause(0.1)` after key presses for event settling
- `screen_stack` checked directly to assert palette open/closed state
- `ListView.__len__` used (not `item_count`) for item count assertion
