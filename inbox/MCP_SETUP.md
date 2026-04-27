# Inbox MCP Setup

This repo now supports two MCP access patterns:

- Local `stdio` MCP for assistants running on the same machine
- HTTP MCP gateway for cloud agents or remote clients

Use both. They serve different jobs.

## Recommended Architecture

Run the private inbox backend and the MCP layers separately:

1. `inbox_server.py`
   - Private backend
   - Holds Gmail/Calendar/Drive tokens
   - Keep this on `127.0.0.1:9849`

2. `mcp_server.py`
   - Assistant-facing MCP HTTP gateway
   - Talks to the private backend using `INBOX_SERVER_URL` and `INBOX_SERVER_TOKEN`
   - Exposes only the curated MCP tool surface

3. `inbox_mcp_readonly.py`
   - Read-only MCP HTTP gateway
   - Intended for cloud agents and less-trusted clients
   - Runs separately from the full MCP gateway

4. `inbox_mcp_stdio.py`
   - Local subprocess MCP entrypoint
   - Reuses the same tool surface as `mcp_server.py`
   - Best for Cursor, Claude Code, Gemini CLI, and local Codex-style clients

5. `inbox_mcp_readonly_stdio.py`
   - Local subprocess entrypoint for the read-only tool surface
   - Useful if you want a second local MCP server with fewer capabilities

## Which Path To Use

Use local `stdio` when the assistant runs on the same machine as Inbox.

Use full HTTP MCP when:

- the agent runs in the cloud
- the assistant cannot spawn a local subprocess
- you want one stable remote MCP endpoint

Use read-only HTTP MCP when:

- you want cloud agents to search/read but not modify
- you want a lower-risk public endpoint
- you want to keep write tools available only to local or more trusted clients

You do not need to force local assistants through the remote HTTP path. Local
`stdio` is simpler, faster, and avoids unnecessary network and auth failure
modes.

Best practice:

- local assistants -> `stdio`
- cloud agents -> HTTP MCP

Both point to the same private Inbox backend.

## Primary vs Dev Testing

Use two separate backend targets when you want to test a worktree without disturbing daily use.

Recommended split:
- primary checkout: `~/projects/inbox` -> `http://127.0.0.1:9849`
- dev checkout: `~/projects/inbox-<topic>` -> `http://127.0.0.1:9850` by default

Start the dev backend from the worktree:

```bash
cd ~/projects/inbox-<topic>
./dev.sh inbox_server.py
```

Or run the full dev TUI + backend:

```bash
cd ~/projects/inbox-<topic>
./dev.sh
```

Override the default dev port when running multiple worktrees:

```bash
cd ~/projects/inbox-<topic>
INBOX_SERVER_PORT=9851 ./dev.sh inbox_server.py
```

### Critical Rule

Running the dev backend is not enough by itself.

Your MCP client must also point to the dev backend. Otherwise the assistant will silently keep talking to primary on `9849`.

For dev testing, always update:
- `cwd` to the worktree path
- `INBOX_SERVER_URL` to the dev backend URL
- `INBOX_SERVER_TOKEN` so MCP can still reach the private backend

Example dev Codex config:

```toml
[mcp_servers.inbox]
command = "uv"
args = ["run", "python", "inbox_mcp_stdio.py"]
cwd = "/Users/jwalinshah/projects/inbox-my-branch"

[mcp_servers.inbox.env]
INBOX_SERVER_URL = "http://127.0.0.1:9850"
INBOX_SERVER_TOKEN = "${INBOX_SERVER_TOKEN}"
```

Example dev Cursor/Claude-style env block:

```json
{
  "cwd": "/Users/jwalinshah/projects/inbox-my-branch",
  "env": {
    "INBOX_SERVER_URL": "http://127.0.0.1:9850",
    "INBOX_SERVER_TOKEN": "${INBOX_SERVER_TOKEN}"
  }
}
```

### Common Failure Mode

Symptoms:
- the dev server is running
- requests succeed
- but results match the primary environment

Cause:
- the MCP client is still pointed at `http://127.0.0.1:9849`

Check the client config before debugging the backend.

## Tokens

Use two different tokens at minimum.

- `INBOX_SERVER_TOKEN`
  - Protects the private Inbox REST API
  - Used by the MCP layer to reach `inbox_server.py`

