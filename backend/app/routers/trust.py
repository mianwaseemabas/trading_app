"""Trust & intelligence API: data health, segregated accounts, alerts, signal assessment, strategy comparison, risk status."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import (Alert, AlertDelivery, Order, Position, Signal, SignalApproval, SignalAssessment, SignalEvent, SignalOutcome,
                      User, utcnow)
from ..secretbox import encrypt
from ..services import alerts as al
from ..services import assess, quality, trading

router = APIRouter(prefix="/api", tags=["trust"])


@router.get("/data/health")
def data_health(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    return quality.data_health(db)


@router.get("/accounts")
def accounts(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    out = quality.accounts(db)
    db.rollback()
    return {"accounts": out, "note": "Each environment is separate. Balances are never added together."}


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def list_alerts(unread: bool = False, type: str | None = None, limit: int = Query(100, le=500),
                user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    q = select(Alert).order_by(Alert.id.desc()).limit(limit)
    if unread:
        q = q.where(Alert.read_at.is_(None))
    if type:
        q = q.where(Alert.type.in_(type.split(",")))
    rows = db.scalars(q).all()
    dels = defaultdict(list)
    for d in db.scalars(select(AlertDelivery).where(AlertDelivery.alert_id.in_([a.id for a in rows] or [0]))).all():
        dels[d.alert_id].append({"channel": d.channel, "status": d.status, "detail": d.detail, "attempts": d.attempts, "at": d.at})
    return {"unread": al.unread_count(db), "alerts": [
        {"id": a.id, "at": a.at, "type": a.type, "severity": a.severity, "title": a.title, "body": a.body, "symbol": a.symbol,
         "signal_id": a.signal_id, "link": a.link, "read": a.read_at is not None, "deliveries": dels.get(a.id, [])} for a in rows]}


@router.get("/alerts/count")
def alert_count(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    return {"unread": al.unread_count(db)}


class ReadIn(BaseModel):
    ids: list[int] = []
    all: bool = False


@router.post("/alerts/read")
def mark_read(body: ReadIn, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    q = select(Alert).where(Alert.read_at.is_(None))
    if not body.all:
        q = q.where(Alert.id.in_(body.ids or [0]))
    for a in db.scalars(q).all():
        a.read_at = utcnow()
    db.commit()
    return {"unread": al.unread_count(db)}


@router.get("/alerts/preferences")
def get_prefs(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    p = al.prefs(db)
    return {"channels": p.get("channels", {}), "types": al.TYPES,
            "telegram": {"chat_id": p.get("telegram", {}).get("chat_id", ""), "has_token": bool(p.get("telegram", {}).get("bot_token_enc"))},
            "email": {k: v for k, v in p.get("email", {}).items() if k != "password_enc"} | {"has_password": bool(p.get("email", {}).get("password_enc"))}}


class PrefsIn(BaseModel):
    channels: dict[str, list[str]] | None = None
    telegram_token: str | None = Field(default=None, max_length=200)
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    email: dict | None = None


@router.post("/alerts/preferences")
def put_prefs(body: PrefsIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    p = state.get_locked(db, "alert_prefs")
    if body.channels is not None:
        p["channels"] = {ch: [t for t in v if t in al.TYPES] for ch, v in body.channels.items() if ch in ("telegram", "email")}
    tg = p.setdefault("telegram", {})
    if body.telegram_token:
        tg["bot_token_enc"] = encrypt(body.telegram_token.strip())
    if body.telegram_chat_id is not None:
        tg["chat_id"] = body.telegram_chat_id.strip()
    if body.email is not None:
        e = p.setdefault("email", {})
        for k in ("host", "port", "username", "from", "to", "starttls"):
            if k in body.email:
                e[k] = body.email[k]
        if body.email.get("password"):
            e["password_enc"] = encrypt(body.email["password"])
    state.put(db, "alert_prefs", p, by=user.username)
    audit(db, "alerts.preferences", user=user, ip=client_ip(request), details={"channels": {k: len(v) for k, v in p.get("channels", {}).items()}})
    db.commit()
    return get_prefs(user, db)


class TestIn(BaseModel):
    channel: str


@router.post("/alerts/test")
def test_channel(body: TestIn, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    text = f"Qivynex test alert from {user.username} — delivery works. Alerts are information only, never orders."
    ok, detail = al._telegram(db, text) if body.channel == "telegram" else al._email(db, "Qivynex test alert", text)
    return {"ok": ok, "detail": detail}


# ----------------------------------------------------------------- signals
@router.get("/signal-board")
def signal_board(tab: str = "all", limit: int = Query(150, le=500), user: User = Depends(require("signals.view")),
                 db: Session = Depends(get_db)):
    """Signals with score, verdict, economics and shadow outcome — tabs: all/qualified/watch/rejected/expired/completed."""
    q = select(Signal, SignalAssessment, SignalOutcome).outerjoin(SignalAssessment, SignalAssessment.signal_id == Signal.id) \
        .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id).where(Signal.action != "NONE").order_by(Signal.id.desc()).limit(limit)
    tabs = {"qualified": Signal.status.in_(["AWAITING", "ORDERED"]), "watch": Signal.status == "WATCH",
            "rejected": Signal.status.in_(["REJECTED", "BLOCKED_DATA", "DECLINED"]), "expired": Signal.status == "EXPIRED",
            "completed": SignalOutcome.status.in_(["TARGET", "STOP", "EXPIRED"])}
    if tab in tabs:
        q = q.where(tabs[tab])
    out = []
    for s, a, o in db.execute(q).all():
        e = (a.economics if a else {}) or {}
        out.append({"id": s.id, "candle_time": s.candle_time, "symbol": s.symbol, "timeframe": s.timeframe, "side": s.action,
                    "strategy": s.strategy, "status": s.status, "mode": s.trading_mode, "entry": s.entry_ref, "stop": s.stop_loss,
                    "target": s.take_profit, "score": a.score if a else None, "tier": a.tier if a else None,
                    "verdict": a.verdict if a else None, "reliability": a.reliability if a else None,
                    "net_rr": e.get("net_rr"), "max_loss": e.get("net_stop"), "net_gain": e.get("net_target"),
                    "spread": (a.data or {}).get("spread_pips") or (a.data or {}).get("spread_bps") if a else None,
                    "spread_unit": "pips" if a and (a.data or {}).get("spread_pips") is not None else "bps",
                    "data_age_s": (a.data or {}).get("quote_age_s") if a else None,
                    "reason": (a.reasons[0] if a and a.reasons else ""), "outcome": o.status if o else None,
                    "outcome_r": o.r_net if o else None})
    counts = dict(db.execute(select(Signal.status, func.count()).where(Signal.action != "NONE",
                                                                       Signal.created_at >= utcnow().replace(hour=0, minute=0, second=0)).group_by(Signal.status)).all())
    return {"signals": out, "today": counts}


@router.get("/signals/{signal_id}/assessment")
def signal_assessment(signal_id: int, refresh: bool = False, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    s = db.get(Signal, signal_id)
    if s is None or s.action == "NONE":
        raise HTTPException(404, "No setup for this signal")
    a = db.get(SignalAssessment, signal_id)
    if a is None or refresh:
        a = assess.assess(db, s, save=False)
        db.rollback()
    o = db.get(SignalOutcome, signal_id)
    ev = db.scalars(select(SignalEvent).where(SignalEvent.signal_id == signal_id).order_by(SignalEvent.id)).all()
    appr = db.get(SignalApproval, signal_id)
    orders = db.scalars(select(Order).where(Order.signal_id == signal_id)).all()
    return {"signal_id": signal_id, "score": a.score, "tier": a.tier, "verdict": a.verdict, "reliability": a.reliability,
            "breakdown": a.breakdown, "gates": a.gates, "reasons": a.reasons, "economics": a.economics, "data": a.data,
            "model_version": a.model_version, "assessed_at": a.at, "live": refresh,
            "score_note": "Quality score from fixed rules (0–100). It is NOT a probability of profit.",
            "lifecycle": [{"at": e.at, "state": e.state, "detail": e.detail} for e in ev],
            "approval": {"status": appr.status, "by": appr.decided_by, "note": appr.note} if appr else None,
            "orders": [{"client_id": x.client_id, "status": x.status, "fill": x.fill_price, "error": x.error} for x in orders],
            "outcome": {"status": o.status, "r_gross": o.r_gross, "r_net": o.r_net, "bars": o.bars, "mfe_r": o.max_favorable_r,
                        "mae_r": o.max_adverse_r, "traded": o.traded, "resolved_at": o.resolved_at} if o else None}


@router.get("/signals/{signal_id}/economics")
def signal_economics(signal_id: int, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    r = signal_assessment(signal_id, True, user, db)
    return {"economics": r["economics"], "verdict": r["verdict"], "reliability": r["reliability"]}


@router.get("/strategies/compare")
def compare(user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    """Baseline vs challenger on shadow outcomes (every signal, traded or not), net of estimated costs."""
    from ..services.strategy import STRATEGIES
    out = []
    for name in STRATEGIES:
        st = assess.comparable(db, name)
        rows = db.execute(select(SignalOutcome.traded, func.count()).join(Signal, Signal.id == SignalOutcome.signal_id)
                          .where(Signal.strategy == name, SignalOutcome.status.in_(["TARGET", "STOP", "EXPIRED"]))
                          .group_by(SignalOutcome.traded)).all()
        out.append({"strategy": name, "description": STRATEGIES[name], **st, "reliability": assess.reliability(st),
                    "traded": dict((("traded" if k else "not_traded"), v) for k, v in rows)})
    return {"strategies": out, "live": state.get(db, "strategy")["name"],
            "note": "Shadow outcomes: stop assumed first when stop and target are hit in the same candle; r_net subtracts estimated costs."}


@router.get("/risk/status")
def risk_status(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    tr = state.get(db, "trading")
    mode = tr["mode"] if tr["mode"] in trading.ORDER_MODES else "PAPER"
    acct, _ = trading.account_state(db, mode)
    info = trading.check_loss_limits(db, mode) if mode in trading.ORDER_MODES else None
    db.rollback()
    risk = state.get(db, "risk")
    day0 = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    trades_today = db.scalar(select(func.count()).select_from(Order).where(Order.mode == mode, Order.action == "OPEN",
                                                                          Order.status == "FILLED", Order.created_at >= day0)) or 0
    eq = acct.equity if acct else 0
    budget = min(eq, risk.get("reference_balance") or eq) * risk["risk_per_trade_pct"] / 100 if eq else 0
    open_pos = trading.open_positions(db, mode)
    no_trade = []
    if tr["kill_switch"]:
        no_trade.append("kill switch engaged")
    if tr["locked"]:
        no_trade.append(f"loss-limit lock: {tr['lock_reason']}")
    if info and info["day_loss"] >= info["day_limit"] * 0.8:
        no_trade.append("daily loss close to the limit")
    if trades_today >= int(risk.get("max_trades_per_day", 3)):
        no_trade.append(f"{trades_today} trades today (max {risk.get('max_trades_per_day', 3)})")
    if len(open_pos) >= risk["max_concurrent_positions"]:
        no_trade.append("maximum open positions reached")
    health = quality.data_health(db)
    if not health["tradeable"]:
        no_trade.append("no market has trustworthy prices right now (closed, stale or wide spreads)")
    from ..services.livegate import is_approved
    return {"mode": tr["mode"], "account_env": mode, "equity": round(eq, 2), "currency": acct.currency if acct else "",
            "risk_per_trade": round(budget, 2), "risk_per_trade_pct": risk["risk_per_trade_pct"],
            "day_loss": round(info["day_loss"], 2) if info else 0, "day_limit": round(info["day_limit"], 2) if info else None,
            "week_loss": round(info["week_loss"], 2) if info else 0, "week_limit": round(info["week_limit"], 2) if info else None,
            "trades_today": trades_today, "max_trades_per_day": risk.get("max_trades_per_day", 3),
            "open_positions": len(open_pos), "max_positions": risk["max_concurrent_positions"],
            "kill_switch": tr["kill_switch"], "locked": tr["locked"], "live_gate_approved": is_approved(db),
            "no_trade_reasons": no_trade, "tradeable_markets": health["tradeable"]}


@router.get("/performance/insights")
def performance_insights(mode: str = "PAPER", days: int = Query(90, ge=7, le=3650), user: User = Depends(require("trades.view")),
                         db: Session = Depends(get_db)):
    """Monthly net P&L, drawdown, R distribution and the signal-to-execution funnel."""
    from datetime import timedelta
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(Position).where(Position.mode == mode, Position.status == "CLOSED", Position.closed_at >= since,
                                             Position.realized_pnl.is_not(None)).order_by(Position.closed_at)).all()
    monthly: dict = {}
    eq = peak = dd = 0.0
    for p in rows:
        k = p.closed_at.strftime("%Y-%m")
        m = monthly.setdefault(k, {"month": k, "trades": 0, "net": 0.0, "costs": 0.0, "wins": 0})
        m["trades"] += 1
        m["net"] = round(m["net"] + p.realized_pnl, 2)
        m["costs"] = round(m["costs"] + abs(p.commission or 0) + max(0, -(p.swap or 0)), 2)
        m["wins"] += 1 if p.realized_pnl > 0 else 0
        eq += p.realized_pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    sig_rows = db.execute(select(Signal.status, func.count()).where(Signal.created_at >= since, Signal.action != "NONE",
                                                                   Signal.timeframe != "MAN").group_by(Signal.status)).all()
    st = dict(sig_rows)
    detected = sum(st.values())
    outcomes = dict(db.execute(select(SignalOutcome.status, func.count()).join(Signal, Signal.id == SignalOutcome.signal_id)
                               .where(Signal.created_at >= since).group_by(SignalOutcome.status)).all())
    funnel = [
        {"stage": "Detected", "count": detected},
        {"stage": "Data OK", "count": detected - st.get("BLOCKED_DATA", 0)},
        {"stage": "Qualified (score + gates)", "count": sum(st.get(k, 0) for k in ("AWAITING", "ORDERED", "DECLINED", "EXPIRED"))},
        {"stage": "Approved / ordered", "count": st.get("ORDERED", 0)},
        {"stage": "Closed trades", "count": len(rows)},
    ]
    return {"mode": mode, "monthly": list(monthly.values()), "max_drawdown": round(dd, 2), "funnel": funnel, "signal_status": st,
            "shadow_outcomes": outcomes, "note": "Paper, broker demo and live are always reported separately."}
