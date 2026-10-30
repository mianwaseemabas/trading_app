"""Live trading: manual order ticket (always through the risk engine) and a live activity feed."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit, event
from ..db import get_db
from ..deps import client_ip, require
from ..models import AuditLog, EventLog, Order, Signal, User, utcnow
from ..services import market_data as md
from ..services import trading
from ..services.indicators import add_indicators
from ..services.risk_engine import evaluate, floor_to_step

router = APIRouter(prefix="/api/live", tags=["live"])


class TicketIn(BaseModel):
    symbol: str = Field(max_length=32)
    side: str
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, gt=0, le=100)  # lots; empty = size from risk budget
    note: str = Field(default="", max_length=200)


def _check(db: Session, body: TicketIn) -> tuple[str, object, object]:
    if body.side not in ("BUY", "SELL"):
        raise HTTPException(400, "side must be BUY or SELL")
    mode = state.get(db, "trading")["mode"]
    if mode not in trading.ORDER_MODES:
        raise HTTPException(400, "Set the trading mode to PAPER, BROKER DEMO or BROKER LIVE to place orders")
    symbol = body.symbol.strip().upper()
    tf = (state.get(db, "strategy").get("trade_timeframes") or ["M15"])[0]
    ctx = trading.build_context(db, symbol=symbol, side=body.side, tf=tf, sl=body.stop_loss, tp=body.take_profit, mode=mode)
    decision = evaluate(ctx)
    if body.volume is not None and decision.loss_per_lot > 0:
        # A manual size is allowed only up to what the risk budget permits.
        vol = floor_to_step(body.volume, ctx.spec.volume_step)
        max_vol = decision.volume
        ok = vol <= max_vol + 1e-12 and vol >= ctx.spec.volume_min
        decision.checks.append({"check": "requested size within risk budget", "passed": ok,
                                "detail": f"requested {vol} lots, max {max_vol} lots"})
        if ok:
            decision.volume = vol
            decision.risk_amount = round(vol * decision.loss_per_lot, 2)
        decision.approved = decision.approved and ok
    return mode, ctx, decision


@router.post("/preview")
def preview(body: TicketIn, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    mode, ctx, d = _check(db, body)
    db.rollback()
    return {"mode": mode, "approved": d.approved, "volume": d.volume, "entry_price": d.entry_price,
            "risk_amount": d.risk_amount, "risk_budget": d.risk_budget, "margin_required": d.margin_required,
            "checks": d.checks, "bid": ctx.bid, "ask": ctx.ask}


@router.post("/order")
def place(body: TicketIn, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    mode, ctx, d = _check(db, body)
    if not d.approved:
        failed = [f"{c['check']} ({c['detail']})" if c.get("detail") else c["check"] for c in d.checks if not c["passed"]]
        raise HTTPException(400, "Rejected by risk engine: " + "; ".join(failed))
    now = utcnow()
    sig = Signal(symbol=ctx.symbol, timeframe="MAN", candle_time=now, strategy="manual", strategy_version=f"manual-{user.id}",
                 action=body.side, entry_ref=d.entry_price, stop_loss=body.stop_loss, take_profit=body.take_profit,
                 features={"note": body.note, "placed_by": user.username, "risk": {k: v for k, v in d.to_dict().items() if k != "checks"}},
                 reasons=[{"check": "manual order", "passed": True, "detail": body.note or user.username}],
                 status="ORDERED", risk_checks=d.checks, trading_mode=mode)
    db.add(sig)
    db.flush()
    o = trading.place_order(db, sig, d, mode)
    audit(db, "order.manual", user=user, ip=client_ip(request), entity="order", entity_id=o.client_id,
          details={"symbol": ctx.symbol, "side": body.side, "volume": o.volume, "sl": body.stop_loss, "tp": body.take_profit, "mode": mode})
    db.commit()
    return {"client_id": o.client_id, "status": o.status, "volume": o.volume, "risk_amount": o.risk_amount, "mode": mode}


@router.get("/suggest")
def suggest(symbol: str, side: str, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    """Suggested stop-loss / take-profit from the strategy's ATR settings."""
    cfg = state.get(db, "strategy")
    tf = (cfg.get("trade_timeframes") or ["H1"])[0]
    df = md.load_candles(db, symbol.upper(), tf, limit=400)
    q = md.latest_quotes(db).get(symbol.upper())
    if df.empty or q is None:
        raise HTTPException(400, "No market data for this symbol yet")
    atr = float(add_indicators(df, cfg)["atr"].iloc[-1])
    entry = q.ask if side == "BUY" else q.bid
    dist = cfg["atr_stop_mult"] * atr
    digits = 3 if symbol.upper().endswith("JPY") else 5
    sl = entry - dist if side == "BUY" else entry + dist
    tp = entry + cfg["reward_risk"] * dist if side == "BUY" else entry - cfg["reward_risk"] * dist
    return {"entry": entry, "stop_loss": round(sl, digits), "take_profit": round(tp, digits), "atr": atr, "timeframe": tf}


@router.get("/feed")
def feed(user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    """Everything the live screen needs in one call (polled every few seconds)."""
    tr = state.get(db, "trading")
    mode = tr["mode"] if tr["mode"] in trading.ORDER_MODES else "PAPER"
    acct, meta = trading.account_state(db, mode)
    positions = trading.open_positions(db, mode)
    orders = db.scalars(select(Order).order_by(Order.id.desc()).limit(15)).all()
    events = db.scalars(select(EventLog).order_by(EventLog.id.desc()).limit(15)).all()
    trades = db.scalars(select(AuditLog).where(AuditLog.action.in_(
        ["order.created", "order.filled", "order.rejected", "position.closed", "order.manual", "trading.kill_switch",
         "trading.mode_changed", "risk.locked"])).order_by(AuditLog.id.desc()).limit(25)).all()
    ext = state.get(db, "external_positions")
    return {
        "server_time": utcnow(), "mode": tr["mode"], "account_mode": mode, "kill_switch": tr["kill_switch"], "locked": tr["locked"],
        "account": acct.__dict__ if acct else None, "account_meta": meta,
        "unrealized": round(sum(p.unrealized_pnl or 0 for p in positions), 2),
        "positions": [{c: getattr(p, c) for c in ("id", "mode", "symbol", "side", "volume", "open_price", "current_price",
                                                  "stop_loss", "take_profit", "unrealized_pnl", "opened_at", "broker_ticket")}
                      for p in positions],
        "orders": [{c: getattr(o, c) for c in ("client_id", "mode", "action", "symbol", "side", "volume", "status", "fill_price",
                                               "error", "created_at", "sl_verified")} for o in orders],
        "activity": [{"at": a.at, "user": a.username, "action": a.action, "details": a.details} for a in trades],
        "events": [{"at": e.at, "level": e.level, "source": e.source, "message": e.message} for e in events],
        "external_positions": ext.get("positions", []), "external_at": ext.get("at"),
        "broker_active": state.get(db, "brokers")["active"],
    }
