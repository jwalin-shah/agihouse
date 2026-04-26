# Google Sheets Integration — Quick Start for Agents

Inbox now has **full Google Sheets API access** for agents. Create, read, update, and delete spreadsheets with formulas, formatting, and multi-sheet support.

## Setup (One-Time)

1. **Start the server** (if not already running):
   ```bash
   cd ~/projects/inbox
   uv run python inbox_server.py
   ```

2. **Re-authenticate** to grant Sheets access (required once):
   ```bash
   curl -X POST http://localhost:9849/accounts/reauth \
     -H 'Content-Type: application/json' \
     -d '{"email": "your-email@gmail.com"}'
   ```
   Or in the TUI: Press `Ctrl+Shift+A`

3. **Verify**:
   ```bash
   curl http://localhost:9849/sheets
   ```

## 30-Second Examples

### Create a spreadsheet
```bash
SHEET=$(curl -s -X POST http://localhost:9849/sheets \
  -H 'Content-Type: application/json' \
  -d '{"title": "My Data", "sheets": ["Data", "Summary"]}' | jq -r '.id')

echo "Created: $SHEET"
```

### Write data (including formulas)
```bash
curl -X PUT http://localhost:9849/sheets/$SHEET/values/A1:C5 \
  -H 'Content-Type: application/json' \
  -d '{
    "values": [
      ["Item", "Price", "Tax"],
      ["Apple", 10, "=B2*0.1"],
      ["Orange", 8, "=B3*0.1"]
    ],
    "value_input": "USER_ENTERED"
  }'
```

### Read data back
```bash
curl http://localhost:9849/sheets/$SHEET/values/A1:C5 | jq
```

### Append rows
```bash
curl -X POST http://localhost:9849/sheets/$SHEET/values/A1:C1/append \
  -H 'Content-Type: application/json' \
  -d '{"values": [["Banana", 12, "=B4*0.1"]]}'
```

### Add a new tab
```bash
curl -X POST http://localhost:9849/sheets/$SHEET/tabs \
  -H 'Content-Type: application/json' \
  -d '{"title": "Archive", "rows": 500, "cols": 20}'
```

## Common Use Cases

### Log ongoing data to a sheet
```bash
# Append a timestamped event
curl -X POST http://localhost:9849/sheets/$SHEET/values/A:A/append \
  -H 'Content-Type: application/json' \
  -d '{"values": [["'$(date)': Task completed"]]}'
```

### Build a report with multiple sections
```bash
curl -X POST http://localhost:9849/sheets/$SHEET/values/batch-update \
  -H 'Content-Type: application/json' \
  -d '{
    "data": [
      {"range": "Summary!A1", "values": [["Daily Report"]]},
      {"range": "Summary!A2", "values": [["Generated: =TODAY()"]]},
      {"range": "Data!A1:B3", "values": [["Count", "=COUNTA(Archive!A:A)"]]}
    ],
    "value_input": "USER_ENTERED"
  }'
```

### Track metrics across days
```bash
# Create new tab per day, log data
curl -X POST http://localhost:9849/sheets/$SHEET/tabs \
  -H 'Content-Type: application/json' \
  -d '{"title": "2026-04-14"}'

curl -X PUT http://localhost:9849/sheets/$SHEET/values/'2026-04-14'!A1 \
  -H 'Content-Type: application/json' \
  -d '{"values": [["Morning Tasks", 12], ["Evening Tasks", 8]]}'
```

## API Cheat Sheet

| Task | Endpoint | Method |
|------|----------|--------|
| List spreadsheets | `/sheets` | GET |
| Create spreadsheet | `/sheets` | POST |
| Get metadata | `/sheets/{id}` | GET |
| Delete spreadsheet | `/sheets/{id}` | DELETE |
| Read range | `/sheets/{id}/values/A1:D10` | GET |
| Write range | `/sheets/{id}/values/A1:B3` | PUT |
| Append rows | `/sheets/{id}/values/A1:A1/append` | POST |
| Clear range | `/sheets/{id}/values/A1:D10` | DELETE |
| Read multiple ranges | `/sheets/{id}/values/batch-get` | POST |
| Write multiple ranges | `/sheets/{id}/values/batch-update` | POST |
| Add tab | `/sheets/{id}/tabs` | POST |
| Delete tab | `/sheets/{id}/tabs/{sheet_id}` | DELETE |
| Rename tab | `/sheets/{id}/tabs/{sheet_id}?title=New` | PATCH |
| Copy tab | `/sheets/{id}/tabs/{sheet_id}/copy` | POST |
| Format cells | `/sheets/{id}/format` | POST |

## Key Concepts

- **A1 Notation**: Specify ranges as `A1`, `A1:D10`, or `Sheet2!A1:B5`
- **Formulas**: Use `value_input: "USER_ENTERED"` to parse formulas (e.g., `=A1+B1`)
- **Accounts**: All operations default to first available account. Specify `?account=email@gmail.com` to choose
- **Multi-range ops**: Use `batch-get` and `batch-update` to read/write multiple ranges in one request
- **Raw requests**: Use `/format` endpoint with raw Google Sheets API `batchUpdate` requests for advanced formatting

## Range Examples

```
A1              # Single cell
A1:D10          # Rectangle
A:A             # Entire column
1:1             # Entire row
Sheet2!A1:B5    # Named sheet
'My Sheet'!A1   # Sheet with spaces (use quotes)
```

## Troubleshooting

**"No Sheets account available"**
- Call re-auth endpoint: `POST /accounts/reauth` with your email
- Check that OAuth tokens exist in `tokens/`

**"Failed to create spreadsheet"**
- Verify Google OAuth is working: `curl http://localhost:9849/accounts`
- Check if `credentials.json` exists with valid client secrets

**"Invalid range"**
- A1 notation is case-sensitive (A1, not a1)
- Use quotes for sheet names with spaces: `'My Sheet'!A1`
- Ranges must be valid (e.g., `A1:B10` not `A1:10`)

**Formulas not evaluating**
- Use `value_input: "USER_ENTERED"` (not `RAW`)
- Keep formula syntax: `=A1+B1`, not `A1+B1`

## Performance Tips

- Use batch operations (`batch-get`, `batch-update`) instead of multiple single requests
- Limit range size when reading (Google API can be slow on 10k+ rows)
- Batch append multiple rows at once instead of one-at-a-time
- Cache spreadsheet IDs to avoid redundant list calls

## Full Documentation

For complete API reference, request/response examples, and advanced usage, see:
- `SHEETS.md` — Full agent API guide with examples
- `SHEETS_CHANGELOG.md` — What's new, design notes, migration info
- `CLAUDE.md` — Project architecture and Sheets section

## Support

If you need help:
1. Check error message in server logs: `uv run python inbox_server.py`
2. Verify auth: `curl http://localhost:9849/accounts`
3. Test manually: `curl http://localhost:9849/sheets`
4. See `SHEETS.md` for detailed examples and error handling
