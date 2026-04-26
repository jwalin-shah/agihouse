# Drive Tab

## Shipped
- Ctrl+8 switches to Drive tab (now all 8 tabs reachable via Ctrl+1-8).
- `DriveItem` widget: icon, filename, modified date, human-readable size, account tag (multi-account).
- Folder navigation: enter folder drills in, Backspace goes back up the stack.
- Upload (o to pick file), new folder (n), download (d), delete/trash (x), open URL (O).
- Search by filename.
- Empty folder state handled.

## API additions
- `GET /drive/files?folder_id=X` — list children of a specific folder.
- `GET /drive/files/{id}/download` — return file binary; Google Workspace docs auto-export to PDF.
- Client: `drive_list(folder_id=...)`, `drive_download(file_id)`.

## Notes
- Delete = trash (recoverable). Never hard-delete.
- Multi-account queries all authed accounts, routes mutations by `account` param.
- Google Workspace export map: Docs/Sheets/Slides/Drawings → PDF.
