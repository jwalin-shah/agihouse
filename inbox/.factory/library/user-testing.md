# User Testing

Testing surface, required testing skills/tools, and resource cost classification.

## Validation Surface

### Surface 1: REST API (curl)
- **URL**: http://localhost:9849
- **How**: `curl` commands against server endpoints
- **Setup**: Start server with `uv run python inbox_server.py`
- **Teardown**: `lsof -ti :9849 | xargs kill`
- **Capabilities**: All CRUD operations, health checks, data retrieval

### Surface 2: TUI (tuistory)
- **Launch**: `uv run python inbox.py` (auto-starts server)
- **How**: tuistory skill — launch terminal app, capture snapshots, send keyboard inputs
- **Capabilities**: Visual validation of widget rendering, tab navigation, keybindings, data display
- **Limitations**: Cannot test macOS-level notifications (only TUI indicator). Server must be running.

### Surface 3: TUI (Textual Pilot — headless)
- **How**: `async with InboxApp().run_test() as pilot:` in pytest
- **Capabilities**: Headless UI testing — press keys, click widgets, query DOM, assert on widget state
- **Limitations**: Requires mocking InboxClient (no real server)

## Validation Concurrency

**Machine specs**: 8GB RAM, 8 CPU cores, Apple Silicon (M-series)

### tuistory surface
- Each tuistory instance: ~200MB (terminal + app process)
- Server: ~100MB
- **Max concurrent validators: 2** (server shared, 2 terminal instances fit in headroom)
- Rationale: 8GB total, ~5GB baseline usage, ~3GB headroom * 0.7 = 2.1GB. Each instance ~200MB + shared server ~100MB = 500MB for 2 instances.

### curl surface
- Lightweight, shares server with tuistory
- **Max concurrent validators: 5** (negligible memory per curl process)

### CLI validator surface (pytest/pyright/ruff)
- `pytest` and static checks are CPU-heavy and can contend with TUI rendering
- **Max concurrent validators: 1** for full-suite pytest-based validation
- Rationale: keep one heavyweight validator to avoid noisy timing/resource skew

## Testing Skills Required
- `tuistory` — for TUI visual validation
- `curl` — for API endpoint validation (built-in, no special skill needed)

## Flow Validator Guidance: CLI

For assertions that use pytest, pyright, ruff, or python import checks:
- Working directory: `/Users/jwalinshah/projects/inbox`
- Commands: `uv run pytest -x -q`, `uv run pyright`, `uv run ruff check .`
- No server needed for CLI checks
- No isolation concerns — these are read-only analysis tools
- Capture full command output and exit code as evidence

## Flow Validator Guidance: tuistory

For assertions that require end-user TUI behavior validation:
- Working directory: `/Users/jwalinshah/projects/inbox`
- Launch command: `uv run python inbox.py`
- Validate behavior through keyboard-driven flows and snapshots
- If a scenario requires server-down behavior, explicitly stop server (`lsof -ti :9849 | xargs kill 2>/dev/null || true`) before launching/refreshing the app
- If a scenario requires server-up behavior, ensure health first: `curl -sf http://localhost:9849/health`
- Keep all validation on port `9849` only
- Capture clear evidence in report steps: key presses, observed status bar text, and whether the app stayed responsive

## Flow Validator Guidance: curl

For assertions that use curl against the server:
- Server URL: http://localhost:9849
- Start server: `cd /Users/jwalinshah/projects/inbox && uv run python inbox_server.py &`
- Wait for healthcheck: `for i in $(seq 1 20); do curl -sf http://localhost:9849/health && break; sleep 1; done`
- Teardown: `lsof -ti :9849 | xargs kill 2>/dev/null || true`
- Only ONE flow validator should manage the server lifecycle at a time

## Known Limitations
- macOS desktop notifications cannot be validated programmatically via tuistory; only the in-TUI bell indicator can be checked
- Audio capture (ambient/dictation) requires real microphone hardware; tests must mock audio input
- LLM model loading takes 5-30 seconds; tests with real models need longer timeouts
- In some automation sessions, `curl http://localhost:9849/...` may fail while `http://127.0.0.1:9849/...` succeeds; prefer `127.0.0.1` fallback when health checks are unexpectedly flaky
- tuistory may occasionally miss `Ctrl+<number>` tab shortcuts; use click-based tab switching as a fallback and record the workaround in evidence
- tuistory may not reliably distinguish `Ctrl+Shift+<key>` from `Ctrl+<key>` in some terminal sessions (notably account re-auth shortcuts); retry with compose unfocused and capture status-bar evidence if still ambiguous
- GitHub `POST /github/notifications/read-all` may be eventually consistent in UI/API snapshots; allow ~5 seconds and refresh once before final badge/empty-state assertions
- For reminder mutation corroboration (`complete`, `edit`, `delete`), create unique test titles and fetch fresh IDs immediately before API checks to avoid ambiguity from duplicate-title records
- For Reminders TUI mutation keys (`c`, `e`, `d`), blur/unfocus the compose input before sending action keys, otherwise keypresses may be consumed by compose and produce flaky outcomes
