# Gmail Upgrade

## Shipped actions (context-scoped to Gmail tab)
- **a** — archive (remove INBOX label)
- **d** — delete (move to trash)
- **s** — toggle star
- **r** — mark read
- **u** — mark unread
- **c** — compose new message (overlay: to/subject/body)
- **l** — cycle through labels
- **t** — download attachment from selected message

## API
- `POST /messages/gmail/{id}/archive|delete|star|unstar|read|unread`
- `POST /messages/compose  {to, subject, body, account}`
- `GET /gmail/labels` — list labels for the active account
- `GET /messages/gmail/{msg_id}/attachments/{att_id}` — download attachment as binary

## Attachments
- `services._extract_attachments` walks Gmail payload parts recursively, collects filename + attachmentId + size + mimeType.
- `Msg.attachments` is a list[dict]; TUI renders badges in thread view.
- Download decodes base64 from `messages().attachments().get(...).execute()`.

## Multi-account routing
- `_get_gmail_service(msg_id)` in `inbox_server.py` resolves the correct account's service by looking up the contact for that msg_id.
