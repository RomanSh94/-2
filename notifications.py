"""X20 Notifications — встроенный smtplib (работает везде)"""
import asyncio, json, urllib.request, smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO, ALERT_WEBHOOK_URL

async def _send_email(subject: str, body: str) -> bool:
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO]):
        return False
    def _mail():
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = f"[X20 Alert] {subject}", SMTP_USER, ALERT_EMAIL_TO
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as srv:
                srv.starttls(); srv.login(SMTP_USER, SMTP_PASSWORD); srv.send_message(msg)
            return True
        except Exception as e:
            print(f"[email] {e}"); return False
    try:
        return await asyncio.get_event_loop().run_in_executor(None, _mail)
    except Exception as e:
        print(f"[email] {e}"); return False

async def _send_webhook(payload: dict) -> bool:
    if not ALERT_WEBHOOK_URL: return False
    def _post():
        try:
            data = json.dumps(payload).encode("utf-8")
            req  = urllib.request.Request(ALERT_WEBHOOK_URL, data=data,
                headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5): return True
        except Exception as e:
            print(f"[webhook] {e}"); return False
    try:
        return await asyncio.get_event_loop().run_in_executor(None, _post)
    except Exception as e:
        print(f"[webhook] {e}"); return False

def _mask_excerpt(text: str, keep: int = 24) -> str:
    """Privacy: a SHORT excerpt, never the full personal message (email/webhook)."""
    t = " ".join((text or "").split())
    return (t[:keep] + "…") if len(t) > keep else t


async def push_alert(subject: str, user_id: int, username: str, risk_level: str,
                     risk_score: int, categories: list, message_text: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    excerpt = _mask_excerpt(message_text)
    body = (f"User: {user_id} (@{username})\nRisk: {risk_level}\nScore: {risk_score}\n"
            f"Categories: {', '.join(categories)}\n"
            f"Excerpt: «{excerpt}» ({len(message_text or '')} chars)\nTime: {ts}")
    emoji = {"critical":"🚨","high":"⚠️"}.get(risk_level, "🔶")
    payload = {
        "text": f"{emoji} X20 Alert — {subject}",
        "blocks": [
            {"type":"section","text":{"type":"mrkdwn","text":f"{emoji} *X20 Alert — {subject}*"}},
            {"type":"section","text":{"type":"mrkdwn","text":f"```{body}```"}},
            {"type":"context","elements":[{"type":"mrkdwn","text":ts}]},
        ]
    }
    await asyncio.gather(_send_email(subject, body), _send_webhook(payload), return_exceptions=True)
