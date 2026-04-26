"""Deterministic policy gate + append-only audit log.

Every time the agent considers acting, it asks `gate(action, ...)`. The gate
returns a Decision, and the consideration is written to audit.log as one
JSONL row. The model never overrides the gate — these are hard rules.

Read policy from policy.yaml in this directory. Write log to log_path
(default: audit.log next to policy.yaml).

Usage:
    from audit import gate, log_event, summary

    d = gate("recall", person="Daniel Park", transcript_chunk="...")
    if d.allow:
        text = recall(...)
        if text:
            notify(text)        # output.py also re-checks dry_run
            log_event("fired", action="recall", person="Daniel Park", output=text)
    else:
        # gate already logged the suppression; nothing else to do.
        pass

Run `python audit.py` to print a stats summary of audit.log.
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).parent
_POLICY_PATH = _DIR / "policy.yaml"


@dataclass
class Decision:
    allow: bool
    reason: str
    action: str
    fields: dict[str, Any] = field(default_factory=dict)


# --- Policy load ---------------------------------------------------------

_policy_cache: dict | None = None
_policy_mtime: float | None = None


def _load_policy() -> dict:
    """Reload policy.yaml whenever it changes on disk. No restart needed."""
    global _policy_cache, _policy_mtime
    try:
        mtime = _POLICY_PATH.stat().st_mtime
    except FileNotFoundError:
        if _policy_cache is None:
            _policy_cache = _DEFAULT_POLICY
        return _policy_cache
    if _policy_cache is None or mtime != _policy_mtime:
        with _POLICY_PATH.open("r") as f:
            _policy_cache = yaml.safe_load(f) or {}
        _policy_mtime = mtime
    return _policy_cache


_DEFAULT_POLICY: dict = {
    "mode": "live",
    "log_path": "audit.log",
    "allowed_actions": ["recall", "departure_nudge", "commitment_log"],
    "denied_actions": [],
    "restraint": {
        "recall_cooldown_seconds": 300,
        "require_prior_correspondence": True,
        "max_proposals_per_minute": 4,
    },
    "privacy": {
        "denylist_names": [],
        "denylist_keywords": [],
        "suppress_in_contexts": [],
    },
}


def policy() -> dict:
    return _load_policy()


def is_dry_run() -> bool:
    return policy().get("mode", "live") == "dry_run"


# --- In-memory restraint state ------------------------------------------

_recall_last_fired: dict[str, float] = {}  # person -> ts
_proposal_window: deque[float] = deque(maxlen=100)  # all firings, ts


# --- Audit log -----------------------------------------------------------


def _log_path() -> Path:
    p = policy().get("log_path", "audit.log")
    return Path(p) if Path(p).is_absolute() else _DIR / p


def log_event(decision: str, *, action: str, reason: str = "", **fields: Any) -> None:
    """Append one JSONL row. decision ∈ {fired, suppressed, dry_run, considered}."""
    row = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "decision": decision,
        "action": action,
        "reason": reason,
        **fields,
    }
    line = json.dumps(row, default=str, ensure_ascii=False)
    with _log_path().open("a") as f:
        f.write(line + "\n")


# --- Gate ----------------------------------------------------------------


def gate(action: str, **ctx: Any) -> Decision:
    """Return a Decision and log the consideration. NEVER raises."""
    pol = policy()
    allowed: list[str] = pol.get("allowed_actions", [])
    denied: list[str] = pol.get("denied_actions", [])
    restraint = pol.get("restraint") or {}
    privacy = pol.get("privacy") or {}

    def deny(reason: str) -> Decision:
        log_event("suppressed", action=action, reason=reason, **ctx)
        return Decision(allow=False, reason=reason, action=action, fields=ctx)

    def allow(reason: str = "ok") -> Decision:
        log_event("considered", action=action, reason=reason, **ctx)
        return Decision(allow=True, reason=reason, action=action, fields=ctx)

    # 1) Allow/deny lists.
    if action in denied:
        return deny(f"action {action!r} on denied list")
    if action not in allowed:
        return deny(f"action {action!r} not on allowed list")

    # 2) Privacy: explicit name denylist.
    person = (ctx.get("person") or "").strip()
    name_lc = person.lower()
    for bad in privacy.get("denylist_names") or []:
        if bad and bad.lower() in name_lc:
            return deny(f"person matches denylist_names ({bad!r})")

    # 3) Privacy: keyword denylist on free-text fields.
    blob = " ".join(
        str(v) for k, v in ctx.items() if k in {"transcript_chunk", "snippet", "summary"}
    ).lower()
    for kw in privacy.get("denylist_keywords") or []:
        if kw and kw.lower() in blob:
            return deny(f"context contains denied keyword ({kw!r})")

    # 4) Privacy: sensitive-context phrases suppress everything.
    for phrase in privacy.get("suppress_in_contexts") or []:
        if phrase and phrase.lower() in blob:
            return deny(f"sensitive context phrase detected ({phrase!r})")

    # 5) Restraint: per-person cooldown for recall-shaped actions.
    if action == "recall" and person:
        cd = int(restraint.get("recall_cooldown_seconds", 0) or 0)
        last = _recall_last_fired.get(name_lc)
        if cd and last and (time.time() - last) < cd:
            wait = int(cd - (time.time() - last))
            return deny(f"recall cooldown active for {person!r} ({wait}s remaining)")

    # 6) Restraint: per-person prior-correspondence requirement.
    if (
        action == "recall"
        and restraint.get("require_prior_correspondence")
        and ctx.get("known_message_count", 1) == 0
    ):
        return deny("no prior correspondence with this person")

    # 7) Restraint: global proposals-per-minute ceiling.
    cap = int(restraint.get("max_proposals_per_minute", 0) or 0)
    if cap:
        now = time.time()
        recent = sum(1 for t in _proposal_window if now - t < 60)
        if recent >= cap:
            return deny(f"proposals/min ceiling reached ({recent}/{cap})")

    return allow()


def mark_fired(action: str, person: str | None = None, **fields: Any) -> None:
    """Call AFTER the agent actually fires. Updates restraint counters and logs."""
    now = time.time()
    _proposal_window.append(now)
    if action == "recall" and person:
        _recall_last_fired[person.strip().lower()] = now
    log_event(
        "dry_run" if is_dry_run() else "fired",
        action=action,
        person=person,
        **fields,
    )


# --- Stats ---------------------------------------------------------------


def summary() -> dict:
    """Aggregate audit.log into counts so you can show 'considered N, fired M'."""
    path = _log_path()
    if not path.exists():
        return {"total": 0}

    counts: dict[str, int] = {}
    by_action: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = row.get("decision", "?")
            a = row.get("action", "?")
            counts[d] = counts.get(d, 0) + 1
            by_action.setdefault(a, {})[d] = by_action.setdefault(a, {}).get(d, 0) + 1
            if d == "suppressed":
                reasons[row.get("reason", "?")] = reasons.get(row.get("reason", "?"), 0) + 1

    total = sum(counts.values())
    return {
        "total": total,
        "by_decision": counts,
        "by_action": by_action,
        "top_suppression_reasons": sorted(
            reasons.items(), key=lambda kv: kv[1], reverse=True
        )[:10],
    }


def _print_summary() -> None:
    s = summary()
    print(f"audit.log entries: {s['total']}")
    if s["total"] == 0:
        print("(no events yet)")
        return
    print("\nby decision:")
    for d, n in sorted(s["by_decision"].items(), key=lambda kv: -kv[1]):
        print(f"  {d:12s} {n}")
    print("\nby action × decision:")
    for a, dd in sorted(s["by_action"].items()):
        parts = ", ".join(f"{d}={n}" for d, n in sorted(dd.items()))
        print(f"  {a:18s} {parts}")
    if s["top_suppression_reasons"]:
        print("\ntop suppression reasons:")
        for r, n in s["top_suppression_reasons"]:
            print(f"  [{n:3d}] {r}")


if __name__ == "__main__":
    _print_summary()
    sys.exit(0)
