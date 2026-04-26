# AI Layer

## What this milestone added

Six AI features built on top of the existing 0.8B Qwen MLX infrastructure.

## Architecture

### Dual-model infrastructure (services.py)

```
Small model (existing): mlx-community/Qwen3.5-0.8B-MLX-4bit
  - Already loaded as _llm_model/_llm_tokenizer
  - Used for: autocomplete, action extraction fallback

Large model (new): configurable via INBOX_LLM_LARGE env var
  - Default: mlx-community/Qwen2.5-3B-Instruct-4bit
  - Lazy-loaded on first use; _ensure_large_llm_loaded() returns bool
  - Used for: briefing summary, triage, summarization, action extraction (preferred)
  - Degrades gracefully — all features work without it
```

### Singleton pattern for large model

```python
_llm_large_model: object | None = None
_llm_large_tokenizer: object | None = None
_llm_large_loading: bool = False

def _ensure_large_llm_loaded() -> bool: ...   # returns False if unavailable
def llm_large_complete(...) -> str | None: ... # returns None if unavailable
def get_large_outlines_model() -> object | None: ...  # returns None if unavailable
```

## API Endpoints (all new)

| Endpoint | Description |
|---|---|
| `GET /llm/status` | Extended to return `{small: {...}, large: {..., loading}}` |
| `POST /ai/briefing` | Compile today's calendar/reminders/unread counts + LLM summary |
| `POST /ai/triage` | `{conversations: [...]}` → `{id: "urgent"|"normal"|"low"}` |
| `POST /ai/summarize` | `{thread_id, messages}` → `{summary, key_points, action_items, decisions}` |
| `POST /ai/extract-actions` | `{text}` → `{actions: [{text, deadline, type}]}` |

## TUI additions (inbox.py)

- `BriefingModal` — ModalScreen for Ctrl+B briefing overlay
- `Ctrl+B` binding → `action_morning_briefing` → `_do_fetch_briefing` worker
- `ConversationItem` — priority indicator (🔴 urgent / ⚪ normal / 🔵 low) via `_priority` field
- `MessageView.ai_summary` reactive — shows summary banner for Gmail threads with 5+ messages
- Triage fires after every `_populate` call (fire-and-forget worker, caps at 20 convos)
- Summarization auto-triggers when viewing Gmail thread with 5+ messages
- Action extraction fires on last message body when thread is loaded (100+ char body)

## Graceful degradation

- No large model → triage defaults all to "normal", briefing has no summary, summarization returns empty fields
- No small model → action extraction returns empty, autocomplete returns None
- Each feature catches all exceptions and returns sensible empty defaults

## Key design decisions

- **Fire-and-forget triage** — `_do_triage_conversations` runs in thread worker, never blocks UI
- **Summary cache** — `_thread_summaries` dict prevents redundant API calls per session
- **Action cache** — `_message_actions` dict keyed by conv_id or body hash
- **Summarization threshold** — only Gmail threads with 5+ messages get summarized (meaningful signal)
- **Briefing endpoint collects data server-side** — client just calls `POST /ai/briefing`, no pre-collection needed

## Testing strategy

All AI features are mocked via `patch.object(services, "llm_large_complete", ...)` or
`patch.object(services, "generate_json_large", ...)` — no real model loads in CI.
Large model path tests verify graceful degradation (return None/defaults when mock returns None).
