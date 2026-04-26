# Google Sheets API — Agent Guide

Quick reference for agents accessing Google Sheets through the inbox server API.

## Base URL
```
http://localhost:9849
```

## Authentication
Add header if `INBOX_SERVER_TOKEN` is configured:
```
Authorization: Bearer <token>
```

## Quick Start

### List all spreadsheets
```bash
curl http://localhost:9849/sheets
```

### Create a new spreadsheet
```bash
curl -X POST http://localhost:9849/sheets \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Q2 Planning",
    "sheets": ["Tasks", "Timeline", "Budget"]
  }'
```

### Read a range
```bash
curl http://localhost:9849/sheets/abc123/values/A1:D10
```

### Write to a range
```bash
curl -X PUT http://localhost:9849/sheets/abc123/values/A1:B3 \
  -H 'Content-Type: application/json' \
  -d '{
    "values": [
      ["Name", "Score"],
      ["Alice", 95],
      ["Bob", 87]
    ]
  }'
```

### Append rows
```bash
curl -X POST http://localhost:9849/sheets/abc123/values/A1:B2/append \
  -H 'Content-Type: application/json' \
  -d '{
    "values": [
      ["Charlie", 92],
      ["Diana", 88]
    ]
  }'
```

## API Endpoints

### Spreadsheet CRUD

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/sheets` | List all spreadsheets (query: `q`, `limit`, `account`) |
| `POST` | `/sheets` | Create new spreadsheet (body: `title`, `sheets[]`, `account`) |
| `GET` | `/sheets/{id}` | Get spreadsheet metadata + sheet tabs |
| `DELETE` | `/sheets/{id}` | Trash spreadsheet |

### Range Operations

All range operations use **A1 notation** (e.g., `A1`, `A1:D10`, `Sheet2!A1:B5`).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/sheets/{id}/values/{range}` | Read range |
| `PUT` | `/sheets/{id}/values/{range}` | Write range (overwrites) |
| `POST` | `/sheets/{id}/values/{range}/append` | Append rows |
| `DELETE` | `/sheets/{id}/values/{range}` | Clear range |
| `POST` | `/sheets/{id}/values/batch-get` | Read multiple ranges (body: `ranges[]`) |
| `POST` | `/sheets/{id}/values/batch-update` | Write multiple ranges (body: `data[]`, `value_input`) |

### Sheet Tab Management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/sheets/{id}/tabs` | Add new sheet tab (body: `title`, `rows=1000`, `cols=26`, `account`) |
| `DELETE` | `/sheets/{id}/tabs/{sheet_id}` | Delete sheet tab |
| `PATCH` | `/sheets/{id}/tabs/{sheet_id}` | Rename sheet tab (query: `title`) |
| `POST` | `/sheets/{id}/tabs/{sheet_id}/copy` | Copy tab to another spreadsheet (body: `dest_spreadsheet_id`) |

### Advanced

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/sheets/{id}/format` | Apply formatting via raw batchUpdate requests |

## Request/Response Examples

### Create spreadsheet with multiple tabs
```json
POST /sheets
{
  "title": "Project Plan",
  "sheets": ["Overview", "Timeline", "Resources", "Budget"],
  "account": "user@gmail.com"
}
```

Response:
```json
{
  "id": "1abc2def3ghi4jkl5mno6pqr",
  "title": "Project Plan",
  "url": "https://docs.google.com/spreadsheets/d/1abc.../edit",
  "sheets": [
    {"sheet_id": 0, "title": "Overview", "index": 0, "row_count": 1000, "col_count": 26},
    {"sheet_id": 123, "title": "Timeline", "index": 1, "row_count": 1000, "col_count": 26},
    ...
  ],
  "account": "user@gmail.com"
}
```

### Write with formulas (USER_ENTERED mode)
```json
PUT /sheets/{id}/values/A1:C5
{
  "values": [
    ["Item", "Qty", "Total"],
    ["Apple", 10, "=B2*5"],
    ["Orange", 8, "=B3*3"],
    ["Banana", 15, "=B4*2"]
  ],
  "value_input": "USER_ENTERED"
}
```

### Batch read multiple ranges
```json
POST /sheets/{id}/values/batch-get
{
  "ranges": ["A1:A10", "C1:C10", "Summary!A1:B5"]
}
```

Response:
```json
{
  "A1:A10": [["Name"], ["Alice"], ["Bob"], ...],
  "C1:C10": [["Score"], [95], [87], ...],
  "Summary!A1:B5": [["Total", 500], ...]
}
```

### Apply cell formatting (borders, bold, colors)
```json
POST /sheets/{id}/format
{
  "requests": [
    {
      "repeatCell": {
        "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {
          "userEnteredFormat": {
            "textFormat": {"bold": true, "fontSize": 12},
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
          }
        },
        "fields": "userEnteredFormat"
      }
    }
  ]
}
```

## Multi-Account

For multi-account setups, specify `account` as a query parameter (single-account operations) or in the request body:

```bash
# List spreadsheets from specific account
curl http://localhost:9849/sheets?account=alice@gmail.com

