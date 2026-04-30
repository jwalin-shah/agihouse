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
from datetime import datetime, timezone
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

from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

def _require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    expected = os.environ.get("AGIHOUSE_API_KEY", "").strip()
    if not expected:
        return
    if not credentials or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")

# Cached service dicts — populated on startup.
_state: dict = {"gmail_services": None, "cal_services": None, "auth_error": None}

# In-memory fanout for the G2 renderer. Each subscriber owns an asyncio.Queue.
_subscribers: list[asyncio.Queue[str]] = []
_scheduled_task: asyncio.Task | None = None
_ambient_task: asyncio.Task | None = None


class RecallBody(BaseModel):
    name: str


class PushBody(BaseModel):
    text: str


class TranscriptBody(BaseModel):
    text: str
    event: dict | None = None


class RejectProposalBody(BaseModel):
    reason: str = "user_rejected"


@app.on_event("startup")
def _auth_once() -> None:
    # Attempt to load Gmail/Calendar OAuth services at startup.
    if _INBOX_IMPORT_ERR is not None:
        _state["auth_error"] = f"inbox import failed: {_INBOX_IMPORT_ERR}"
        print(f"[trigger_server] inbox unavailable: {_INBOX_IMPORT_ERR}", file=sys.stderr)
        return
    try:
        svcs = google_auth_all(interactive=False)
        _state["gmail_services"] = svcs
        _state["cal_services"] = svcs
    except Exception as e:
        _state["auth_error"] = f"OAuth failed: {e}"
        print(f"[trigger_server] Gmail OAuth failed (token expired?): {e}", file=sys.stderr)


@app.on_event("startup")
async def _start_background_tasks() -> None:
    global _scheduled_task, _ambient_task
    _scheduled_task = asyncio.create_task(_scheduled_imessage_loop())
    if os.environ.get("AGIHOUSE_AMBIENT_TICK", "").strip().lower() in {"1", "true", "yes"}:
        _ambient_task = asyncio.create_task(_ambient_tick_loop())
    try:
        from imessage_watcher import start_watcher
        start_watcher()
    except Exception as e:
        print(f"[trigger_server] imsg watcher failed to start: {e!r}", file=sys.stderr)


@app.on_event("shutdown")
async def _stop_background_tasks() -> None:
    for task in (_scheduled_task, _ambient_task):
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _scheduled_imessage_loop() -> None:
    while True:
        try:
            from actions import send_due_imessages

            sent = await asyncio.to_thread(send_due_imessages)
            for item in sent:
                print(f"[trigger_server] scheduled iMessage job {item['job_id']} -> {item['status']}", file=sys.stderr)
        except Exception as e:
            print(f"[trigger_server] scheduled iMessage loop failed: {e!r}", file=sys.stderr)
        await asyncio.sleep(float(os.environ.get("AGIHOUSE_SCHEDULER_INTERVAL", "15")))


async def _ambient_tick_loop() -> None:
    if _INBOX_IMPORT_ERR is not None:
        return
    state = _load_state()
    interval = float(os.environ.get("AGIHOUSE_AMBIENT_TICK_SECONDS", "60"))
    while True:
        try:
            await asyncio.to_thread(tick, state)
        except Exception as e:
            print(f"[trigger_server] ambient loop failed: {e!r}", file=sys.stderr)
        await asyncio.sleep(interval)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "inbox_available": _INBOX_IMPORT_ERR is None}


def _path_diag(path: Path) -> dict:
    exists = path.exists()
    if exists:
        writable = os.access(path, os.W_OK)
    else:
        writable = os.access(path.parent, os.W_OK)
    return {"path": str(path), "exists": exists, "writable": writable}


@app.get("/diagnostics", dependencies=[Depends(_require_auth)])
def diagnostics() -> dict:
    # Keep this endpoint deterministic + local-only so it's safe for quick checks.
    from audit import policy as load_policy  # lazy import to avoid startup coupling
    from actions import _live_imessage_handles  # lazy import to avoid startup coupling

    pol = load_policy() or {}
    root = Path(__file__).parent
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "inbox_available": _INBOX_IMPORT_ERR is None,
        "inbox_error": _INBOX_IMPORT_ERR,
        "policy_mode": pol.get("mode", "unknown"),
        "proposal_first": os.environ.get("AGIHOUSE_PROPOSAL_ONLY", "1"),
        "ambient_tick": os.environ.get("AGIHOUSE_AMBIENT_TICK", "0"),
        "scheduler_interval": os.environ.get("AGIHOUSE_SCHEDULER_INTERVAL", "15"),
        "live_imessage_handles": len(_live_imessage_handles()),
        "keys_present": {
            "GROQ_API_KEY": bool(os.environ.get("GROQ_API_KEY")),
            "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "NVIDIA_API_KEY": bool(os.environ.get("NVIDIA_API_KEY")),
        },
        "files": {
            "actions_jsonl": _path_diag(root / "actions.jsonl"),
            "audit_log": _path_diag(root / "audit.log"),
            "policy_yaml": _path_diag(root / "policy.yaml"),
            "contacts_json": _path_diag(root / "contacts.json"),
        },
        "subscribers": len(_subscribers),
    }


