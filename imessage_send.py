"""Send iMessage via AppleScript. macOS only.

Requires Messages.app signed in. First run will prompt for Automation permission
in System Settings → Privacy & Security → Automation → Terminal/Python → Messages.
"""
import subprocess


def send(handle: str, text: str) -> tuple[bool, str]:
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{handle}" of targetService
        send "{text}" to targetBuddy
    end tell
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return True, "sent"
        return False, r.stderr.strip() or "unknown error"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: python imessage_send.py <phone-or-email> <message>")
        sys.exit(1)
    ok, msg = send(sys.argv[1], sys.argv[2])
    print(f"{'OK' if ok else 'FAIL'}: {msg}")
    sys.exit(0 if ok else 1)
