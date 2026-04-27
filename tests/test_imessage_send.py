from __future__ import annotations

import subprocess

import imessage_send


def test_send_passes_handle_and_text_as_args(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = imessage_send.send('buddy"evil', 'hello "quoted" text')

    assert ok is True
    assert msg == "sent via imessage"
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 10
    assert captured["cmd"][:3] == ["osascript", "-e", imessage_send._APPLESCRIPT_SEND]
    assert captured["cmd"][3:] == ['buddy"evil', 'hello "quoted" text', "imessage"]


def test_send_returns_stderr_for_nonzero(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 1, "", "script error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = imessage_send.send("foo", "bar")

    assert ok is False
    assert msg == "script error"
