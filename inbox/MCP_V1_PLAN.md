# Inbox MCP V1

## Goal

Expose a portable MCP tool layer for personal workflows while keeping account
connections and policy in this repo instead of inside any single assistant.

## Architecture

```text
ChatGPT or another MCP client
    -> public mcp_server.py (/mcp)
    -> private inbox_server.py (localhost only)
    -> Gmail, iMessage, Apple Notes, Apple Reminders
```

Calendar stays on the built-in ChatGPT Google connector for now.

## Security Model

- `inbox_server.py` stays private on `127.0.0.1:9849`
- `mcp_server.py` is the only public assistant-facing surface
- public MCP auth uses `INBOX_MCP_TOKEN` in v1
- internal inbox auth still uses `INBOX_SERVER_TOKEN`
- write tools are confirmation-gated with `confirm=True`
- destructive mail actions and deletes stay out of scope in v1

## V1 Tools

### Email

- `list_inbox_threads`
- `search_email`
- `get_email_thread`
- `send_email_reply`
- `archive_email_thread`
- `mark_email_read`

### iMessage

- `list_message_threads`
- `get_message_thread`
- `send_imessage`

### Notes

- `list_notes`
- `get_note`
- `append_daily_note`

### Reminders

- `list_reminders`
- `create_reminder`
- `complete_reminder`

### Memory

- `get_memory`
- `save_memory_note`
- `list_open_commitments`

## Memory Shape

Use small structured records instead of generic transcript memory.

Suggested types:

- `person_preference`
- `project`
- `commitment`
- `writing_preference`
- `workflow_rule`

Each memory entry stores:

- `memory_type`
- `subject`
- `content`
- `source`
- `confidence`
- `status`
- `expires_at`

## Run

1. Start the private inbox server:

```bash
uv run python inbox_server.py
```

2. Start the MCP gateway:

```bash
uv run python mcp_server.py
```

3. Expose the MCP gateway over HTTPS:

```bash
ngrok http 8000
```

4. In ChatGPT web developer mode, create a connector pointing at:

```text
https://<your-subdomain>.ngrok.app/mcp
```

## Next Steps

- replace token auth with OAuth for ChatGPT connector auth
- add audit logging for tool calls
- add richer policy enforcement by tool class
- add account-aware Gmail prioritization
- add cross-app workflow tools
