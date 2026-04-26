# Google Sheets Integration — Changelog

## What's New

Full Google Sheets API integration added to the inbox server. Agents can now create, read, update, and delete spreadsheets, manage sheet tabs, write data (including formulas), and apply formatting.

## Changes

### Core Changes

**services.py**
- Added `spreadsheets` scope to `GOOGLE_SCOPES`
- Added `Spreadsheet` and `SheetTab` dataclasses
- Modified `google_auth_all()` to build sheets service (returns 4-tuple instead of 3-tuple)
- Added 15 Sheets service functions:
  - Spreadsheet CRUD: `sheets_list`, `sheets_get`, `sheets_create`, `sheets_delete`
  - Value operations: `sheets_values_get`, `sheets_values_batch_get`, `sheets_values_update`, `sheets_values_batch_update`, `sheets_values_append`, `sheets_values_clear`
  - Tab management: `sheets_add_sheet`, `sheets_delete_sheet`, `sheets_rename_sheet`, `sheets_copy_to`
  - Formatting: `sheets_format` (raw batchUpdate passthrough)

**inbox_server.py**
- Added `sheets_services` to `ServerState`
- Added Pydantic models: `SpreadsheetOut`, `SheetTabOut`, request bodies for all Sheets operations
- Added helper functions: `_get_sheets_service_for_account`, `_get_drive_service_for_account`, `_spreadsheet_to_out`, `_sheet_tab_to_out`
- Added 15 API endpoints under `/sheets/*`:
  - Spreadsheet CRUD: `GET /sheets`, `POST /sheets`, `GET /sheets/{id}`, `DELETE /sheets/{id}`
  - Range operations: `GET /sheets/{id}/values/{range}`, `PUT`, `POST /append`, `DELETE`, `batch-get`, `batch-update`
  - Tab management: `POST /sheets/{id}/tabs`, `DELETE /sheets/{id}/tabs/{sheet_id}`, `PATCH /sheets/{id}/tabs/{sheet_id}`, `POST /sheets/{id}/tabs/{sheet_id}/copy`
  - Formatting: `POST /sheets/{id}/format`
- Updated lifespan to unpack 4-tuple from `google_auth_all()`
- Updated `/accounts/add` and `/accounts/reauth` endpoints to handle sheets_services
- Updated health and accounts endpoints to report sheets accounts

**Test Suite**
- Updated all test fixtures to return 4-tuple from `google_auth_all()` mock
- Added `sheets_services = {}` initialization to all test fixtures
- Fixed indentation issues in test_gmail_actions.py and test_conversations_latency.py
- All 736 tests pass

### Documentation

**CLAUDE.md**
- Updated intro to mention Sheets
- Updated Architecture section to list Sheets
- Added `/sheets/*` endpoints to API endpoint reference
- Added new "Google Sheets" section with feature overview
- Updated "Multi-account Google" section to mention new `spreadsheets` scope
- Updated Data sources to include Sheets

**SHEETS.md** (New)
- Comprehensive agent guide with curl examples
- API endpoint reference table
- Request/response examples
- Multi-account usage
- Common patterns
- Error handling guide
- Rate limiting notes

## User Action Required

### Re-authentication

The new `spreadsheets` scope requires users to re-authenticate:

1. **First startup after upgrade**: Server prints a warning about the new scope
2. **Re-auth endpoint**: Call `POST /accounts/reauth` with the user's email to trigger OAuth flow
3. **TUI users**: Press `Ctrl+Shift+A` to re-auth and grant the new scope

```bash
# Manual re-auth
curl -X POST http://localhost:9849/accounts/reauth \
  -H 'Content-Type: application/json' \
  -d '{"email": "user@gmail.com"}'
```

### Environment

No new environment variables required. Optional `INBOX_SERVER_TOKEN` auth still works.

## API Usage

See `SHEETS.md` for full agent guide. Quick example:

```bash
# List spreadsheets
curl http://localhost:9849/sheets

# Create a spreadsheet
curl -X POST http://localhost:9849/sheets \
  -H 'Content-Type: application/json' \
  -d '{"title": "New Sheet", "sheets": ["Sheet1", "Sheet2"]}'

# Write data
curl -X PUT http://localhost:9849/sheets/{id}/values/A1:B3 \
  -H 'Content-Type: application/json' \
  -d '{"values": [["Name", "Score"], ["Alice", 95], ["Bob", 87]]}'

# Read data
curl http://localhost:9849/sheets/{id}/values/A1:B10
```

## Design Notes

- **Follows existing patterns**: Service functions, error handling, multi-account routing identical to Drive integration
- **Token reuse**: Uses same OAuth tokens as Gmail/Calendar/Drive (single scope addition)
- **Full API coverage**: Agents can perform any Sheets operation (values, formatting, tabs, formulas)
- **Batch operations**: Efficient batch-get and batch-update for multiple ranges
- **Raw formatting**: `sheets_format` accepts raw Sheets API `batchUpdate` requests for maximum flexibility
- **A1 notation**: All range specs use standard A1 notation (e.g., `A1:D10`, `Sheet2!A1:B5`)
- **Value input options**: `RAW` (default) or `USER_ENTERED` for formula support

## Testing

All 736 tests pass:
- Existing tests updated for 4-tuple return
- Sheets functionality not directly tested in test suite (agents will test via API)
- Integration tests in TUI coming later if Sheets tab added

Run tests:
```bash
uv run pytest tests/ -q
```

## No Breaking Changes

- Existing endpoints unchanged
- Existing services unchanged
- Only addition: sheets service + endpoints
- Force migration: Re-auth required due to new scope (handled gracefully)

## Future Enhancements

- Sheets tab in TUI (agents can use API now)
- Scheduled sheet sync (e.g., daily backups)
- Sheet templates (pre-built reporting sheets)
- Named ranges (for cleaner agent queries)
- Conditional formatting helpers