- `INBOX_MCP_TOKEN`
  - Protects the public HTTP MCP gateway
  - Used by external MCP clients hitting `mcp_server.py` or `inbox_mcp_readonly.py`

Do not reuse the same token for both layers.

## Local Setup

Create your private env file from [config/inbox.env.example](config/inbox.env.example).

```bash
cp config/inbox.env.example config/inbox.env
```

Example:

```bash
export INBOX_SERVER_URL=http://127.0.0.1:9849
export INBOX_SERVER_TOKEN=replace-with-a-long-random-token
export INBOX_MCP_TOKEN=replace-with-a-different-long-random-token
```

Start the private backend:

```bash
./scripts/run_inbox_backend.sh
```

Start the full HTTP MCP gateway if you want remote/cloud access:

```bash
./scripts/run_inbox_mcp_http.sh
```

Start the read-only HTTP MCP gateway:

```bash
./scripts/run_inbox_mcp_http_readonly.sh
```

For local assistants that support `stdio`, point them at:

```bash
./scripts/run_inbox_mcp_stdio.sh
```

For local read-only usage:

```bash
./scripts/run_inbox_mcp_stdio_readonly.sh
```

Bootstrap the macOS services and create the local env file:

```bash
./scripts/setup_inbox_mcp.sh
```

## Client Configs

### Claude Code

This repo includes `.mcp.json` for project-local use.

It uses:

```json
{
  "mcpServers": {
    "inbox": {
      "command": "uv",
      "args": ["run", "python", "inbox_mcp_stdio.py"]
    }
  }
}
```

and also includes `inbox-readonly`.

### Cursor

This repo includes `.cursor/mcp.json` with both the full and read-only local `stdio` servers.

### Gemini CLI

Gemini CLI uses `~/.gemini/settings.json`.

An example snippet is provided in
[config/gemini-settings.inbox.example.json](config/gemini-settings.inbox.example.json).

### Codex

Codex should use the same local `stdio` command or the same remote MCP HTTP
endpoint, depending on where Codex is running.

An example snippet is provided in
[config/codex.inbox.example.toml](config/codex.inbox.example.toml).

## Cloud Agent Setup

If you want a cloud agent to use Inbox:

1. Keep `inbox_server.py` private
2. Expose `inbox_mcp_readonly.py` over HTTPS first
3. Expose `mcp_server.py` separately only if you truly need remote write operations
3. Put TLS and routing in front of it with Caddy or nginx
4. Require `INBOX_MCP_TOKEN`
5. Keep `INBOX_SERVER_TOKEN` private to the MCP host

Recommended topology:

```text
cloud agent
  -> https://inbox-mcp-readonly.your-domain.com/mcp
  -> inbox_mcp_readonly.py
  -> http://127.0.0.1:9849
  -> inbox_server.py
```

The backend should never be internet-facing.

Optional full-access topology:

```text
trusted remote client
  -> https://inbox-mcp.your-domain.com/mcp
  -> mcp_server.py
  -> http://127.0.0.1:9849
  -> inbox_server.py
```

## Reverse Proxy

An example Caddy config is in [deploy/Caddyfile.example](deploy/Caddyfile.example).

That config exposes:

- `/health`
- `/mcp`

and nothing else.

## Deployment Guidance

For a cloud VM or always-on host:

- run `inbox_server.py` as a service
- run `mcp_server.py` as a separate service if you need remote writes
- run `inbox_mcp_readonly.py` as a separate service for safer cloud access
- front `mcp_server.py` with Caddy
- front `inbox_mcp_readonly.py` with Caddy on a separate hostname
- bind `inbox_server.py` to loopback only
- store tokens in the service environment, not in repo files

Example service files are included for both `launchd` and `systemd` under `deploy/`.

## Security Notes

- Treat the host running `inbox_server.py` as sensitive because it holds your
  Gmail and Google Workspace OAuth tokens
- Expose MCP, not the raw inbox REST API
- Prefer the read-only MCP surface for cloud agents first
- Keep destructive tools confirmation-gated
- Add IP allowlisting if your deployment path supports it

## What To Use Locally vs Remotely

Use full `stdio` locally when you trust the client and want the best ergonomics.

Use read-only `stdio` locally when you want fewer tools available.

Use read-only HTTP MCP remotely by default.

Use full HTTP MCP remotely only for trusted clients and only if you truly need
remote writes.

Do not force local assistants through the public path unless you specifically want
to test the remote deployment.
