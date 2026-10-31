"""Optional Telegram notifications. Failures are logged and never block trading logic."""
from __future__ import annotations

import json
import logging
import threading
import urllib.request

from ..config import settings

log = logging.getLogger("notifier")


def telegram_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _send(text: str) -> None:
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            data=json.dumps({"chat_id": settings.telegram_chat_id, "text": text[:4000]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # pragma: no cover - network
        log.warning("Telegram send failed: %s", e)


def notify(text: str, enabled: bool = True) -> None:
    """Legacy direct messages. Telegram/email delivery now goes through the alert center
    (services/alerts.py) with dedup, retries and per-type preferences, so this only logs."""
    log.info("notify: %s", text)


def send_direct(text: str) -> None:
    if telegram_configured():
        threading.Thread(target=_send, args=(f"[{settings.app_name}] {text}",), daemon=True).start()
