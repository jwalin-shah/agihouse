"""
Read-only MCP surface for Inbox.

Use this for cloud agents or less-trusted clients that should be able to search
and read data but not mutate inbox state.
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
        "The 'mcp' package is required to run inbox_mcp_readonly.py. Install dependencies with: uv sync"
    ) from exc


MCP_TOKEN_ENV = "INBOX_MCP_TOKEN"  # nosec: B105 - env var name, not a hardcoded credential
MEMORY_DB_ENV = "INBOX_MEMORY_DB"
DEFAULT_HTTP_PORT = 8001


backend = InboxBackend()
memory_store = MemoryStore(
    Path(os.getenv(MEMORY_DB_ENV, "")).expanduser() if os.getenv(MEMORY_DB_ENV) else None
)
mcp = FastMCP(
    "Inbox Personal Assistant (Read Only)",
    stateless_http=True,
    json_response=True,
)


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
            "mode": "readonly",
            "mcp_path": "/mcp",
            "backend": backend_health,
            "memory_db": str(memory_store.db_path),
            "auth_enabled": bool(_public_token()),
        }
    )


@mcp.tool()
async def read_daily_note(date: str = "") -> dict:
    """Read today's daily note or a specific YYYY-MM-DD note if present."""
    path = ambient_notes._today_file() if not date else ambient_notes.VAULT_DIR / f"{date}.md"
    if not path.exists():
        return {"ok": False, "path": str(path), "content": ""}
    return {"ok": True, "path": str(path), "content": path.read_text(encoding="utf-8")}


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
async def list_open_commitments(limit: int = 25) -> list[dict]:
    """List open commitment memory entries."""
    return memory_store.list_open_commitments(limit=limit)


_register_registry_tools(mcp, backend, readonly_only=True)


app = Starlette(
    routes=[
        Route("/health", endpoint=health),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ],
    middleware=[Middleware(PublicAuthMiddleware)],
)


def main() -> None:
    import uvicorn

    port = int(os.getenv("INBOX_MCP_READONLY_PORT", str(DEFAULT_HTTP_PORT)))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
