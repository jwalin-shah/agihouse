# Mode: triage

Score and prioritize inbox threads. Output a ranked table + write `batch/triage-output.tsv` for downstream batch ops.

## Workflow

### 1. Fetch Threads

```
GET /conversations?source=all&limit=50
```

If `config/priorities.yml` exists, load it now — priority senders/keywords used in scoring.

### 2. Score Each Thread

For each conversation, assign:

**Urgency (1–5):**
- 5 — human sender, unread, last message > 48h ago (stale needs-reply)
- 4 — human sender, unread, last message < 48h
- 3 — human sender, read, last message this week
- 2 — automated (receipts, notifications, confirmations)
- 1 — newsletters, marketing, noreply

Boost +1 if sender is in `config/priorities.yml` priority_senders.
Boost +1 if snippet matches a priority_keyword.
Cap at 5.

**Action:**
- `reply` — human thread, you haven't replied yet
- `track` — waiting on someone else, or FYI
- `archive` — automated, no action needed
- `ignore` — newsletter, promotional, bulk

**Category:** one of: `human` | `automated` | `newsletter` | `notification` | `receipt`

### 3. If 20+ Threads

Split into chunks of 15. Process each chunk, then merge results sorted by urgency desc.

### 4. Write TSV

Write `batch/triage-output.tsv`:
```
thread_id\tsource\tscore\tcategory\taction\tname\tsnippet\tlast_ts
```

Create `batch/` dir if it doesn't exist.

### 5. Display Table

```
## Triage — {N} threads scored

| Score | Action  | Name           | Snippet                    | Age    |
|-------|---------|----------------|----------------------------|--------|
| 5     | reply   | Tierra Hall    | Re: hardware TPM screen... | 2d ago |
...
```

Show score 4-5 first, then 3, then summarize 1-2 as counts only.
After table: "Wrote batch/triage-output.tsv — {N} threads. Run `/inbox batch` to archive score ≤1."
