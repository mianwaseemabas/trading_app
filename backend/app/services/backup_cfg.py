from __future__ import annotations

from sqlalchemy.orm import Session

from .. import state


def telegram_target(db: Session) -> tuple[str, str]:
    from ..config import settings
    from ..secretbox import decrypt
    p = state.get(db, "alert_prefs").get("telegram", {})
    token = decrypt(p["bot_token_enc"]) if p.get("bot_token_enc") else settings.telegram_bot_token
    return token or "", str(p.get("chat_id") or settings.telegram_chat_id or "")
