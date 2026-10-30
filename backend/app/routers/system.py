from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit, event
from ..config import settings
from ..db import get_db
from ..deps import active_user, client_ip, require
from ..models import Candle, Order, Position, Quote, User, utcnow
from ..security import user_has, verify_password
from ..services import trading
from ..services.market_data import TF_SECONDS
from ..services.notifier import notify, telegram_configured

router = APIRouter(prefix="/api/system", tags=["system"])

LIVE_CONFIRM_PHRASE = "ENABLE LIVE TRADING"


def _confirm_password(user: User, password: str | None) -> None:
    if not password or not verify_password(password, user.password_hash):
        raise HTTPException(403, "Password confirmation failed")


@router.get("/status")
def status(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    tr = state.get(db, "trading")
    mode = tr["mode"]
    acct_mode = mode if mode in trading.ORDER_MODES else "PAPER"
    acct, meta = trading.account_state(db, acct_mode)
    limits = trading.check_loss_limits(db, acct_mode) if acct else None
    db.commit()
    bridge = state.get(db, "bridge")
    last_seen = bridge.get("last_seen")
    bridge_age = (utcnow() - datetime.fromisoformat(last_seen)).total_seconds() if last_seen else None
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    realized_today = db.scalar(
        select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(
            Position.mode == acct_mode, Position.status == "CLOSED", Position.closed_at >= day_start)
    )
    open_pos = trading.open_positions(db, acct_mode)
    return {
        "server_time": utcnow(),
        "trading": tr,
        "data": state.get(db, "data"),
        "account_mode": acct_mode,
        "account": acct.__dict__ if acct else None,
        "account_meta": meta,
        "limits": limits,
        "realized_today": float(realized_today or 0),
        "unrealized": round(sum(p.unrealized_pnl or 0 for p in open_pos), 2),
        "open_positions": len(open_pos),
        "bridge": {**bridge, "age_s": bridge_age, "configured": bool(settings.bridge_secret)},
        "reconciliation": state.get(db, "reconciliation"),
        "live_gate": state.get(db, "live_gate"),
        "telegram_configured": telegram_configured(),
        "strategy": {k: state.get(db, "strategy")[k] for k in ("name", "version", "symbols", "timeframes", "trade_timeframes")},
    }


class ModeIn(BaseModel):
    mode: str
    password: str | None = None
    confirm: str | None = None


@router.post("/mode")
def set_mode(body: ModeIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    if body.mode not in state.TRADING_MODES:
        raise HTTPException(400, "Unknown mode")
    tr = state.get_locked(db, "trading")
    data = state.get(db, "data")
    if body.mode in trading.MT5_MODES and data["source"] != "mt5":
        raise HTTPException(400, "Broker modes require broker data (cTrader/MT5), not the simulator")
    if body.mode == "MT5_LIVE":
        if not user_has(user, "live.approve"):
            raise HTTPException(403, "Missing permission: live.approve")
        gate = state.get(db, "live_gate")
        if not gate.get("approved"):
            raise HTTPException(400, "Live-trading gate has not been approved")
        _confirm_password(user, body.password)
        if (body.confirm or "").strip() != LIVE_CONFIRM_PHRASE:
            raise HTTPException(400, f'Type "{LIVE_CONFIRM_PHRASE}" to confirm')
    elif body.mode in trading.MT5_MODES:
        _confirm_password(user, body.password)
    old = tr["mode"]
    tr["mode"] = body.mode
    state.put(db, "trading", tr, by=user.username)
    if old != body.mode:
        # Pending opening orders belong to the old mode: cancel them.
        for o in db.scalars(select(Order).where(Order.status == "PENDING", Order.action == "OPEN")).all():
            o.status, o.error = "CANCELLED", f"mode changed {old} -> {body.mode}"
    audit(db, "trading.mode_changed", user=user, ip=client_ip(request), details={"from": old, "to": body.mode})
    notify(f"Trading mode changed {old} → {body.mode} by {user.username}")
    db.commit()
    return state.get(db, "trading")


class KillIn(BaseModel):
    engaged: bool
    reason: str = Field(default="", max_length=500)
    close_positions: bool = False


@router.post("/kill-switch")
def kill_switch(body: KillIn, request: Request, user: User = Depends(require("killswitch.use")), db: Session = Depends(get_db)):
    tr = state.get_locked(db, "trading")
    tr["kill_switch"] = body.engaged
    tr["kill_switch_reason"] = body.reason if body.engaged else ""
    state.put(db, "trading", tr, by=user.username)
    cancelled = closed = 0
    if body.engaged:
        for o in db.scalars(select(Order).where(Order.status == "PENDING", Order.action == "OPEN")).all():
            o.status, o.error = "CANCELLED", "kill switch engaged"
            cancelled += 1
        if body.close_positions:
            for p in db.scalars(select(Position).where(Position.status == "OPEN")).all():
                trading.request_close(db, p, "kill_switch", user.username)
                closed += 1
    audit(db, "trading.kill_switch", user=user, ip=client_ip(request),
          details={"engaged": body.engaged, "reason": body.reason, "cancelled": cancelled, "close_requested": closed})
    notify(f"{'🛑 KILL SWITCH ENGAGED' if body.engaged else '✅ Kill switch released'} by {user.username}. {body.reason}")
    db.commit()
    return {"trading": state.get(db, "trading"), "cancelled_orders": cancelled, "positions_closing": closed}


class UnlockIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/unlock")
def unlock(body: UnlockIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    tr = state.get_locked(db, "trading")
    prev = tr["lock_reason"]
    tr.update(locked=False, lock_reason="")
    state.put(db, "trading", tr, by=user.username)
    rec = state.get(db, "reconciliation")
    rec.update(ok=True, detail="", at=utcnow().isoformat())
    state.put(db, "reconciliation", rec)
    audit(db, "trading.unlocked", user=user, ip=client_ip(request), details={"previous_reason": prev, "note": body.note})
    db.commit()
    # If a loss limit is still breached the next engine tick locks again.
    return state.get(db, "trading")


# ------------------------------------------------------------------ risk
@router.get("/risk")
def get_risk(user: User = Depends(active_user), db: Session = Depends(get_db)):
    return {"risk": state.get(db, "risk"), "bounds": state.RISK_BOUNDS, "locked_keys": sorted(state.RISK_LOCKED_KEYS)}


class RiskIn(BaseModel):
    values: dict
    password: str | None = None


@router.put("/risk")
def put_risk(body: RiskIn, request: Request, user: User = Depends(require("risk.edit")), db: Session = Depends(get_db)):
    cur = state.get_locked(db, "risk")
    new = dict(cur)
    increases = []
    for k, v in body.values.items():
        if k in state.RISK_LOCKED_KEYS:
            if bool(v) != bool(cur[k]):
                raise HTTPException(400, f"'{k}' is a fixed safety setting and cannot be changed")
            continue
        if k not in state.DEFAULTS["risk"]:
            raise HTTPException(400, f"Unknown risk setting '{k}'")
        if isinstance(state.DEFAULTS["risk"][k], bool):
            new[k] = bool(v)
            if k == "include_unrealized_in_limits" and cur[k] and not new[k]:
                increases.append(k)
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"'{k}' must be a number")
        lo, hi = state.RISK_BOUNDS.get(k, (None, None))
        if lo is not None and not (lo <= v <= hi):
            raise HTTPException(400, f"'{k}' must be between {lo} and {hi}")
        if isinstance(state.DEFAULTS["risk"][k], int):
            v = int(v)
        if (k in state.RISK_INCREASING_UP and v > cur[k]) or (k in state.RISK_INCREASING_DOWN and v < cur[k]) \
                or (k == "reference_balance" and v > cur[k]):
            increases.append(k)
        new[k] = v
    if new["weekly_loss_pct"] < new["daily_loss_pct"]:
        raise HTTPException(400, "Weekly loss limit must be >= daily loss limit")
    if increases:
        _confirm_password(user, body.password)
    changes = {k: {"from": cur[k], "to": new[k]} for k in new if new[k] != cur.get(k)}
    state.put(db, "risk", new, by=user.username)
    audit(db, "risk.updated", user=user, ip=client_ip(request), details={"changes": changes, "risk_increase": increases})
    if increases:
        notify(f"⚠️ Risk settings increased by {user.username}: {', '.join(increases)}")
    db.commit()
    return {"risk": new, "risk_increase_confirmed": increases}


# -------------------------------------------------------------- strategy
@router.get("/strategy")
def get_strategy(user: User = Depends(active_user), db: Session = Depends(get_db)):
    return state.get(db, "strategy")


class StrategyIn(BaseModel):
    values: dict


STRATEGY_NUMERIC = {
    "ema_fast": (2, 400), "ema_slow": (5, 1000), "rsi_period": (2, 100), "rsi_long_min": (0, 100),
    "rsi_long_max": (0, 100), "rsi_short_min": (0, 100), "rsi_short_max": (0, 100), "macd_fast": (2, 100),
    "macd_slow": (3, 200), "macd_signal": (2, 100), "atr_period": (2, 100), "atr_stop_mult": (0.3, 10),
    "reward_risk": (0.5, 10), "max_atr_pct": (0.01, 10),
}


def _bump(version: str) -> str:
    try:
        a, b, c = (int(x) for x in version.split("."))
        return f"{a}.{b}.{c + 1}"
    except ValueError:
        return version + ".1"


@router.put("/strategy")
def put_strategy(body: StrategyIn, request: Request, user: User = Depends(require("strategy.edit")), db: Session = Depends(get_db)):
    cur = state.get_locked(db, "strategy")
    new = dict(cur)
    for k, v in body.values.items():
        if k in STRATEGY_NUMERIC:
            lo, hi = STRATEGY_NUMERIC[k]
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"'{k}' must be a number")
            if not lo <= v <= hi:
                raise HTTPException(400, f"'{k}' must be between {lo} and {hi}")
            new[k] = int(v) if isinstance(state.DEFAULTS["strategy"][k], int) else v
        elif k == "symbols":
            syms = [s.strip().upper() for s in v if s.strip()]
            if not syms or len(syms) > 10 or not all(s.isalnum() and 3 <= len(s) <= 16 for s in syms):
                raise HTTPException(400, "Provide 1-10 valid symbols")
            new[k] = syms
        elif k in ("timeframes", "trade_timeframes"):
            if not all(t in TF_SECONDS for t in v):
                raise HTTPException(400, f"Timeframes must be among {list(TF_SECONDS)}")
            new[k] = list(dict.fromkeys(v))
        elif k in ("name", "version"):
            continue
        else:
            raise HTTPException(400, f"Unknown strategy setting '{k}'")
    if new["ema_fast"] >= new["ema_slow"]:
        raise HTTPException(400, "ema_fast must be smaller than ema_slow")
    if new["macd_fast"] >= new["macd_slow"]:
        raise HTTPException(400, "macd_fast must be smaller than macd_slow")
    if not set(new["trade_timeframes"]) <= set(new["timeframes"]):
        raise HTTPException(400, "trade_timeframes must be a subset of timeframes")
    rule_keys = set(STRATEGY_NUMERIC)
    if any(new[k] != cur[k] for k in rule_keys):
        new["version"] = _bump(cur["version"])
    changes = {k: {"from": cur[k], "to": new[k]} for k in new if new[k] != cur.get(k)}
    state.put(db, "strategy", new, by=user.username)
    audit(db, "strategy.updated", user=user, ip=client_ip(request), details=changes)
    db.commit()
    return new


# ------------------------------------------------------------- live gate
class GateIn(BaseModel):
    items: dict
    approve: bool = False
    password: str | None = None


GATE_ITEMS = {
    "broker_legal_checked": "Broker/legal eligibility checks complete (incl. Saudi CMA guidance)",
    "strategy_oos_documented": "Strategy has documented, reproducible out-of-sample results",
    "demo_results_satisfactory": "Demo results and execution reconciliation are satisfactory (2-4+ weeks)",
    "risk_failure_tests_passed": "Risk engine and kill switch passed failure testing",
    "credentials_secured": "Credentials and network access are secured",
}


@router.get("/live-gate")
def get_gate(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    return {"gate": state.get(db, "live_gate"), "items": GATE_ITEMS, "confirm_phrase": LIVE_CONFIRM_PHRASE}


@router.put("/live-gate")
def put_gate(body: GateIn, request: Request, user: User = Depends(require("live.approve")), db: Session = Depends(get_db)):
    gate = state.get_locked(db, "live_gate")
    for k, v in body.items.items():
        if k in GATE_ITEMS:
            gate[k] = bool(v)
    all_ok = all(gate[k] for k in GATE_ITEMS)
    if body.approve:
        if not all_ok:
            raise HTTPException(400, "All gate items must be checked before approval")
        _confirm_password(user, body.password)
        gate.update(approved=True, approved_by=user.username, approved_at=utcnow().isoformat())
    if not all_ok and gate.get("approved"):
        gate.update(approved=False, approved_by="", approved_at="")
        tr = state.get_locked(db, "trading")
        if tr["mode"] == "MT5_LIVE":
            tr["mode"] = "MT5_DEMO"
            state.put(db, "trading", tr, by=user.username)
            event(db, "risk", "Live gate revoked: mode switched to MT5_DEMO", "WARNING")
    state.put(db, "live_gate", gate, by=user.username)
    audit(db, "live_gate.updated", user=user, ip=client_ip(request), details=gate)
    db.commit()
    return {"gate": gate}


# --------------------------------------------------------- data & notify
class DataIn(BaseModel):
    source: str
    stale_quote_seconds: int | None = Field(default=None, ge=10, le=3600)
    stale_heartbeat_seconds: int | None = Field(default=None, ge=10, le=3600)


@router.put("/data")
def put_data(body: DataIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    if body.source not in ("mt5", "simulator"):
        raise HTTPException(400, "source must be 'mt5' or 'simulator'")
    data = state.get_locked(db, "data")
    tr = state.get(db, "trading")
    purged = 0
    if body.source != data["source"]:
        if tr["mode"] not in ("OFF", "SIGNALS_ONLY"):
            raise HTTPException(400, "Switch trading mode to OFF or SIGNALS_ONLY before changing the data source")
        if db.scalar(select(func.count()).select_from(Position).where(Position.status == "OPEN")):
            raise HTTPException(400, "Close all open positions before changing the data source")
        # Never mix synthetic and broker data.
        purged = db.execute(delete(Candle).where(Candle.source != body.source)).rowcount
        db.execute(delete(Quote).where(Quote.source != body.source))
    data["source"] = body.source
    if body.stale_quote_seconds:
        data["stale_quote_seconds"] = body.stale_quote_seconds
    if body.stale_heartbeat_seconds:
        data["stale_heartbeat_seconds"] = body.stale_heartbeat_seconds
    state.put(db, "data", data, by=user.username)
    audit(db, "data.updated", user=user, ip=client_ip(request), details={**data, "purged_candles": purged})
    db.commit()
    return data


class NotifyIn(BaseModel):
    telegram_enabled: bool
    notify_signals: bool
    notify_orders: bool


@router.put("/notifications")
def put_notify(body: NotifyIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    state.put(db, "notifications", body.model_dump(), by=user.username)
    audit(db, "notifications.updated", user=user, ip=client_ip(request), details=body.model_dump())
    db.commit()
    return body.model_dump()


@router.get("/notifications")
def get_notify(user: User = Depends(active_user), db: Session = Depends(get_db)):
    return {**state.get(db, "notifications"), "telegram_configured": telegram_configured()}


@router.post("/notifications/test")
def test_notify(user: User = Depends(require("system.settings"))):
    if not telegram_configured():
        raise HTTPException(400, "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first")
    notify(f"Test notification from {user.username}")
    return {"status": "sent"}


class PaperResetIn(BaseModel):
    balance: float = Field(ge=10, le=1_000_000)
    password: str


@router.post("/paper/reset")
def paper_reset(body: PaperResetIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    _confirm_password(user, body.password)
    if trading.open_positions(db, "PAPER"):
        raise HTTPException(400, "Close open paper positions first")
    state.put(db, "paper_account", {"balance": body.balance, "currency": "USD"}, by=user.username)
    anchors = state.get(db, "anchors")
    anchors.pop("PAPER", None)
    state.put(db, "anchors", anchors)
    audit(db, "paper.reset", user=user, ip=client_ip(request), details={"balance": body.balance})
    db.commit()
    return {"status": "ok"}
