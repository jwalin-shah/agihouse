#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_EXAMPLE="$ROOT_DIR/config/inbox.env.example"
ENV_FILE="$ROOT_DIR/config/inbox.env"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

BACKEND_PLIST_SRC="$ROOT_DIR/deploy/com.inbox.backend.plist.example"
MCP_PLIST_SRC="$ROOT_DIR/deploy/com.inbox.mcp.plist.example"
MCP_READONLY_PLIST_SRC="$ROOT_DIR/deploy/com.inbox.mcp-readonly.plist.example"

BACKEND_PLIST_DST="$LAUNCH_AGENTS_DIR/com.inbox.backend.plist"
MCP_PLIST_DST="$LAUNCH_AGENTS_DIR/com.inbox.mcp.plist"
MCP_READONLY_PLIST_DST="$LAUNCH_AGENTS_DIR/com.inbox.mcp-readonly.plist"

copy_env_template() {
  if [[ -f "$ENV_FILE" ]]; then
    echo "config/inbox.env already exists"
  else
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "created config/inbox.env from template"
  fi
}

install_launch_agent() {
  local src="$1"
  local dst="$2"

  mkdir -p "$LAUNCH_AGENTS_DIR"
  cp "$src" "$dst"
  launchctl unload "$dst" >/dev/null 2>&1 || true
  launchctl load "$dst"
  echo "loaded $(basename "$dst")"
}

print_next_steps() {
  cat <<EOF

Inbox MCP bootstrap complete.

Fill in secrets here before relying on the services:
  $ENV_FILE

Start or reload services later with:
  launchctl unload $BACKEND_PLIST_DST >/dev/null 2>&1 || true
  launchctl load $BACKEND_PLIST_DST
  launchctl unload $MCP_PLIST_DST >/dev/null 2>&1 || true
  launchctl load $MCP_PLIST_DST
  launchctl unload $MCP_READONLY_PLIST_DST >/dev/null 2>&1 || true
  launchctl load $MCP_READONLY_PLIST_DST

Log files:
  /tmp/inbox-backend.out.log
  /tmp/inbox-backend.err.log
  /tmp/inbox-mcp.out.log
  /tmp/inbox-mcp.err.log
  /tmp/inbox-mcp-readonly.out.log
  /tmp/inbox-mcp-readonly.err.log

Client config snippets:

Claude Code:
  Repo config already added at:
    $ROOT_DIR/.mcp.json

Cursor:
  Repo config already added at:
    $ROOT_DIR/.cursor/mcp.json

Gemini CLI:
  Merge this into ~/.gemini/settings.json:
    $ROOT_DIR/config/gemini-settings.inbox.example.json

Codex:
  Sync the MCP sections from:
    $ROOT_DIR/config/codex.inbox.example.toml
  Keep INBOX_SERVER_TOKEN in your shell environment or secret store rather than
  embedding it in ~/.codex/config.toml.

Remote safer endpoint:
  Expose the read-only MCP service first via Caddy:
    $ROOT_DIR/deploy/Caddyfile.example

EOF
}

main() {
  copy_env_template

  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "launchd bootstrap only runs on macOS"
    print_next_steps
    exit 0
  fi

  install_launch_agent "$BACKEND_PLIST_SRC" "$BACKEND_PLIST_DST"
  install_launch_agent "$MCP_PLIST_SRC" "$MCP_PLIST_DST"
  install_launch_agent "$MCP_READONLY_PLIST_SRC" "$MCP_READONLY_PLIST_DST"

  print_next_steps
}

main "$@"
