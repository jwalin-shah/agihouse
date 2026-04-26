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

sys.path.insert(0, str(Path(__file__).parent))

# Inbox stack is optional — /push and /events work without it. /recall and /tick
# will 503 if the import failed (e.g. google deps missing in this venv).
_INBOX_IMPORT_ERR: str | None = None
try:
    from services import google_auth_all  # noqa: E402
    from ambient import _load_state, tick  # noqa: E402
    from output import notify  # noqa: E402
    from recall import recall  # noqa: E402
except Exception as _e:
    _INBOX_IMPORT_ERR = repr(_e)
    google_auth_all = None  # type: ignore[assignment]
    _load_state = tick = notify = recall = None  # type: ignore[assignment]

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
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
    # Demo path is mic → VAD → Groq → recall (iMessage local sqlite). Gmail
    # is not on the hot path; recall.py guards Gmail calls with try/except,
    # so we leave gmail_services None and skip the OAuth refresh entirely.
    if _INBOX_IMPORT_ERR is not None:
        _state["auth_error"] = f"inbox import failed: {_INBOX_IMPORT_ERR}"
        print(f"[trigger_server] inbox unavailable: {_INBOX_IMPORT_ERR}", file=sys.stderr)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/recall")
def recall_endpoint(body: RecallBody) -> dict:
    gmail_svcs = _state.get("gmail_services")
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
def tick_endpoint(include_far: bool = False) -> dict:
    state = _load_state()
    state["alerted"] = []
    try:
        tick(state, force=True, include_far=include_far)
    except Exception as e:
        print(f"[trigger_server] tick failed: {e!r}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=repr(e))
    return {"ok": True}


DEMO_SCENARIOS: dict[str, list[str]] = {
    "departure": [
        "Leave in 12 min — 14 min drive to Sequoia HQ.",
        "Daniel: deck v7 in drive — break a leg.",
    ],
    "who_is_daniel": [
        'Daniel Park — last text Mon: "deck ready by Friday?" Pending: send the deck.',
    ],
    "who_is_sarah": [
        'Sarah Chen — Thursday lunch on the table. No reply yet.',
    ],
    "commitment": [
        "Noted: send deck tonight to Daniel Park.",
    ],
    "silence": [],  # demonstrates calibrated silence — pushes nothing
}


SUPPRESSED_DEMOS: dict[str, dict] = {
    "silence": {
        "reason": "ambiguous commitment — no time, no person, no action verb on a calendar",
        "transcript": "we should grab coffee sometime",
    },
    "privacy_therapist": {
        "reason": "context contains denied keyword ('therapist')",
        "transcript": "my therapist said I should set boundaries",
    },
    "off_the_record": {
        "reason": "sensitive context phrase detected ('off the record')",
        "transcript": "this is off the record but Daniel mentioned the round",
    },
    "cooldown": {
        "reason": "recall cooldown active — already surfaced this person 90s ago",
        "transcript": "remind me about Daniel Park",
    },
}


@app.post("/demo/{scenario}")
async def demo_endpoint(scenario: str) -> dict:
    # Suppressed scenarios — emit a policy decision to audit.log, push NOTHING.
    if scenario in SUPPRESSED_DEMOS:
        info = SUPPRESSED_DEMOS[scenario]
        from audit import log_event  # type: ignore
        log_event(
            "suppressed",
            action="recall",
            reason=info["reason"],
            transcript_chunk=info["transcript"],
        )
        return {"ok": True, "scenario": scenario, "pushed": 0, "suppressed": True, **info}

    if scenario not in DEMO_SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario: {scenario}")
    lines = DEMO_SCENARIOS[scenario]
    pushed = 0
    for i, line in enumerate(lines):
        if i > 0:
            await asyncio.sleep(2.0)
        payload = json.dumps({"text": line})
        for q in list(_subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        pushed += 1
    return {"ok": True, "scenario": scenario, "pushed": pushed}


_glasses_audio = None

def _audio_pipeline():
    global _glasses_audio
    if _glasses_audio is None:
        from audio_pipeline import GlassesAudioPipeline  # noqa: WPS433  (lazy)
        _glasses_audio = GlassesAudioPipeline(lambda: _state.get("gmail_services"))
    return _glasses_audio


@app.post("/audio")
async def audio_endpoint(request: Request) -> dict:
    body = await request.body()
    n = len(body)
    if n:
        _audio_pipeline().feed(body)
    return {"ok": True, "bytes": n}


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
