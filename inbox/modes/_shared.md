# Inbox — Shared Context

Loaded by every inbox mode. Do not repeat this in mode files.

## Server

- Base URL: `http://localhost:9849` (override via `INBOX_SERVER_URL`)
- Auth: `Authorization: Bearer $INBOX_SERVER_TOKEN` if env var is set
- Health: `GET /health` → `{"status": "ok"}`

## Key Endpoints

```
GET  /conversations?source=all|imessage|gmail&limit=N
GET  /messages/{source}/{conv_id}?thread_id=...
GET  /calendar/events?date=YYYY-MM-DD
GET  /reminders?show_completed=false&limit=100
GET  /github/notifications?all=false
GET  /accounts
POST /search  {"q": str, "sources": [str], "limit": int}
POST /gmail/batch-modify  {"msg_ids": [str], "add_label_ids": [str], "remove_label_ids": [str]}
```

## Conversation Fields

```
id          — conv_id for follow-on calls
name        — contact name or email
source      — "gmail" | "imessage"
snippet     — last message preview
unread      — unread message count
last_ts     — ISO timestamp of last message
thread_id   — Gmail thread ID (Gmail only)
gmail_account — which Google account owns this thread
```

## Priorities (user config)

If `config/priorities.yml` exists, load it before scoring any threads.
Priority senders and keywords should boost urgency score by +1.

## Output Conventions

- Tables: use markdown with aligned columns
- TSV output: write to `batch/` dir, tab-separated, header row always included
- Scores: 1 (ignore) to 5 (urgent), integer only
- Actions: `reply` | `track` | `archive` | `ignore`
