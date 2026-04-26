"""
MCP gateway for Inbox.

This server is the public assistant-facing layer. It talks to the private
local inbox REST server and exposes a smaller, safer tool surface over MCP.

Run:
    uv run python mcp_server.py

Environment:
    INBOX_MCP_TOKEN      Optional bearer token for the public MCP endpoint.
    INBOX_SERVER_URL     Private inbox server URL (defaults to localhost:9849).
    INBOX_SERVER_TOKEN   Bearer token for the private inbox server, if enabled.
    INBOX_MEMORY_DB      Optional path for the local memory store sqlite file.
"""

from __future__ import annotations

import os
from pathlib import Path
from secrets import compare_digest

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

import ambient_notes
from mcp_backend import InboxBackend, InboxBackendError
from memory_store import MemoryStore
from tools_registry import register_all as _register_registry_tools

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - runtime-only path
    raise RuntimeError(
        "The 'mcp' package is required to run mcp_server.py. Install dependencies with: uv sync"
    ) from exc


MCP_TOKEN_ENV = "INBOX_MCP_TOKEN"  # nosec: B105 - env var name, not a hardcoded credential
MEMORY_DB_ENV = "INBOX_MEMORY_DB"


backend = InboxBackend()
memory_store = MemoryStore(
    Path(os.getenv(MEMORY_DB_ENV, "")).expanduser() if os.getenv(MEMORY_DB_ENV) else None
)
mcp = FastMCP(
    "Inbox Personal Assistant",
    stateless_http=True,
    json_response=True,
)


def _require_confirmation(confirm: bool, action: str) -> None:
    if not confirm:
        raise ValueError(
            f"{action} requires explicit confirmation. Retry with confirm=True after user approval."
        )


# Hand-written @mcp.tool defs for HTTP-backed capabilities were migrated to
# tools_registry.TOOLS. Remaining hand-written defs below are for non-HTTP
# integrations (ambient notes, memory_store).


def _public_token() -> str:
    return os.getenv(MCP_TOKEN_ENV, "").strip()


def _is_publicly_authorized(request: Request) -> bool:
    token = _public_token()
    if not token:
        return True

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        provided = auth_header[7:].strip()
        return bool(provided) and compare_digest(provided, token)
    return False


class PublicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if not _is_publicly_authorized(request):
            return JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


async def health(_request: Request) -> JSONResponse:
    try:
        backend_health = await backend.health()
    except InboxBackendError as exc:
        backend_health = {"status": "error", "detail": str(exc)}

    return JSONResponse(
        {
            "status": "ok",
            "mcp_path": "/mcp",
            "backend": backend_health,
            "memory_db": str(memory_store.db_path),
            "auth_enabled": bool(_public_token()),
        }
    )


@mcp.tool()
async def append_daily_note(content: str, confirm: bool = False) -> dict:
    """Append content to today's daily note in the Obsidian vault."""
    _require_confirmation(confirm, "append_daily_note")
    ambient_notes.append_to_daily(content)
    return {"ok": True, "date": str(ambient_notes._today_file().stem)}


@mcp.tool()
async def get_memory(
    query: str = "",
    memory_type: str = "",
    subject: str = "",
    status: str = "",
    limit: int = 10,
) -> list[dict]:
    """Retrieve structured memory entries for people, projects, preferences, or commitments."""
    return memory_store.query_entries(
        query=query,
        memory_type=memory_type,
        subject=subject,
        status=status,
        limit=limit,
    )


@mcp.tool()
async def save_memory_note(
    memory_type: str,
    subject: str,
    content: str,
    confirm: bool = False,
    source: str = "chat",
    confidence: float = 0.8,
    status: str = "active",
    expires_at: str = "",
) -> dict:
    """Save a structured memory note. This tool is confirmation-gated."""
    _require_confirmation(confirm, "save_memory_note")
    return memory_store.save_entry(
        memory_type=memory_type,
        subject=subject,
        content=content,
        source=source,
        confidence=confidence,
        status=status,
        expires_at=expires_at or None,
    )


@mcp.tool()
async def list_open_commitments(limit: int = 25) -> list[dict]:
    """List open commitment memory entries."""
    return memory_store.list_open_commitments(limit=limit)


@mcp.tool()
async def update_memory(
    entry_id: int,
    confirm: bool = False,
    subject: str | None = None,
    content: str | None = None,
    status: str | None = None,
    confidence: float | None = None,
) -> dict:
    """Update a memory entry. This tool is confirmation-gated."""
    _require_confirmation(confirm, "update_memory")
    kwargs = {}
    if subject is not None:
        kwargs["subject"] = subject
    if content is not None:
        kwargs["content"] = content
    if status is not None:
        kwargs["status"] = status
    if confidence is not None:
        kwargs["confidence"] = confidence
    return memory_store.update_entry(entry_id, **kwargs)


@mcp.tool()
async def close_commitment(entry_id: int, confirm: bool = False) -> dict:
    """Close a commitment (set status to 'closed'). This tool is confirmation-gated."""
    _require_confirmation(confirm, "close_commitment")
    return memory_store.close_commitment(entry_id)


_register_registry_tools(mcp, backend, readonly_only=False)


app = Starlette(
    routes=[
        Route("/health", endpoint=health),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ],
    middleware=[Middleware(PublicAuthMiddleware)],
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
