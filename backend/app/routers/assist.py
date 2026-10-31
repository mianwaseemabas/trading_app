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
           "3) `./deploy/restore.sh restore.sql.gz`. Without the backup password the file cannot be opened — keep it safe (not only in this app).")


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
