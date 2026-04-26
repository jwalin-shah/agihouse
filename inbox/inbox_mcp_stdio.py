"""
Local stdio MCP entrypoint for Inbox.

Use this for local MCP-capable clients like Claude Code, Cursor, Gemini CLI,
and any other client that prefers spawning a subprocess over calling the HTTP
MCP gateway directly.

The tool surface is defined in mcp_server.py; this file only changes transport.
"""

from __future__ import annotations

from mcp_server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
