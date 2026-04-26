# Mode: followup-sweep

Find threads where you owe a reply or are waiting on someone. For reply-owed threads, draft an opener.

## Workflow

### 1. Load Triage Output

Check `batch/triage-output.tsv`:
- If exists and modified within 6 hours: use it
- Otherwise: run triage inline (fetch conversations + score, do not write TSV)

### 2. Partition

From scored threads:

**Needs reply (action=reply, score ≥3):**
For each: fetch last 3 messages to confirm you didn't already reply.
```
GET /messages/{source}/{conv_id}?thread_id={thread_id}
```
Check: is the last message from you? If yes, reclassify as `track`.

**Waiting on (action=track, score ≥3):**
Threads where you sent last and no response yet. Note age.

### 3. Draft Reply Starters

For each confirmed needs-reply thread (max 5):
- Read last message body
- Draft a 1-sentence reply opener that fits the context
- Do not draft full replies — just the opener

### 4. Output

```
## Followup Sweep — {date}

### Needs Reply ({N})
| Name           | Last message           | Age    | Draft opener                        |
|----------------|------------------------|--------|-------------------------------------|
| Tierra Hall    | "Can you confirm..."   | 3d ago | "Thanks for following up — yes..." |

### Waiting On ({N})
| Name        | Sent              | Age    |
|-------------|-------------------|--------|
| Adam Chan   | "Let me know if..." | 5d ago |

### All Clear
{list any remaining threads where action is needed but score < 3, as a compact list}
```

If no needs-reply threads: say "Inbox clear — no threads waiting on your reply."