def _require_inbox(feature: str) -> None:
    if _INBOX_IMPORT_ERR is not None:
        raise HTTPException(
            status_code=503,
            detail=f"{feature} unavailable: inbox dependency import failed",
        )


@app.post("/recall", dependencies=[Depends(_require_auth)])
def recall_endpoint(body: RecallBody) -> dict:
    _require_inbox("recall")
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


@app.post("/tick", dependencies=[Depends(_require_auth)])
def tick_endpoint(include_far: bool = False) -> dict:
    _require_inbox("tick")
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


@app.post("/demo/{scenario}", dependencies=[Depends(_require_auth)])
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


@app.post("/audio", dependencies=[Depends(_require_auth)])
async def audio_endpoint(request: Request, background_tasks: BackgroundTasks) -> dict:
    _require_inbox("audio")
    body = await request.body()
    n = len(body)
    if n:
        background_tasks.add_task(_audio_pipeline().feed, body)
    return {"ok": True, "bytes": n}


@app.post("/transcript", dependencies=[Depends(_require_auth)])
def transcript_endpoint(body: TranscriptBody) -> dict:
    """Perfect-ASR test hook: run a transcript through the action path."""
    from action_runtime import evaluate_and_dispatch
    from actions import lookup_contact
    from event_extractor import extract as extract_event

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty transcript")
    event = body.event or extract_event(text)
    if not event:
        return {"ok": True, "status": "no_action", "transcript": text}

    action = event.get("action")
    payload = dict(event.get("payload") or {})
    if action in {"send_imessage", "schedule_imessage"} and "handle" in payload:
        contact = lookup_contact(payload["handle"])
        if contact and contact.get("phone"):
            payload["handle"] = contact["phone"]

    result = evaluate_and_dispatch(
        action,
        payload,
        transcript=text,
        confidence=event.get("confidence"),
    )
    return {"ok": True, "transcript": text, "event": {**event, "payload": payload}, "result": result}


@app.post("/push", dependencies=[Depends(_require_auth)])
async def push_endpoint(body: PushBody) -> dict:
    payload = json.dumps({"text": body.text})
    dropped = 0
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dropped += 1
    return {"ok": True, "subscribers": len(_subscribers), "dropped": dropped}


@app.get("/proposals", dependencies=[Depends(_require_auth)])
def proposals_endpoint() -> dict:
    from action_runtime import list_pending_proposals

    return {"ok": True, "proposals": list_pending_proposals()}


@app.get("/audit/summary", dependencies=[Depends(_require_auth)])
def audit_summary_endpoint() -> dict:
    from audit import summary

    return {"ok": True, "summary": summary()}


@app.get("/actions/recent", dependencies=[Depends(_require_auth)])
def recent_actions_endpoint(limit: int = 50) -> dict:
    from actions import recent_actions

    return {"ok": True, "actions": recent_actions(limit=limit)}


@app.get("/memory/edges", dependencies=[Depends(_require_auth)])
def memory_edges_endpoint(limit: int = 50) -> dict:
    from actions import list_memory_edges

    return {"ok": True, "edges": list_memory_edges(limit=limit)}


@app.get("/memories", dependencies=[Depends(_require_auth)])
def memories_endpoint(query: str | None = None) -> dict:
    from actions import list_memories

    return {"ok": True, "result": list_memories(query=query)}


@app.get("/scheduled-imessages", dependencies=[Depends(_require_auth)])
def scheduled_imessages_endpoint(limit: int = 50) -> dict:
    from actions import list_scheduled_imessages

    return {"ok": True, "scheduled": list_scheduled_imessages(limit=limit)}


@app.post("/scheduled-imessages/run-due", dependencies=[Depends(_require_auth)])
def run_due_scheduled_imessages_endpoint() -> dict:
    from actions import send_due_imessages

    return {"ok": True, "sent": send_due_imessages()}


@app.post("/proposals/confirm-latest")
def confirm_latest_endpoint() -> dict:
    """Voice-confirm: pop OLDEST pending proposal (FIFO), confirm it, and
    surface the next-in-line on the HUD if more are queued."""
    from action_runtime import confirm_proposal, list_pending_proposals
    pending = list_pending_proposals()
    if not pending:
        return {"ok": False, "reason": "no_pending"}
    oldest = pending[0]
    pid = oldest.get("id") or oldest.get("proposal_id")
    if not pid:
        return {"ok": False, "reason": "no_id"}
    out = confirm_proposal(pid)
    remaining = list_pending_proposals()
    if remaining:
        nxt = remaining[0]
        title = nxt.get("title") or nxt.get("preview") or "next action"
        from output import notify
        notify(f"🟡 NEXT: {title}\nSay 'confirm' or 'reject' ({len(remaining)} queued)", speak=False)
    return {**out, "remaining": len(remaining)}


@app.post("/proposals/{proposal_id}/confirm", dependencies=[Depends(_require_auth)])
def confirm_proposal_endpoint(proposal_id: str) -> dict:
    from action_runtime import confirm_proposal

    out = confirm_proposal(proposal_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out)
    return out


@app.post("/proposals/{proposal_id}/reject", dependencies=[Depends(_require_auth)])
def reject_proposal_endpoint(proposal_id: str, body: RejectProposalBody) -> dict:
    from action_runtime import reject_proposal

    out = reject_proposal(proposal_id, reason=body.reason)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out)
    return out


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
