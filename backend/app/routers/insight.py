"""Time Machine odds and AI bull/bear review of setups (ideas from day-trader-skill and TradingAgents)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import AIReview, Signal, SignalAssessment, User
from ..services import ai_review, timemachine

router = APIRouter(prefix="/api/insight", tags=["insight"])

# how strict each setting is (higher = more protective); loosening needs the password
_MODE_RANK = {"OFF": 0, "LOG_ONLY": 0, "VETO": 2}
_NEUTRAL_RANK = {"ALLOW": 0, "HALF": 1, "VETO": 2}


@router.get("/settings")
def get_settings(user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    c = ai_review.cfg(db)
    return {"time_machine": state.get(db, "timemachine"), "ai_review": c, "ai_ready": ai_review.ai_ready(db),
            "reviews_left_today": ai_review.budget_left(db), "modes": ai_review.MODES, "styles": ai_review.STYLES,
            "neutral_actions": ai_review.NEUTRAL_ACTIONS}


class SettingsIn(BaseModel):
    tm_enabled: bool | None = None
    tm_gate: bool | None = None
    mode: str | None = None
    style: str | None = None
    neutral_action: str | None = None
    min_confidence: int | None = Field(default=None, ge=0, le=100)
    timeout_s: int | None = Field(default=None, ge=10, le=120)
    max_per_day: int | None = Field(default=None, ge=0, le=500)
    password: str | None = None


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, user: User = Depends(require("strategy.edit")), db: Session = Depends(get_db)):
    from .system import _confirm_password
    tm = state.get_locked(db, "timemachine")
    c = state.get_locked(db, "ai_review")
    looser = False
    if body.tm_enabled is not None:
        looser |= bool(tm.get("enabled", True) and not body.tm_enabled and tm.get("gate"))
        tm["enabled"] = body.tm_enabled
    if body.tm_gate is not None:
        looser |= bool(tm.get("gate")) and not body.tm_gate
        tm["gate"] = body.tm_gate
        if body.tm_gate:
            tm["enabled"] = True
    if body.mode is not None:
        if body.mode not in ai_review.MODES:
            raise HTTPException(400, f"mode must be one of {', '.join(ai_review.MODES)}")
        if body.mode == "VETO" and not ai_review.ai_ready(db):
            raise HTTPException(400, "Set up the AI assistant first (AI Assistant page) — VETO mode would otherwise block every auto trade")
        looser |= _MODE_RANK[body.mode] < _MODE_RANK.get(c.get("mode", "OFF"), 0)
        c["mode"] = body.mode
    if body.style is not None:
        if body.style not in ai_review.STYLES:
            raise HTTPException(400, "style must be debate or quick")
        c["style"] = body.style
    if body.neutral_action is not None:
        if body.neutral_action not in ai_review.NEUTRAL_ACTIONS:
            raise HTTPException(400, "neutral_action must be HALF, VETO or ALLOW")
        looser |= _NEUTRAL_RANK[body.neutral_action] < _NEUTRAL_RANK.get(c.get("neutral_action", "HALF"), 1)
        c["neutral_action"] = body.neutral_action
    if body.min_confidence is not None:
        looser |= body.min_confidence > int(c.get("min_confidence", 60))  # a higher bar = fewer vetoes
        c["min_confidence"] = body.min_confidence
    if body.timeout_s is not None:
        c["timeout_s"] = body.timeout_s
    if body.max_per_day is not None:
        c["max_per_day"] = body.max_per_day
    if looser:
        _confirm_password(user, body.password)
    state.put(db, "timemachine", tm, by=user.username)
    state.put(db, "ai_review", c, by=user.username)
    audit(db, "insight.settings", user=user, ip=client_ip(request),
          details={**{k: v for k, v in body.model_dump().items() if k != "password" and v is not None}, "loosened": looser})
    db.commit()
    return get_settings(user, db)


def _signal(db: Session, signal_id: int) -> Signal:
    sig = db.get(Signal, signal_id)
    if sig is None or sig.action not in ("BUY", "SELL"):
        raise HTTPException(404, "No trade setup for this signal")
    return sig


@router.get("/stats/ai")
def ai_stats(user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    return ai_review.stats(db)


@router.get("/{signal_id}")
def get_insight(signal_id: int, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    sig = _signal(db, signal_id)
    a = db.get(SignalAssessment, signal_id)
    return {"signal_id": signal_id, "time_machine": ((a.economics or {}).get("time_machine") if a else None),
            "ai_review": ai_review.as_dict(db.get(AIReview, signal_id)), "ai_mode": ai_review.cfg(db).get("mode"),
            "symbol": sig.symbol, "side": sig.action}


@router.post("/{signal_id}/time-machine")
def run_time_machine(signal_id: int, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    sig = _signal(db, signal_id)
    out = timemachine.for_signal(db, sig)
    if out is None:
        raise HTTPException(400, "This setup has no stop, target or standard timeframe")
    return out


@router.post("/{signal_id}/ai-review")
def run_ai_review(signal_id: int, request: Request, user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    """Manual review (information only — it does not change an order that is already placed or waiting)."""
    sig = _signal(db, signal_id)
    if not ai_review.ai_ready(db):
        raise HTTPException(400, "Turn on the AI assistant first (AI Assistant page)")
    rv = ai_review.run(db, sig, mode="MANUAL")
    audit(db, "insight.ai_review", user=user, ip=client_ip(request), details={"signal_id": signal_id, "rating": rv.rating})
    db.commit()
    return ai_review.as_dict(rv)


class ManualTM(BaseModel):
    symbol: str = Field(max_length=32)
    timeframe: str = "H1"
    side: str
    entry: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    strategy: str | None = None


@router.post("/time-machine")
def manual_time_machine(body: ManualTM, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    from ..services import market_data as md
    from ..services.strategy import STRATEGIES
    if body.side not in ("BUY", "SELL") or body.timeframe not in md.TF_SECONDS:
        raise HTTPException(400, "side must be BUY or SELL and timeframe a standard one")
    buy = body.side == "BUY"
    if (buy and not body.stop_loss < body.entry < body.take_profit) or (not buy and not body.take_profit < body.entry < body.stop_loss):
        raise HTTPException(400, "For BUY: stop < entry < target. For SELL: target < entry < stop.")
    if body.strategy and body.strategy not in STRATEGIES:
        raise HTTPException(400, "Unknown strategy")
    return timemachine.for_manual(db, body.symbol.upper(), body.timeframe, body.side, body.entry, body.stop_loss, body.take_profit,
                                  body.strategy)
