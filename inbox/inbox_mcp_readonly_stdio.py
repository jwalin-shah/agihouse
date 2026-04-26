"""
Local stdio entrypoint for the read-only Inbox MCP surface.
"""

from __future__ import annotations

from inbox_mcp_readonly import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
