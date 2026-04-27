"""Send Messages.app messages via AppleScript. macOS only.

Requires Messages.app signed in. First run will prompt for Automation permission
in System Settings -> Privacy & Security -> Automation -> Terminal/Python -> Messages.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_CONTACTS_PATH = Path(__file__).parent / "contacts.json"


def _service_for_handle(handle: str) -> str | None:
    try:
        contacts = json.loads(_CONTACTS_PATH.read_text())
    except Exception:
        return None
    needle = handle.strip().lower()
    for c in contacts:
        if not isinstance(c, dict):
            continue
        candidates = {str(c.get("phone") or "").strip().lower(),
                      str(c.get("email") or "").strip().lower(),
                      str(c.get("name") or "").strip().lower()}
        candidates.update(str(a).strip().lower() for a in (c.get("aliases") or []))
        if needle and needle in candidates:
            svc = str(c.get("service") or "").strip().lower()
            if svc in {"imessage", "sms"}:
                return svc
    return None


_APPLESCRIPT_SEND = """
on run argv
    set targetHandle to item 1 of argv
    set messageText to item 2 of argv
    set serviceKind to item 3 of argv
    tell application "Messages"
        if serviceKind is "sms" then
            set targetService to 1st service whose service type = SMS
        else
            set targetService to 1st service whose service type = iMessage
        end if
        set targetBuddy to buddy targetHandle of targetService
        send messageText to targetBuddy
    end tell
end run
"""


def _send_with_service(handle: str, text: str, service: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["osascript", "-e", _APPLESCRIPT_SEND, handle, text, service],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return True, f"sent via {service}"
        return False, r.stderr.strip() or "unknown error"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def send(handle: str, text: str) -> tuple[bool, str]:
    contact_service = _service_for_handle(handle)
    if contact_service:
        return _send_with_service(handle, text, contact_service)
    if os.environ.get("AGIHOUSE_FORCE_SMS", "").strip().lower() in {"1", "true", "yes"}:
        return _send_with_service(handle, text, "sms")
    ok, msg = _send_with_service(handle, text, "imessage")
    if ok:
        return ok, msg
    if os.environ.get("AGIHOUSE_ALLOW_SMS_FALLBACK", "").strip().lower() in {"1", "true", "yes"}:
        sms_ok, sms_msg = _send_with_service(handle, text, "sms")
        if sms_ok:
            return sms_ok, sms_msg
        return False, f"iMessage failed: {msg}; SMS failed: {sms_msg}"
    return False, msg


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: python imessage_send.py <phone-or-email> <message>")
        sys.exit(1)
    ok, msg = send(sys.argv[1], sys.argv[2])
    print(f"{'OK' if ok else 'FAIL'}: {msg}")
    sys.exit(0 if ok else 1)
