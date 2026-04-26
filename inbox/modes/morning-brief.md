# Mode: morning-brief

Daily kickoff. Pull today's calendar, unread inbox, open reminders, and GitHub. Format as a focused brief — no raw dumps.

## Workflow

### 1. Calendar

```
GET /calendar/events?date=<today YYYY-MM-DD>
```

Show events as a timeline. Flag:
- Events starting within 2 hours → mark **soon**
- Events with no description and external attendees → mark **needs prep**
- Events with "confirm" / "RSVP" in title → mark **needs action**

### 2. Gmail Inbox

```
GET /conversations?source=gmail&limit=40
```

Filter to: `unread > 0` OR `last_ts` within 24h.
If `config/priorities.yml` exists, surface priority-sender threads first.

Group into three buckets:
- **Needs reply** — unread, human sender, not automated
- **Needs action** — receipts, confirmations, deadlines
- **FYI** — newsletters, notifications, automated mail

Show max 5 per bucket. If more exist, say "+ N more".

### 3. iMessage

```
GET /conversations?source=imessage&limit=20
```

Show unread threads only. Skip if none.

### 4. Reminders

```
GET /reminders?show_completed=false&limit=50
```

Filter to: due today or overdue. Skip if none.

### 5. GitHub

```
GET /github/notifications?all=false
```

Show unread only: PR review requests first, then issues, then other. Skip if none.

---

## Output Format

```
## Morning Brief — {weekday}, {date}

### Today
{time} {event title} [{soon|needs prep|needs action} if flagged]
...

### Inbox
**Needs reply ({N})**
- {name}: {snippet} [{account}]

**Needs action ({N})**
- {name}: {snippet}

**FYI** — {N} threads (skip listing unless ≤ 3)

### iMessage  ← skip section if none
- {name}: {snippet}

### Reminders  ← skip section if none
- [{list}] {title} — due {date}

### GitHub  ← skip section if none
- 🔀 {repo}: {title}
```

Keep total output under 60 lines. Cut low-signal items before expanding high-signal ones.
