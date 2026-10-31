"""Setup review & manual approval, SMC scanner and market overview."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import Order, Signal, SignalApproval, SmcEvent, User, utcnow
from ..services import market_data as md
from ..services import report, smc, trading
from ..services.indicators import add_indicators
from ..services.risk_engine import evaluate

router = APIRouter(prefix="/api", tags=["research"])


def _row(sig: Signal, a: SignalApproval | None) -> dict:
    return {"signal_id": sig.id, "symbol": sig.symbol, "timeframe": sig.timeframe, "side": sig.action, "strategy": sig.strategy,
            "candle_time": sig.candle_time, "entry_ref": sig.entry_ref, "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
            "signal_status": sig.status, "status": a.status if a else None, "expires_at": a.expires_at if a else None,
            "decided_by": a.decided_by if a else "", "decided_at": a.decided_at if a else None, "note": a.note if a else "",
            "order_client_id": a.order_client_id if a else None,
            "risk": (a.snapshot if a else None) or (sig.features or {}).get("risk")}


@router.get("/setups")
def list_setups(status: str | None = None, limit: int = Query(50, le=500), user: User = Depends(require("signals.view")),
                db: Session = Depends(get_db)):
    q = select(SignalApproval).order_by(SignalApproval.created_at.desc()).limit(limit)
    if status:
        q = q.where(SignalApproval.status.in_(status.split(",")))
    out = []
    for a in db.scalars(q).all():
        sig = db.get(Signal, a.signal_id)
        if sig:
            out.append(_row(sig, a))
    return {"setups": out, "controls": {k: state.get(db, "controls").get(k) for k in ("require_approval", "approval_minutes")}}


@router.get("/setups/{signal_id}/report")
def setup_report(signal_id: int, user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    sig = db.get(Signal, signal_id)
    if sig is None or sig.action not in ("BUY", "SELL"):
        raise HTTPException(404, "No trade setup for this signal")
    rep = report.setup_report(db, sig)
    a = db.get(SignalApproval, signal_id)
    rep["approval"] = _row(sig, a) if a else None
    return rep


class DecisionIn(BaseModel):
    note: str = Field(default="", max_length=1000)


@router.post("/setups/{signal_id}/approve")
def approve(signal_id: int, body: DecisionIn, request: Request, user: User = Depends(require("trading.manual")),
            db: Session = Depends(get_db)):
    a = db.get(SignalApproval, signal_id, with_for_update=True)
    sig = db.get(Signal, signal_id)
    if a is None or sig is None:
        raise HTTPException(404, "Setup not found")
    if a.status != "WAITING":
        raise HTTPException(409, f"This setup is already {a.status.lower()}")
    if a.expires_at < utcnow():
        a.status, a.decided_at = "EXPIRED", utcnow()
        sig.status = "EXPIRED"
        db.commit()
        raise HTTPException(409, "This setup expired; it is no longer valid")
    mode = state.get(db, "trading")["mode"]
    if mode not in trading.ORDER_MODES:
        raise HTTPException(400, "Set the trading mode to PRACTICE (paper) or BROKER DEMO first")
    if sig.trading_mode != mode:
        raise HTTPException(400, f"The setup was found in {sig.trading_mode} mode but the app is now in {mode}")
    # Re-check everything at today's price: the risk engine has the final word.
    dup = db.scalar(select(Order.id).where(Order.client_id == f"S{sig.id}-{mode}")) is not None
    ctx = trading.build_context(db, symbol=sig.symbol, side=sig.action, tf=sig.timeframe, sl=sig.stop_loss,
                                tp=sig.take_profit, mode=mode, duplicate=dup)
    price = ctx.ask if sig.action == "BUY" else ctx.bid
    if (sig.action == "BUY" and not sig.stop_loss < price < (sig.take_profit or 1e18)) or \
       (sig.action == "SELL" and not (sig.take_profit or -1) < price < sig.stop_loss):
        raise HTTPException(400, f"Price {price} is already outside the stop-loss / take-profit range — the setup is invalid")
    d = evaluate(ctx)
    if not d.approved:
        failed = "; ".join(f"{c['check']} ({c.get('detail', '')})" for c in d.checks if not c["passed"])
        db.rollback()
        raise HTTPException(400, f"Risk engine blocks this trade now: {failed}")
    o = trading.place_order(db, sig, d, mode)
    sig.status, sig.risk_checks = "ORDERED", d.checks
    a.status, a.decided_by, a.decided_at, a.note, a.order_client_id = "APPROVED", user.username, utcnow(), body.note, o.client_id
    a.snapshot = {k: v for k, v in d.to_dict().items() if k != "checks"}
    audit(db, "setup.approved", user=user, ip=client_ip(request), entity="signal", entity_id=sig.id,
          details={"symbol": sig.symbol, "side": sig.action, "volume": o.volume, "note": body.note, "order": o.client_id})
    db.commit()
    return {"status": "approved", "order": o.client_id, "order_status": o.status, "volume": o.volume}


@router.post("/setups/{signal_id}/decline")
def decline(signal_id: int, body: DecisionIn, request: Request, user: User = Depends(require("trading.manual")),
            db: Session = Depends(get_db)):
    a = db.get(SignalApproval, signal_id, with_for_update=True)
    sig = db.get(Signal, signal_id)
    if a is None or sig is None:
        raise HTTPException(404, "Setup not found")
    if a.status != "WAITING":
        raise HTTPException(409, f"This setup is already {a.status.lower()}")
    a.status, a.decided_by, a.decided_at, a.note = "DECLINED", user.username, utcnow(), body.note
    sig.status = "DECLINED"
    audit(db, "setup.declined", user=user, ip=client_ip(request), entity="signal", entity_id=sig.id,
          details={"symbol": sig.symbol, "side": sig.action, "note": body.note})
    db.commit()
    return {"status": "declined"}


# ----------------------------------------------------------------- SMC
@router.get("/smc/scan")
def smc_scan(symbol: str, timeframe: str = "H1", bars: int = Query(200, ge=50, le=1000),
             user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    if timeframe not in md.TF_SECONDS:
        raise HTTPException(400, "Unknown timeframe")
    cfg = state.get(db, "strategy")
    df = report.candles_tf(db, symbol.upper(), timeframe, limit=bars + 300)
    if len(df) < 60:
        raise HTTPException(400, f"Not enough {timeframe} candles stored for {symbol}")
    ind = add_indicators(df, cfg)
    an = smc.analyze(ind.tail(bars + 100), cfg)
    view = ind.tail(bars)
    t0 = view.index[0]
    events = [{**{k: v for k, v in e.items() if k != "i"}, "time": e["time"].isoformat(),
               **({"pivot_time": e["pivot_time"].isoformat()} if e.get("pivot_time") is not None else {}),
               **({"swing_time": e["swing_time"].isoformat()} if e.get("swing_time") is not None else {})}
              for e in an["events"] if e["time"] >= t0]
    candles = [{"time": int(t.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "ema_fast": None if r.ema_fast != r.ema_fast else r.ema_fast, "ema_slow": None if r.ema_slow != r.ema_slow else r.ema_slow}
               for t, r in view.iterrows()]
    return {"symbol": symbol.upper(), "timeframe": timeframe, "state": {**an["state"], "open_fvgs": [
        {**f, "time": f["time"].isoformat()} for f in an["state"]["open_fvgs"]]}, "events": events, "candles": candles,
        "definitions": smc.__doc__}


@router.get("/smc/events")
def smc_events(symbol: str | None = None, kind: str | None = None, days: int = Query(7, ge=1, le=365), limit: int = Query(300, le=5000),
               fmt: str = "json", user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    q = select(SmcEvent).where(SmcEvent.time >= utcnow() - timedelta(days=days)).order_by(SmcEvent.time.desc()).limit(limit)
    if symbol:
        q = q.where(SmcEvent.symbol == symbol.upper())
    if kind:
        q = q.where(SmcEvent.kind.in_(kind.split(",")))
    rows = [{"time": e.time.isoformat(), "symbol": e.symbol, "timeframe": e.timeframe, "kind": e.kind, "dir": e.dir,
             "level": e.level, "zone_low": e.zone_low, "zone_high": e.zone_high} for e in db.scalars(q).all()]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["time", "symbol", "timeframe", "kind", "dir", "level", "zone_low", "zone_high"])
        w.writeheader()
        w.writerows(rows)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=smc_events.csv"})
    return rows


@router.get("/market/overview")
def market_overview(user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    return report.overview(db)
