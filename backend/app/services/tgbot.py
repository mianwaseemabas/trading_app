"""Two-way Telegram bot: read-only commands for everyone in the configured chat, risky ones need the owner's PIN.

Runs as a background thread inside the worker (long polling, no webhook, no open port).
Only messages from the saved chat id are answered. A PIN message is deleted from the chat right after it is read.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time

from sqlalchemy import select

from .. import state
from ..db import SessionLocal
from ..models import Position, Quote, Signal, SignalApproval, User, utcnow

log = logging.getLogger("tgbot")

HELP = """Qivynex bot commands
/status — mode, account, risk, alerts
/positions — open trades with P/L
/setups — setups waiting for you
/price BTCUSDT — latest price
/brief — today's AI brief now
/ask <question> — ask the AI coach
/approve <id> <PIN> — approve a setup
/decline <id> — decline a setup
/kill <PIN> — KILL SWITCH (stop all new orders)
/resume <PIN> — release the kill switch
PIN commands: the message with your PIN is deleted right away."""

_fails: dict = {"n": 0, "until": 0.0}


def pin_hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def set_pin(db, pin: str) -> None:
    salt = os.urandom(8).hex()
    b = state.get(db, "tgbot")
    b.update(pin_salt=salt, pin_hash=pin_hash(pin, salt))
    state.put(db, "tgbot", b)


def check_pin(db, pin: str) -> tuple[bool, str]:
    if time.time() < _fails["until"]:
        return False, f"Too many wrong PINs. Try again in {int(_fails['until'] - time.time()) // 60 + 1} min."
    b = state.get(db, "tgbot")
    if not b.get("pin_hash"):
        return False, "No PIN set. Set one in the app: Alerts → Telegram → Bot PIN."
    ok = hmac.compare_digest(pin_hash(pin or "", b["pin_salt"]), b["pin_hash"])
    if ok:
        _fails["n"] = 0
        return True, ""
    _fails["n"] += 1
    if _fails["n"] >= 5:
        _fails.update(n=0, until=time.time() + 900)
        return False, "Wrong PIN 5 times — PIN commands are locked for 15 minutes."
    return False, "Wrong PIN."


class _Req:
    """Minimal request stand-in so the normal API handlers (with audit) can be reused."""
    headers = {"x-real-ip": "telegram"}
    client = None


def _owner(db) -> User | None:
    return db.scalar(select(User).where(User.is_admin.is_(True), User.is_active.is_(True)).order_by(User.id)) \
        if hasattr(User, "is_active") else db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))


def handle(db, text: str) -> tuple[str, bool]:
    """Returns (reply, delete_incoming_message)."""
    parts = text.strip().split()
    if not parts:
        return "", False
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    from fastapi import HTTPException
    try:
        if cmd in ("/start", "/help"):
            return HELP, False
        if cmd == "/status":
            from . import trading
            tr = state.get(db, "trading")
            mode = tr["mode"] if tr["mode"] in trading.ORDER_MODES else "PAPER"
            acct, _ = trading.account_state(db, mode)
            cc = state.get(db, "crypto")
            n_pos = len(db.scalars(select(Position.id).where(Position.status == "OPEN")).all())
            n_set = len(db.scalars(select(SignalApproval.signal_id).where(SignalApproval.status == "WAITING")).all())
            from .automation import flags_of
            auto = [m for m, on in flags_of(state.get(db, "controls")).items() if on]
            return (f"Mode: {tr['mode']} · crypto: {cc.get('mode')}\n"
                    f"{'🛑 KILL SWITCH ON' if tr.get('kill_switch') else '✅ kill switch off'}{' · LOCKED: ' + tr.get('lock_reason', '') if tr.get('locked') else ''}\n"
                    f"Equity ({mode}): {acct.equity:.2f} {acct.currency}\nOpen positions: {n_pos} · setups waiting: {n_set}\n"
                    f"Auto-trade: {', '.join(auto) or 'off'}"), False
        if cmd == "/positions":
            ps = db.scalars(select(Position).where(Position.status == "OPEN").order_by(Position.id)).all()
            if not ps:
                return "No open positions.", False
            return "\n".join(f"#{p.id} {p.mode} {p.symbol} {p.side} {p.volume} @ {p.open_price} · SL {p.stop_loss} · TP {p.take_profit} · "
                             f"P/L {p.unrealized_pnl:+.2f}" for p in ps), False
        if cmd == "/setups":
            rows = db.execute(select(Signal, SignalApproval).join(SignalApproval, SignalApproval.signal_id == Signal.id)
                              .where(SignalApproval.status == "WAITING").order_by(Signal.id)).all()
            if not rows:
                return "No setups waiting.", False
            return "\n".join(f"#{s.id} {s.symbol} {s.action} {s.timeframe} · entry {s.entry_ref} SL {s.stop_loss} TP {s.take_profit} · "
                             f"expires {a.expires_at:%H:%M} UTC" for s, a in rows) + "\n\nApprove: /approve <id> <PIN>", False
        if cmd == "/price":
            sym = (args[0] if args else "BTCUSDT").upper()
            q = db.get(Quote, sym)
            return (f"{sym}: bid {q.bid} · ask {q.ask} · {(utcnow() - q.time).total_seconds():.0f}s ago" if q else f"No price for {sym}."), False
        if cmd == "/brief":
            from .ai_jobs import morning_brief
            return morning_brief(db, force=True), False
        if cmd == "/ask":
            if not args:
                return "Usage: /ask why was my last trade stopped out?", False
            from ..routers.ai import SYSTEM_PROMPT, _call, _context
            from ..secretbox import decrypt
            ai = state.get(db, "ai")
            if not ai.get("enabled") or not ai.get("api_key_enc"):
                return "The AI assistant is off (AI Assistant page in the app).", False
            system = SYSTEM_PROMPT + "\nAnswer in under 150 words (Telegram).\n\nAPP DATA:\n" + _context(db)
            return _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), system, [{"role": "user", "content": " ".join(args)[:1500]}]), False
        if cmd == "/decline":
            if not args or not args[0].lstrip("#").isdigit():
                return "Usage: /decline <id>", False
            from ..routers.setups import DecisionIn, decline
            decline(int(args[0].lstrip("#")), DecisionIn(note="declined from Telegram"), _Req(), _owner(db), db)
            return f"Setup #{args[0]} declined.", False
        if cmd == "/approve":
            if len(args) < 2 or not args[0].lstrip("#").isdigit():
                return "Usage: /approve <id> <PIN>", True
            ok, why = check_pin(db, args[1])
            if not ok:
                return why, True
            from ..routers.setups import DecisionIn, approve
            approve(int(args[0].lstrip("#")), DecisionIn(note="approved from Telegram"), _Req(), _owner(db), db)
            return f"✅ Setup #{args[0]} approved — the risk engine re-checked it and the order was sent. You'll get the fill alert.", True
        if cmd in ("/kill", "/resume"):
            ok, why = check_pin(db, args[0] if args else "")
            if not ok:
                return why, True
            from ..routers.system import KillIn, kill_switch
            kill_switch(KillIn(engaged=cmd == "/kill", reason="from Telegram"), _Req(), _owner(db), db)
            return ("🛑 KILL SWITCH ENGAGED — no new orders. Open trades keep their stops. /resume <PIN> to release."
                    if cmd == "/kill" else "✅ Kill switch released."), True
        return "Unknown command. /help shows the list.", False
    except HTTPException as e:
        db.rollback()
        return f"Not done: {e.detail}", cmd in ("/approve", "/kill", "/resume")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("bot command failed")
        return f"Error: {str(e)[:200]}", cmd in ("/approve", "/kill", "/resume")


def _creds(db) -> tuple[str, str]:
    from ..config import settings
    from ..secretbox import decrypt
    p = state.get(db, "alert_prefs").get("telegram", {})
    token = decrypt(p["bot_token_enc"]) if p.get("bot_token_enc") else settings.telegram_bot_token
    return token or "", str(p.get("chat_id") or settings.telegram_chat_id or "")


def poll_once(api, offset: int, timeout: int = 25) -> int:
    """One long-poll round. `api(method, payload)` calls Telegram. Returns the next offset."""
    db = SessionLocal()
    try:
        b = state.get(db, "tgbot")
        token, chat = _creds(db)
        if not b.get("enabled") or not token or not chat:
            time.sleep(15)
            return offset
        res = api(token, "getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]})
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1)
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) != chat or not m.get("text", "").startswith("/"):
                continue
            reply, delete = handle(db, m["text"])
            db.commit()
            if delete:
                try:
                    api(token, "deleteMessage", {"chat_id": chat, "message_id": m["message_id"]})
                except Exception:  # noqa: BLE001
                    pass
            if reply:
                api(token, "sendMessage", {"chat_id": chat, "text": reply[:4000]})
        return offset
    finally:
        db.close()


def start_thread() -> threading.Thread:
    from .alerts import telegram_api

    def run():
        offset = 0
        while True:
            try:
                offset = poll_once(lambda t, m, p: telegram_api(t, m, p, timeout=35), offset)
            except Exception as e:  # noqa: BLE001
                log.warning("telegram bot poll failed: %s", e)
                time.sleep(20)
    t = threading.Thread(target=run, name="tgbot", daemon=True)
    t.start()
    return t