# Write from specific account
curl -X PUT http://localhost:9849/sheets/abc123/values/A1 \
  -H 'Content-Type: application/json' \
  -d '{"values": [["test"]], "account": "bob@gmail.com"}'
```

If `account` is omitted, the first available account is used.

## Value Input Options

When writing (`PUT`, `POST /append`), specify `value_input`:

- **`RAW`** (default) — values are stored as-is (strings, numbers)
- **`USER_ENTERED`** — formulas (starting with `=`) are parsed; numbers/dates are converted; strings are stored as-is

```json
// RAW: "=A1+B1" stored as text string
{"values": [["=A1+B1"]], "value_input": "RAW"}

// USER_ENTERED: "=A1+B1" evaluated as formula
{"values": [["=A1+B1"]], "value_input": "USER_ENTERED"}
```

## Common Patterns

### Log data to a tracking sheet
```bash
curl -X POST http://localhost:9849/sheets/tracking-id/values/A1:A1/append \
  -H 'Content-Type: application/json' \
  -d '{"values": [["2026-04-14 10:30: Event occurred"]]}'
```

### Build a report (write multiple ranges)
```bash
curl -X POST http://localhost:9849/sheets/report-id/values/batch-update \
  -H 'Content-Type: application/json' \
  -d '{
    "data": [
      {"range": "Summary!A1", "values": [["Report Generated: 2026-04-14"]]},
      {"range": "Data!A1:C3", "values": [["Col1", "Col2", "Col3"], [1, 2, 3], [4, 5, 6]]},
      {"range": "Summary!B1", "values": [["Total: =COUNTA(Data!A:A)"]]}
    ],
    "value_input": "USER_ENTERED"
  }'
```

### Manage tabs dynamically
```bash
# Add a new tab
curl -X POST http://localhost:9849/sheets/abc123/tabs \
  -H 'Content-Type: application/json' \
  -d '{"title": "Archive", "rows": 500, "cols": 20}'

# Rename a tab
curl -X PATCH http://localhost:9849/sheets/abc123/tabs/456?title=Archive-2026

# Delete a tab
curl -X DELETE http://localhost:9849/sheets/abc123/tabs/789
```

## Error Handling

All endpoints return standard HTTP status codes:

- `200 OK` — operation succeeded
- `400 Bad Request` — invalid input (missing fields, invalid range, etc.)
- `404 Not Found` — spreadsheet/range not found or no accounts available
- `500 Internal Server Error` — API call failed (check server logs)

Error response format:
```json
{
  "detail": "Failed to update range: Invalid range"
}
```

## Rate Limiting & Performance

- Google Sheets API allows ~300 requests/min per user
- Batch operations (batch-get, batch-update) count as single requests
- Use batch operations when possible to reduce request count
- Reading large ranges (>10k cells) may take several seconds

## Re-authentication

If you add a new Google account or encounter auth errors:

```bash
curl -X POST http://localhost:9849/accounts/reauth \
  -H 'Content-Type: application/json' \
  -d '{"email": "user@gmail.com"}'
```

This opens an OAuth browser flow and updates the token with new scopes (including `spreadsheets`).
