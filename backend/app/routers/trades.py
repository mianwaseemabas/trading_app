from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import AccountSnapshot, EventLog, Order, Position, Signal, User, utcnow
from ..services import trading
from ..services.backtest import metrics

router = APIRouter(prefix="/api", tags=["trading"])


def _row(o, cols):
    return {c: getattr(o, c) for c in cols}


SIGNAL_COLS = ("id", "created_at", "symbol", "timeframe", "candle_time", "strategy", "strategy_version", "action",
               "entry_ref", "stop_loss", "take_profit", "status", "trading_mode")
ORDER_COLS = ("id", "client_id", "signal_id", "mode", "action", "symbol", "side", "volume", "requested_price",
              "stop_loss", "take_profit", "status", "broker_ticket", "fill_price", "filled_volume", "slippage_points",
              "sl_verified", "error", "risk_amount", "created_at", "updated_at", "expires_at")
POS_COLS = ("id", "mode", "order_id", "broker_ticket", "symbol", "side", "volume", "open_price", "stop_loss",
            "take_profit", "opened_at", "status", "closed_at", "close_price", "close_reason", "commission", "swap",
            "realized_pnl", "unrealized_pnl", "current_price")


@router.get("/signals")
def signals(
    symbol: str | None = None,
    status: str | None = None,
    actionable: bool = False,
    limit: int = Query(200, le=1000),
    user: User = Depends(require("signals.view")),
    db: Session = Depends(get_db),
):
    q = select(Signal).order_by(Signal.id.desc()).limit(limit)
    if symbol:
        q = q.where(Signal.symbol == symbol)
    if status:
        q = q.where(Signal.status == status)
    if actionable:
        q = q.where(Signal.action != "NONE")
    return [_row(s, SIGNAL_COLS) for s in db.scalars(q).all()]


@router.get("/signals/{signal_id}")
def signal_detail(signal_id: int, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    s = db.get(Signal, signal_id)
    if s is None:
        raise HTTPException(404, "Not found")
    orders = db.scalars(select(Order).where(Order.signal_id == s.id)).all()
    return {**_row(s, SIGNAL_COLS), "features": s.features, "reasons": s.reasons, "risk_checks": s.risk_checks,
            "orders": [_row(o, ORDER_COLS) for o in orders]}


@router.get("/orders")
def orders(mode: str | None = None, limit: int = Query(200, le=1000),
           user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    q = select(Order).order_by(Order.id.desc()).limit(limit)
    if mode:
        q = q.where(Order.mode == mode)
    return [_row(o, ORDER_COLS) for o in db.scalars(q).all()]


@router.get("/positions")
def positions(status: str = "OPEN", mode: str | None = None, limit: int = Query(500, le=2000),
              user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    q = select(Position).where(Position.status == status).order_by(Position.id.desc()).limit(limit)
    if mode:
        q = q.where(Position.mode == mode)
    return [_row(p, POS_COLS) for p in db.scalars(q).all()]


@router.post("/positions/{position_id}/close")
def close_position(position_id: int, request: Request, user: User = Depends(require("killswitch.use")),
                   db: Session = Depends(get_db)):
    p = db.get(Position, position_id)
    if p is None or p.status != "OPEN":
        raise HTTPException(404, "Open position not found")
    try:
        o = trading.request_close(db, p, "manual", user.username)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, "position.close_manual", user=user, ip=client_ip(request), entity="position", entity_id=p.id)
    db.commit()
    return {"status": "closed" if o is None else "close_order_queued", "order": o.client_id if o else None}


@router.get("/performance")
def performance(mode: str = "PAPER", days: int = Query(30, ge=1, le=3650),
                user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(Position).where(Position.mode == mode, Position.status == "CLOSED",
                                             Position.closed_at >= since, Position.realized_pnl.is_not(None))
                      .order_by(Position.closed_at)).all()
    trades = [{"net_pnl": p.realized_pnl, "side": p.side, "commission": abs(p.commission or 0),
               "swap": p.swap or 0, "spread_cost": 0, "r_multiple": None} for p in rows]
    start_equity = 500.0
    if mode in trading.MT5_MODES:
        first = db.scalar(select(AccountSnapshot).where(AccountSnapshot.source == "mt5", AccountSnapshot.at >= since)
                          .order_by(AccountSnapshot.at).limit(1))
        if first:
            start_equity = first.balance
    else:
        start_equity = (trading.account_state(db, "PAPER")[0].balance - sum(t["net_pnl"] for t in trades)) or 500
    equity, curve = start_equity, []
    for p in rows:
        equity += p.realized_pnl
        curve.append({"time": p.closed_at.isoformat(), "equity": round(equity, 2)})
    groups: dict = {"symbol": {}, "side": {}, "session": {}, "strategy": {}, "weekday": {}, "hour": {}, "source": {}}
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    orders = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_([p.order_id for p in rows if p.order_id] or [0]))).all()}
    sigs = {s.id: s for s in db.scalars(select(Signal).where(Signal.id.in_([o.signal_id for o in orders.values() if o.signal_id] or [0]))).all()}
    for p in rows:
        t = {"net_pnl": p.realized_pnl, "side": p.side, "commission": 0, "swap": 0, "spread_cost": 0, "r_multiple": None}
        o = orders.get(p.order_id)
        strat = sigs[o.signal_id].strategy if o and o.signal_id in sigs else "broker"
        src = {"manual": "manual", "pending": "manual (limit/stop)"}.get(strat, "strategy" if strat != "broker" else "broker")
        from ..services.report import session_of
        for g, k in (("symbol", p.symbol), ("side", p.side), ("session", session_of(p.opened_at)), ("strategy", strat),
                     ("weekday", days[p.opened_at.weekday()]),
                     ("hour", f"{p.opened_at.hour:02d}:00 UTC"), ("source", src)):
            groups[g].setdefault(k, []).append(t)
    return {"mode": mode, "metrics": metrics(trades, start_equity), "equity_curve": curve,
            "by_symbol": {s: metrics(t, start_equity) for s, t in groups["symbol"].items()},
            "breakdown": {g: {k: metrics(v, start_equity) for k, v in sorted(d.items())} for g, d in groups.items()},
            "note": "Realized results only, net of commission and swap. Unrealized P&L is never counted as profit."}


@router.get("/account/history")
def account_history(hours: int = Query(72, le=24 * 90), user: User = Depends(require("dashboard.view")),
                    db: Session = Depends(get_db)):
    rows = db.scalars(select(AccountSnapshot).where(AccountSnapshot.at >= utcnow() - timedelta(hours=hours))
                      .order_by(AccountSnapshot.at)).all()
    step = max(1, len(rows) // 500)
    return [{"time": r.at.isoformat(), "balance": r.balance, "equity": r.equity} for r in rows[::step]]


@router.get("/events")
def events(level: str | None = None, limit: int = Query(200, le=1000),
           user: User = Depends(require("audit.view")), db: Session = Depends(get_db)):
    q = select(EventLog).order_by(EventLog.id.desc()).limit(limit)
    if level:
        q = q.where(EventLog.level == level)
    return [_row(e, ("id", "at", "level", "source", "message", "details")) for e in db.scalars(q).all()]
