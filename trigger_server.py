"""Local trigger server — phone-side button via iOS Shortcut + G2 renderer bridge.

POST /recall  {"name": "..."}  → recall() + notify()
POST /tick                     → force one ambient tick
POST /push    {"text": "..."}  → fan out to G2 renderer subscribers
GET  /events                   → SSE stream consumed by g2-renderer
GET  /health                   → sanity

Run:
    cd ~/projects/agihouse
    uv run --with anthropic --with fastapi --with uvicorn python trigger_server.py

Bind to LAN so the phone-side Even Hub companion app can reach /events:
    AGIHOUSE_TRIGGER_HOST=0.0.0.0 uv run ... python trigger_server.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

# --- Make sibling inbox project importable -------------------------------
INBOX_DIR = Path.home() / "projects" / "inbox"
if str(INBOX_DIR) not in sys.path:
    sys.path.insert(0, str(INBOX_DIR))
os.chdir(INBOX_DIR)  # services.py uses BASE_DIR-relative paths

from services import google_auth_all  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from ambient import _load_state, tick  # noqa: E402
from output import notify  # noqa: E402
from recall import recall  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cached service dicts — populated on startup.
_state: dict = {"gmail_services": None, "cal_services": None, "auth_error": None}

# In-memory fanout for the G2 renderer. Each subscriber owns an asyncio.Queue.
_subscribers: list[asyncio.Queue[str]] = []


class RecallBody(BaseModel):
    name: str


class PushBody(BaseModel):
    text: str


@app.on_event("startup")
def _auth_once() -> None:
    try:
        gmail_svcs, cal_svcs, *_ = google_auth_all()
        _state["gmail_services"] = gmail_svcs
        _state["cal_services"] = cal_svcs
    except Exception as e:  # let server start anyway
        _state["auth_error"] = repr(e)
        print(f"[trigger_server] startup auth failed: {e!r}", file=sys.stderr)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/recall")
def recall_endpoint(body: RecallBody) -> dict:
    gmail_svcs = _state.get("gmail_services")
    if not gmail_svcs:
        raise HTTPException(status_code=503, detail=f"auth not ready: {_state.get('auth_error')}")
    name = body.name.strip()
    try:
        text = recall(
            name,
            gmail_services=gmail_svcs,
            cal_services=_state.get("cal_services"),
        )
    except Exception as e:
        print(f"[trigger_server] recall failed: {e!r}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=repr(e))

    if text:
        notify(text)
        return {"ok": True, "text": text}

    notify(f"No recent context for {name}.")
    return {"ok": True, "text": None}


@app.post("/tick")
def tick_endpoint() -> dict:
    state = _load_state()
    state["alerted"] = []
    try:
        tick(state, force=True)
    except Exception as e:
        print(f"[trigger_server] tick failed: {e!r}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=repr(e))
    return {"ok": True}


@app.post("/push")
async def push_endpoint(body: PushBody) -> dict:
    payload = json.dumps({"text": body.text})
    dropped = 0
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dropped += 1
    return {"ok": True, "subscribers": len(_subscribers), "dropped": dropped}


@app.get("/events")
async def events_endpoint() -> StreamingResponse:
    async def stream():
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        _subscribers.append(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            with contextlib.suppress(ValueError):
                _subscribers.remove(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("AGIHOUSE_TRIGGER_HOST", "127.0.0.1")
    port = int(os.environ.get("AGIHOUSE_TRIGGER_PORT", "9876"))
    uvicorn.run(app, host=host, port=port)
