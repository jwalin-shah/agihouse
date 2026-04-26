#!/usr/bin/env bash
# Dev launcher for an inbox worktree. Defaults to port 9850 so the primary
# ~/projects/inbox instance on 9849 keeps working uninterrupted.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
export INBOX_SERVER_PORT="${INBOX_SERVER_PORT:-9850}"
export INBOX_SERVER_URL="${INBOX_SERVER_URL:-http://127.0.0.1:${INBOX_SERVER_PORT}}"

cd "$ROOT_DIR"
exec uv run python "${1:-inbox.py}"
