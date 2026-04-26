# Mode: batch-archive

Bulk archive threads from `batch/archive-input.tsv` with resumable TSV state.

## Workflow

### 1. Check Input

Read `batch/archive-input.tsv`. If missing or empty:
> "No archive input found. To populate it:
> 1. Run `/inbox triage` first
> 2. Copy score ≤2 rows from `batch/triage-output.tsv` to `batch/archive-input.tsv`
> 3. Or add thread IDs manually (see format below)"

Format of `archive-input.tsv`:
```
thread_id\tsource\tnotes
```

### 2. Check State

Read `batch/archive-state.tsv`. Columns:
```
thread_id\tsource\tstatus\tarchived_at\terror\tretries
```

Status values: `pending` | `completed` | `failed`

By default: only process `pending` rows.
With `--retry-failed`: also process `failed` rows with retries < 3.

### 3. Dry Run Confirmation

Before archiving, show:
```
Ready to archive N threads:
  source: gmail ({count}), imessage ({count})
  Proceed? [y/N]
```

### 4. Archive Each Thread

For Gmail threads:
```
POST /gmail/batch-modify
{"msg_ids": [thread_id], "add_label_ids": ["ARCHIVED"], "remove_label_ids": ["INBOX"]}
```

Update `archive-state.tsv` row: `completed` + timestamp. On error: `failed` + error message.

### 5. Summary

```
Archived {N} threads. {M} failed — see batch/archive-state.tsv.
```
