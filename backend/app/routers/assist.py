"""Settings and actions for the AI helpers, the Telegram bot, ask-the-chart and off-server backups."""
from __future__ import annotations

import re
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import User
from ..secretbox import encrypt
from ..services import ai_jobs

router = APIRouter(prefix="/api", tags=["assist"])
_rate: dict = {}


def _limit(user: User, key: str, seconds: int) -> None:
    k = (user.id, key)
    if time.time() - _rate.get(k, 0) < seconds:
        raise HTTPException(429, "Please wait a few seconds and try again")
    _rate[k] = time.time()


# ------------------------------------------------------------------ AI brief + coach
@router.get("/ai-jobs")
def get_jobs(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    c = ai_jobs.cfg(db)
    ai = state.get(db, "ai")
    return {**c, "ai_ready": bool(ai.get("enabled") and ai.get("api_key_enc"))}


class JobsIn(BaseModel):
    brief_enabled: bool | None = None
    brief_time: str | None = None
    timezone: str | None = Field(default=None, max_length=64)
    coach_enabled: bool | None = None
    coach_day: int | None = Field(default=None, ge=0, le=6)
    coach_time: str | None = None


@router.put("/ai-jobs")
def put_jobs(body: JobsIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    c = state.get_locked(db, "ai_jobs")
    for k in ("brief_time", "coach_time"):
        v = getattr(body, k)
        if v is not None:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                raise HTTPException(400, f"{k}: use HH:MM, e.g. 07:30")
            c[k] = v
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "Unknown time zone (example: Asia/Riyadh)")
        c["timezone"] = body.timezone
    for k in ("brief_enabled", "coach_enabled", "coach_day"):
        if getattr(body, k) is not None:
            c[k] = getattr(body, k)
    if body.brief_time is not None:
        c["last_brief"] = ""  # a new time applies from today
    state.put(db, "ai_jobs", c, by=user.username)
    audit(db, "ai_jobs.updated", user=user, ip=client_ip(request), details={k: c[k] for k in ("brief_enabled", "brief_time", "coach_enabled", "coach_day", "coach_time", "timezone")})
    db.commit()
    return get_jobs(user, db)


@router.post("/ai-jobs/brief")
def brief_now(user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    _limit(user, "brief", 20)
    text = ai_jobs.morning_brief(db, force=True)
    db.commit()
    return {"text": text}


@router.post("/ai-jobs/coach")
def coach_now(user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    _limit(user, "coach", 20)
    text = ai_jobs.weekly_coach(db, force=True)
    db.commit()
    return {"text": text}


# ------------------------------------------------------------------ ask the chart
class ChartIn(BaseModel):
    symbol: str = Field(max_length=32)
    tf: str = Field(default="H1", max_length=4)


@router.post("/intel/chart-explain")
def chart_explain(body: ChartIn, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    from ..services.chart import CHART_TFS
    if body.tf not in CHART_TFS:
        raise HTTPException(400, "Unknown timeframe")
    _limit(user, "chart", 8)
    try:
        return ai_jobs.explain_chart(db, body.symbol.upper(), body.tf)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ Telegram bot
@router.get("/tgbot")
def get_bot(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    b = state.get(db, "tgbot")
    tg = state.get(db, "alert_prefs").get("telegram", {})
    return {"enabled": bool(b.get("enabled")), "has_pin": bool(b.get("pin_hash")), "telegram_ready": bool(tg.get("bot_token_enc") and tg.get("chat_id"))}


class BotIn(BaseModel):
    enabled: bool | None = None
    pin: str | None = Field(default=None, max_length=12)
    password: str | None = None


@router.put("/tgbot")
def put_bot(body: BotIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    from ..services import tgbot
    from .system import _confirm_password
    if body.pin is not None:
        if not re.fullmatch(r"\d{4,8}", body.pin):
            raise HTTPException(400, "PIN must be 4–8 digits")
        if len(set(body.pin)) == 1 or body.pin in "0123456789" or body.pin in "9876543210":
            raise HTTPException(400, "PIN is too easy to guess")
        _confirm_password(user, body.password)
        tgbot.set_pin(db, body.pin)
    if body.enabled is not None:
        b = state.get_locked(db, "tgbot")
        b["enabled"] = body.enabled
        state.put(db, "tgbot", b, by=user.username)
    audit(db, "tgbot.updated", user=user, ip=client_ip(request), details={"enabled": body.enabled, "pin_changed": body.pin is not None})
    db.commit()
    return get_bot(user, db)


# ------------------------------------------------------------------ backups
RESTORE = ("To restore: 1) download the .qvx file from your Telegram; 2) on the server run "
           "`docker compose exec -T -e BACKUP_PASSWORD='your-backup-password' worker python -m app.backup_tool decrypt < FILE.qvx > restore.sql.gz`; "
           "3) `./deploy/restore.sh restore.sql.gz`. Without the backup password the file cannot be opened — keep it safe (not only in this app). "
           "On a NEW server you also need the old SECRET_KEY from /opt/trading_app/.env, otherwise saved API keys/tokens must be re-entered: "
           "keep a copy of .env in your password manager.")


@router.get("/backup")
def get_backup(user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    c = state.get(db, "backup")
    return {"offsite_telegram": bool(c.get("offsite_telegram")), "has_password": bool(c.get("password_enc")),
            "status": state.get(db, "backup_status"), "restore": RESTORE}


class BackupIn(BaseModel):
    offsite_telegram: bool | None = None
    backup_password: str | None = Field(default=None, max_length=200)
    password: str | None = None


@router.put("/backup")
def put_backup(body: BackupIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    from .system import _confirm_password
    c = state.get_locked(db, "backup")
    if body.backup_password is not None:
        if len(body.backup_password) < 10:
            raise HTTPException(400, "Backup password: at least 10 characters")
        _confirm_password(user, body.password)
        c["password_enc"] = encrypt(body.backup_password)
    if body.offsite_telegram is not None:
        if body.offsite_telegram and not c.get("password_enc"):
            raise HTTPException(400, "Set a backup password first")
        c["offsite_telegram"] = body.offsite_telegram
    state.put(db, "backup", c, by=user.username)
    audit(db, "backup.settings", user=user, ip=client_ip(request), details={"offsite_telegram": c.get("offsite_telegram"), "password_changed": body.backup_password is not None})
    db.commit()
    return get_backup(user, db)


# ------------------------------------------------------------------ security checklist
@router.get("/security/checklist")
def security_checklist(user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    """What the app can verify about its own security settings (no secrets returned)."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from ..config import settings
    from ..models import LoginAttempt, UserSession, utcnow
    from ..services import automation
    from ..services.crypto import desk
    from ..services.livegate import is_approved
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()
    admins = [u for u in users if u.is_admin]
    day = utcnow() - timedelta(hours=24)
    fails = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.at >= day, LoginAttempt.success.is_(False))) or 0
    sessions = db.scalar(select(func.count()).select_from(UserSession).where(UserSession.revoked.is_(False), UserSession.expires_at > utcnow())) or 0
    tg = state.get(db, "alert_prefs").get("telegram", {})
    bot = state.get(db, "tgbot")
    bk = state.get(db, "backup")
    flags = automation.flags_of(state.get(db, "controls"))
    cc = state.get(db, "crypto")
    st = state.get(db, "crypto_status")
    items = [
        ("2fa_admins", "All admins use two-factor login", all(u.totp_enabled for u in admins), f"{sum(u.totp_enabled for u in admins)}/{len(admins)} admins have 2FA", "#/account"),
        ("2fa_required", "2FA is required for every user", all(u.require_2fa or u.totp_enabled for u in users), "Users page → edit user → require 2FA", "#/users"),
        ("few_users", "Only the people who need access", len(users) <= 3, f"{len(users)} active user(s): " + ", ".join(u.username for u in users), "#/users"),
        ("default_admin", "No unused default 'admin' account", not any(u.username == "admin" and not u.last_login_at for u in users), "disable the bootstrap 'admin' user if you use another account", "#/users"),
        ("login_attacks", "No login attacks in the last 24 h", fails < 10, f"{fails} failed login(s) in 24 h", "#/audit"),
        ("sessions", "Few open sessions", sessions <= 6, f"{sessions} active session(s) — sign out old ones on My account", "#/account"),
        ("session_timeout", "Short idle timeout", settings.session_idle_minutes <= 60, f"idle timeout {settings.session_idle_minutes} min", None),
        ("cookies", "Secure cookies (HTTPS only)", settings.cookie_secure, "COOKIE_SECURE=true in .env", None),
        ("bot_pin", "Telegram bot protected by a PIN", (not bot.get("enabled")) or bool(bot.get("pin_hash")), "Alerts → Telegram bot → set PIN", "#/alerts"),
        ("telegram_private", "Telegram alerts go to a private chat", not str(tg.get("chat_id", "")).startswith("-"), "a chat id starting with '-' is a group — anyone in it reads your alerts", "#/alerts"),
        ("backup_offsite", "Encrypted off-server backup on", bool(bk.get("offsite_telegram") and bk.get("password_enc")), "System → Backups", "#/settings"),
        ("live_locked", "Real money locked until you decide", not is_approved(db) and not flags.get("MT5_LIVE") and not flags.get("CRYPTO_LIVE"),
         "live gate approved" if is_approved(db) else "live gate not approved — real-money orders impossible", "#/risk"),
        ("exchange_keys", "Exchange keys cannot withdraw", (not desk.has_keys(db, cc.get("exchange", "binance"))) or not st.get("withdraw_enabled"),
         "create keys with trading only, no withdrawals, IP-restricted to 37.60.252.21", "#/brokers"),
    ]
    out = [{"key": k, "label": lab, "ok": bool(okv), "detail": det, "link": link} for k, lab, okv, det, link in items]
    return {"items": out, "score": sum(i["ok"] for i in out), "total": len(out),
            "server_check": "On the server run: sudo /opt/trading_app/deploy/security-check.sh (read-only) and send the output for review."}
