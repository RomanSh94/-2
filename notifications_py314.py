"""
X20 Notifications — Оптимизирована для Python 3.14

ВНИМАНИЕ: aiosmtplib может не устанавливаться на Python 3.14.
Эта версия использует встроенный smtplib (синхронный, но работает везде).

Для production-среды используйте Python 3.12 или 3.13.
"""
import asyncio, json, urllib.request, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO, ALERT_WEBHOOK_URL

async def _send_email(subject: str, body: str) -> bool:
    """Отправляет email синхронно в отдельном потоке."""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO]):
        return False

    def _mail():
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[X20 Alert] {subject}"
            msg["From"]    = SMTP_USER
            msg["To"]      = ALERT_EMAIL_TO
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"[email] {e}")
            return False

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _mail)
    except Exception as e:
        print(f"[email error] {e}")
        return False

async def _send_webhook(payload: dict) -> bool:
    """Отправляет JSON в webhook (Slack/Discord/custom)."""
    if not ALERT_WEBHOOK_URL:
        return False
    def _post():
        try:
            data = json.dumps(payload).encode("utf-8")
            req  = urllib.request.Request(ALERT_WEBHOOK_URL, data=data,
                                          headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except Exception as e:
            print(f"[webhook] {e}")
            return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _post)
        return True
    except Exception as e:
        print(f"[webhook error] {e}")
        return False

async def push_alert(subject: str, user_id: int, username: str,
                     risk_level: str, risk_score: int,
                     categories: list, message_text: str) -> None:
    """Отправляет alert по email и webhook одновременно."""
    ts   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = (f"User:       {user_id} (@{username})\n"
            f"Risk level: {risk_level}\n"
            f"Score:      {risk_score}\n"
            f"Categories: {', '.join(categories)}\n"
            f"Message:    {message_text[:300]}\n"
            f"Time:       {ts}")
    emoji = {"critical":"🚨","high":"⚠️"}.get(risk_level, "🔶")
    webhook_payload = {
        "text": f"{emoji} X20 Alert — {subject}",
        "blocks": [
            {"type":"section","text":{"type":"mrkdwn","text":f"{emoji} *X20 Alert — {subject}*"}},
            {"type":"section","text":{"type":"mrkdwn","text":f"```{body}```"}},
            {"type":"context","elements":[{"type":"mrkdwn","text":ts}]},
        ]
    }
    await asyncio.gather(_send_email(subject, body), _send_webhook(webhook_payload),
                         return_exceptions=True)
