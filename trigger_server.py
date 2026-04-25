"""Local trigger server — phone-side button via iOS Shortcut.

POST /recall  {"name": "..."}  → recall() + notify()
POST /tick                     → force one ambient tick
GET  /health                   → sanity

Run:
    cd ~/projects/agihouse
    uv run --with anthropic --with fastapi --with uvicorn python trigger_server.py
"""

from __future__ import annotations

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
from pydantic import BaseModel  # noqa: E402

app = FastAPI()

# Cached service dicts — populated on startup.
_state: dict = {"gmail_services": None, "cal_services": None, "auth_error": None}


class RecallBody(BaseModel):
    name: str


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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("AGIHOUSE_TRIGGER_PORT", "9876"))
    uvicorn.run(app, host="127.0.0.1", port=port)
