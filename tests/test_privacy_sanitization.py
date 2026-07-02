"""PR 1A — log/alert/webhook sanitization, with a real positive control.

Not "the current code happens to be clean" — a regression test against the ACTUAL
production alert path (notifications.push_alert), proving a full sensitive payload
never reaches the email body or the webhook JSON, only a bounded masked excerpt.
This is verified red/green by hand before commit (see the PR report), the same
discipline as PR 0's rogue-latent-read probe.
"""
import asyncio
import json

import notifications


async def _capture(monkeypatch):
    captured = {}

    async def fake_email(subject, body):
        captured["email_body"] = body
        return True

    async def fake_webhook(payload):
        captured["webhook_payload"] = payload
        return True

    monkeypatch.setattr(notifications, "_send_email", fake_email)
    monkeypatch.setattr(notifications, "_send_webhook", fake_webhook)
    return captured


def test_push_alert_never_leaks_full_sensitive_payload(monkeypatch):
    captured = {}

    async def go():
        nonlocal captured
        captured = await _capture(monkeypatch)
        secret = "SENSITIVE_CANARY_" + ("x" * 500)
        await notifications.push_alert(
            "Critical Risk", 42, "alice", "critical", 100, ["suicide"], secret)
        return secret

    secret = asyncio.run(go())
    assert "email_body" in captured and "webhook_payload" in captured
    assert secret not in captured["email_body"], (
        "push_alert leaked the FULL sensitive message into the admin email body")
    assert secret not in json.dumps(captured["webhook_payload"]), (
        "push_alert leaked the FULL sensitive message into the webhook payload")
    # A masked excerpt (short prefix) IS allowed to appear — that's the design.
    assert secret[:24] in captured["email_body"]


def test_push_alert_excerpt_is_bounded_length(monkeypatch):
    async def go():
        captured = await _capture(monkeypatch)
        await notifications.push_alert(
            "Critical Risk", 42, "alice", "critical", 100, ["suicide"], "y" * 5000)
        return captured

    captured = asyncio.run(go())
    # The excerpt in the body must be short — not the 5000-char payload.
    assert "Excerpt: «" in captured["email_body"]
    excerpt_line = [l for l in captured["email_body"].splitlines() if l.startswith("Excerpt:")][0]
    assert len(excerpt_line) < 100, f"excerpt line too long, sanitizer may be bypassed: {len(excerpt_line)}"
