"""Live trading: order ticket (market / limit / stop, risk-based sizing, trade management),
pending orders, position detail & journal, SL/TP editing, trading controls and the live feed."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import (AuditLog, EventLog, NewsEvent, Order, PendingOrder, Position, PositionMgmt, Signal, SignalApproval,
                      TradeAttachment, TradeNote, User, utcnow)
from ..services import market_data as md
from ..services import trading
from ..services.indicators import add_indicators
from ..services.pro import submit_order
from ..services.risk_engine import evaluate

router = APIRouter(prefix="/api/live", tags=["live"])
pos_router = APIRouter(prefix="/api/positions", tags=["positions"])


class Management(BaseModel):
    breakeven_r: float | None = Field(default=None, ge=0.3, le=5)
    trailing_atr: float | None = Field(default=None, ge=0.5, le=10)


class TicketIn(BaseModel):
    symbol: str = Field(max_length=32)
    side: str
    order_type: str = "MARKET"  # MARKET | LIMIT | STOP
    trigger_price: float | None = Field(default=None, gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    sizing_mode: str = "auto"  # auto | risk_pct | risk_amount | lots
    sizing_value: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, gt=0, le=100)  # legacy: fixed lots
    management: Management = Management()
    expires_hours: float | None = Field(default=24, gt=0, le=24 * 30)
    note: str = Field(default="", max_length=200)


def _norm(body: TicketIn) -> TicketIn:
    body.symbol = body.symbol.strip().upper()
    body.side = body.side.upper()
    body.order_type = body.order_type.upper()
    if body.volume and body.sizing_mode == "auto":
        body.sizing_mode, body.sizing_value = "lots", body.volume
    if body.side not in ("BUY", "SELL"):
        raise HTTPException(400, "side must be BUY or SELL")
    if body.order_type not in ("MARKET", "LIMIT", "STOP"):
        raise HTTPException(400, "order type must be MARKET, LIMIT or STOP")
    if body.sizing_mode not in trading.SIZING_MODES:
        raise HTTPException(400, "unknown sizing mode")
    if body.sizing_mode != "auto" and not body.sizing_value:
        raise HTTPException(400, "enter the size value")
    return body


def _require_trading(db: Session):
    mode = state.get(db, "trading")["mode"]
    if mode not in trading.ORDER_MODES:
        raise HTTPException(400, "Set the trading mode to PRACTICE (paper), BROKER DEMO or BROKER LIVE to place orders")
    if not state.get(db, "controls").get("manual_trading", True):
        raise HTTPException(400, "Manual trading is switched off (Live trading → controls)")
    return mode


def _validate_trigger(db: Session, body: TicketIn):
    q = md.latest_quotes(db).get(body.symbol)
    if q is None:
        raise HTTPException(400, "No price for this market yet")
    if body.order_type == "MARKET":
        return q
    t = body.trigger_price
    if not t:
        raise HTTPException(400, "Enter the trigger price for a limit / stop order")
    ref = q.ask if body.side == "BUY" else q.bid
    rules = {("BUY", "LIMIT"): t < ref, ("BUY", "STOP"): t > ref, ("SELL", "LIMIT"): t > ref, ("SELL", "STOP"): t < ref}
    if not rules[(body.side, body.order_type)]:
        where = "below" if (body.side, body.order_type) in (("BUY", "LIMIT"), ("SELL", "STOP")) else "above"
        raise HTTPException(400, f"A {body.side} {body.order_type} must be {where} the current price {ref}")
    if (body.side == "BUY" and body.stop_loss >= t) or (body.side == "SELL" and body.stop_loss <= t):
        raise HTTPException(400, "The stop-loss must be on the losing side of the trigger price")
    return q


@router.post("/preview")
def preview(body: TicketIn, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    body = _norm(body)
    mode = state.get(db, "trading")["mode"]
    q = _validate_trigger(db, body)
    tf = (state.get(db, "strategy").get("trade_timeframes") or ["M15"])[0]
    ctx = trading.build_context(db, symbol=body.symbol, side=body.side, tf=tf, sl=body.stop_loss, tp=body.take_profit,
                                mode=mode if mode in trading.ORDER_MODES else "PAPER")
    if body.order_type != "MARKET":  # size as if filled at the trigger price
        spread = q.ask - q.bid
        ctx.bid, ctx.ask = (body.trigger_price - spread, body.trigger_price) if body.side == "BUY" else (body.trigger_price, body.trigger_price + spread)
    d = evaluate(ctx)
    trading.apply_sizing(d, ctx, body.sizing_mode, body.sizing_value)
    if mode not in trading.ORDER_MODES:
        d.checks.append({"check": "trading mode allows orders", "passed": False, "detail": mode})
        d.approved = False
    out = trading.potential_outcomes(d, ctx)
    db.rollback()
    return {"mode": mode, "approved": d.approved, "volume": d.volume, "entry_price": d.entry_price, "risk_amount": d.risk_amount,
            "risk_budget": d.risk_budget, "margin_required": d.margin_required, "checks": d.checks, "bid": q.bid, "ask": q.ask,
            "max_volume": None if not d.loss_per_lot else trading.floor_to_step(d.risk_budget / d.loss_per_lot, ctx.spec.volume_step),
            "volume_min": ctx.spec.volume_min, "equity": ctx.account.equity, **out}


@router.post("/order")
def place(body: TicketIn, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    body = _norm(body)
    mode = _require_trading(db)
    _validate_trigger(db, body)
    mgmt = body.management.model_dump()
    if body.order_type == "MARKET":
        o, checks, out = submit_order(db, symbol=body.symbol, side=body.side, sl=body.stop_loss, tp=body.take_profit,
                                      sizing_mode=body.sizing_mode, sizing_value=body.sizing_value, management=mgmt,
                                      note=body.note, by=user.username, source="manual")
        if o is None:
            failed = [f"{c['check']} ({c['detail']})" if c.get("detail") else c["check"] for c in checks if not c["passed"]]
            raise HTTPException(400, "Rejected by risk engine: " + "; ".join(failed))
        audit(db, "order.manual", user=user, ip=client_ip(request), entity="order", entity_id=o.client_id,
              details={"symbol": body.symbol, "side": body.side, "volume": o.volume, "sl": body.stop_loss, "tp": body.take_profit,
                       "mode": mode, "sizing": body.sizing_mode, "management": mgmt})
        db.commit()
        return {"type": "MARKET", "client_id": o.client_id, "status": o.status, "volume": o.volume, "risk_amount": o.risk_amount, "mode": mode, **out}
    p = PendingOrder(created_by=user.username, mode=mode, symbol=body.symbol, side=body.side, order_type=body.order_type,
                     trigger_price=body.trigger_price, stop_loss=body.stop_loss, take_profit=body.take_profit,
                     sizing_mode=body.sizing_mode, sizing_value=body.sizing_value, management=mgmt, note=body.note,
                     expires_at=utcnow() + timedelta(hours=body.expires_hours or 24))
    db.add(p)
    db.flush()
    audit(db, "pending.created", user=user, ip=client_ip(request), entity="pending", entity_id=p.id,
          details={"symbol": p.symbol, "side": p.side, "type": p.order_type, "trigger": p.trigger_price, "sl": p.stop_loss,
                   "tp": p.take_profit, "mode": mode})
    db.commit()
    return {"type": p.order_type, "pending_id": p.id, "status": p.status, "expires_at": p.expires_at, "mode": mode}


PENDING_COLS = ("id", "created_at", "created_by", "mode", "symbol", "side", "order_type", "trigger_price", "stop_loss",
                "take_profit", "sizing_mode", "sizing_value", "management", "note", "status", "expires_at", "order_client_id", "error")


@router.get("/pending")
def pending(status: str | None = "WORKING", user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    q = select(PendingOrder).order_by(PendingOrder.id.desc()).limit(200)
    if status:
        q = q.where(PendingOrder.status == status)
    return [{c: getattr(p, c) for c in PENDING_COLS} for p in db.scalars(q).all()]


@router.post("/pending/{pid}/cancel")
def cancel_pending(pid: int, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    p = db.get(PendingOrder, pid)
    if p is None or p.status != "WORKING":
        raise HTTPException(404, "Working order not found")
    p.status, p.error = "CANCELLED", f"cancelled by {user.username}"
    audit(db, "pending.cancelled", user=user, ip=client_ip(request), entity="pending", entity_id=pid)
    db.commit()
    return {"status": "ok"}


@router.get("/suggest")
def suggest(symbol: str, side: str, entry: float | None = None, user: User = Depends(require("trading.manual")),
            db: Session = Depends(get_db)):
    """Suggested stop-loss / take-profit from the strategy's ATR settings."""
    cfg = state.get(db, "strategy")
    tf = (cfg.get("trade_timeframes") or ["H1"])[0]
    df = md.load_candles(db, symbol.upper(), tf, limit=400)
    q = md.latest_quotes(db).get(symbol.upper())
    if df.empty or q is None:
        raise HTTPException(400, "No market data for this symbol yet")
    atr = float(add_indicators(df, cfg)["atr"].iloc[-1])
    entry = entry or (q.ask if side == "BUY" else q.bid)
    dist = cfg["atr_stop_mult"] * atr
    digits = 3 if symbol.upper().endswith("JPY") else 5
    sl = entry - dist if side == "BUY" else entry + dist
    tp = entry + cfg["reward_risk"] * dist if side == "BUY" else entry - cfg["reward_risk"] * dist
    return {"entry": entry, "stop_loss": round(sl, digits), "take_profit": round(tp, digits), "atr": atr, "timeframe": tf,
            "bid": q.bid, "ask": q.ask}


# ---------------------------------------------------------------- controls
class ControlsIn(BaseModel):
    require_approval: bool | None = None
    approval_minutes: int | None = Field(default=None, ge=5, le=24 * 60)
    auto_trading: bool | None = None
    manual_trading: bool | None = None
    disabled_symbols: list[str] | None = None
    session_filter: bool | None = None
    session_start_utc: int | None = Field(default=None, ge=0, le=23)
    session_end_utc: int | None = Field(default=None, ge=0, le=24)
    news_filter: bool | None = None
    news_before_min: int | None = Field(default=None, ge=0, le=240)
    news_after_min: int | None = Field(default=None, ge=0, le=240)
    news_impacts: list[str] | None = None
    default_breakeven_r: float | None = Field(default=None, ge=0, le=5)
    default_trailing_atr: float | None = Field(default=None, ge=0, le=10)


@router.get("/controls")
def get_controls(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    return state.get(db, "controls")


@router.put("/controls")
def put_controls(body: ControlsIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    c = state.get_locked(db, "controls")
    changes = {}
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "news_impacts":
            v = [x for x in v if x in ("High", "Medium", "Low")] or ["High"]
        if k == "disabled_symbols":
            v = sorted({x.strip().upper() for x in v if x.strip()})
        if k in ("default_breakeven_r", "default_trailing_atr") and not v:
            v = None
        if c.get(k) != v:
            changes[k] = {"from": c.get(k), "to": v}
            c[k] = v
    if changes:
        state.put(db, "controls", c, by=user.username)
        audit(db, "controls.updated", user=user, ip=client_ip(request), details=changes)
        db.commit()
    return c


# ----------------------------------------------------------------- feed
@router.get("/feed")
def feed(user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    """Everything the live screen needs in one call (polled every few seconds)."""
    tr = state.get(db, "trading")
    mode = tr["mode"] if tr["mode"] in trading.ORDER_MODES else "PAPER"
    acct, meta = trading.account_state(db, mode)
    positions = trading.open_positions(db, mode)
    orders = db.scalars(select(Order).order_by(Order.id.desc()).limit(15)).all()
    events = db.scalars(select(EventLog).order_by(EventLog.id.desc()).limit(15)).all()
    acts = db.scalars(select(AuditLog).where(AuditLog.action.in_(
        ["order.created", "order.filled", "order.rejected", "position.closed", "order.manual", "trading.kill_switch",
         "trading.mode_changed", "risk.locked", "pending.created", "pending.triggered", "pending.cancelled",
         "position.sl_tp_modified", "controls.updated"])).order_by(AuditLog.id.desc()).limit(30)).all()
    ext = state.get(db, "external_positions")
    now = utcnow()
    news = db.scalars(select(NewsEvent).where(NewsEvent.time >= now - timedelta(minutes=30), NewsEvent.impact.in_(["High", "Medium"]))
                      .order_by(NewsEvent.time).limit(8)).all()
    mgmt = {m.position_id: m for m in db.scalars(select(PositionMgmt).where(PositionMgmt.position_id.in_([p.id for p in positions] or [0]))).all()}
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    closed_today = db.scalars(select(Position).where(Position.mode == mode, Position.status == "CLOSED",
                                                     Position.closed_at >= day_start)).all()
    return {
        "server_time": now, "mode": tr["mode"], "account_mode": mode, "kill_switch": tr["kill_switch"], "locked": tr["locked"],
        "lock_reason": tr["lock_reason"],
        "account": acct.__dict__ if acct else None, "account_meta": meta,
        "unrealized": round(sum(p.unrealized_pnl or 0 for p in positions), 2),
        "realized_today": round(sum(p.realized_pnl or 0 for p in closed_today), 2), "trades_today": len(closed_today),
        "positions": [{**{c: getattr(p, c) for c in ("id", "mode", "symbol", "side", "volume", "open_price", "current_price",
                                                       "stop_loss", "take_profit", "unrealized_pnl", "opened_at", "broker_ticket")},
                       "management": ({"breakeven_r": mgmt[p.id].breakeven_r, "trailing_atr": mgmt[p.id].trailing_atr,
                                       "breakeven_done": mgmt[p.id].breakeven_done} if p.id in mgmt else None)} for p in positions],
        "pending": [{c: getattr(p, c) for c in PENDING_COLS} for p in
                    db.scalars(select(PendingOrder).where(PendingOrder.status == "WORKING").order_by(PendingOrder.id.desc())).all()],
        "orders": [{c: getattr(o, c) for c in ("client_id", "mode", "action", "symbol", "side", "volume", "status", "fill_price",
                                               "error", "created_at", "sl_verified", "stop_loss")} for o in orders],
        "activity": [{"at": a.at, "user": a.username, "action": a.action, "details": a.details} for a in acts],
        "events": [{"at": e.at, "level": e.level, "source": e.source, "message": e.message} for e in events],
        "external_positions": ext.get("positions", []), "external_at": ext.get("at"),
        "broker_active": state.get(db, "brokers")["active"],
        "controls": state.get(db, "controls"),
        "news": [{"time": n.time, "currency": n.currency, "impact": n.impact, "title": n.title, "forecast": n.forecast,
                  "previous": n.previous} for n in news],
        "news_status": state.get(db, "news_status"),
    }


# ------------------------------------------------------------ positions
class ModifyIn(BaseModel):
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, ge=0)  # 0 = remove TP


@pos_router.post("/{pid}/modify")
def modify(pid: int, body: ModifyIn, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    p = db.get(Position, pid)
    if p is None or p.status != "OPEN":
        raise HTTPException(404, "Open position not found")
    try:
        o = trading.request_modify(db, p, body.stop_loss, body.take_profit, user.username, "manual edit")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"status": "applied" if o is None else "sent_to_broker", "order": o.client_id if o else None}


class MgmtIn(BaseModel):
    breakeven_r: float | None = Field(default=None, ge=0, le=5)
    trailing_atr: float | None = Field(default=None, ge=0, le=10)


@pos_router.put("/{pid}/management")
def set_mgmt(pid: int, body: MgmtIn, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    p = db.get(Position, pid)
    if p is None or p.status != "OPEN" or not p.stop_loss:
        raise HTTPException(404, "Open position with a stop-loss not found")
    m = db.get(PositionMgmt, pid)
    if m is None:
        m = PositionMgmt(position_id=pid, initial_sl=p.stop_loss)
        db.add(m)
    m.breakeven_r, m.trailing_atr = body.breakeven_r or None, body.trailing_atr or None
    audit(db, "position.management_set", user=user, ip=client_ip(request), entity="position", entity_id=pid, details=body.model_dump())
    db.commit()
    return {"status": "ok"}


@pos_router.get("/export.csv")
def export_csv(mode: str | None = None, user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    q = select(Position).where(Position.status == "CLOSED").order_by(Position.closed_at)
    if mode:
        q = q.where(Position.mode == mode)
    notes = {n.position_id: n for n in db.scalars(select(TradeNote)).all()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "mode", "symbol", "side", "lots", "opened_utc", "open_price", "stop_loss", "take_profit", "closed_utc",
                "close_price", "close_reason", "commission", "swap", "net_pnl", "duration_min", "journal_note", "tags"])
    for p in db.scalars(q).all():
        n = notes.get(p.id)
        dur = round((p.closed_at - p.opened_at).total_seconds() / 60) if p.closed_at and p.opened_at else ""
        w.writerow([p.id, p.mode, p.symbol, p.side, p.volume, p.opened_at.isoformat() if p.opened_at else "", p.open_price,
                    p.stop_loss, p.take_profit, p.closed_at.isoformat() if p.closed_at else "", p.close_price, p.close_reason,
                    p.commission, p.swap, p.realized_pnl, dur, n.note if n else "", ";".join(n.tags) if n else ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=trades.csv"})


@pos_router.get("/{pid}/detail")
def detail(pid: int, user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    p = db.get(Position, pid)
    if p is None:
        raise HTTPException(404, "Not found")
    o = db.get(Order, p.order_id) if p.order_id else None
    sig = db.get(Signal, o.signal_id) if o and o.signal_id else None
    orders = db.scalars(select(Order).where(or_(Order.position_id == p.id, Order.id == p.order_id)).order_by(Order.id)).all()
    ids = [str(p.id)] + [x.client_id for x in orders]
    timeline = db.scalars(select(AuditLog).where(AuditLog.entity_id.in_(ids)).order_by(AuditLog.id)).all()
    start = p.opened_at - timedelta(hours=6)
    end = (p.closed_at or utcnow()) + timedelta(hours=2)
    df = md.load_candles(db, p.symbol, "M15", start=start, end=end)
    m = db.get(PositionMgmt, p.id)
    n = db.get(TradeNote, p.id)
    risk = abs(p.open_price - (m.initial_sl if m else (sig.stop_loss if sig and sig.stop_loss else p.stop_loss or p.open_price)))
    last = p.close_price or p.current_price or p.open_price
    r_mult = ((last - p.open_price) if p.side == "BUY" else (p.open_price - last)) / risk if risk else None
    return {
        "position": {c: getattr(p, c) for c in ("id", "mode", "symbol", "side", "volume", "open_price", "stop_loss", "take_profit",
                                                 "opened_at", "status", "closed_at", "close_price", "close_reason", "commission",
                                                 "swap", "realized_pnl", "unrealized_pnl", "current_price", "broker_ticket")},
        "r_multiple": round(r_mult, 2) if r_mult is not None else None,
        "duration_min": round(((p.closed_at or utcnow()) - p.opened_at).total_seconds() / 60),
        "signal": {"id": sig.id, "strategy": sig.strategy, "timeframe": sig.timeframe, "reasons": sig.reasons,
                   "features": {k: v for k, v in (sig.features or {}).items() if k != "risk"}, "risk_checks": sig.risk_checks} if sig else None,
        "orders": [{c: getattr(x, c) for c in ("client_id", "action", "status", "volume", "fill_price", "stop_loss", "take_profit",
                                               "sl_verified", "error", "created_at")} for x in orders],
        "timeline": [{"at": a.at, "user": a.username, "action": a.action, "details": a.details} for a in timeline],
        "management": {"initial_sl": m.initial_sl, "breakeven_r": m.breakeven_r, "trailing_atr": m.trailing_atr,
                       "breakeven_done": m.breakeven_done} if m else None,
        "note": {"note": n.note, "tags": n.tags, "rating": n.rating} if n else None,
        "approval": ({"status": ap.status, "by": ap.decided_by, "at": ap.decided_at, "note": ap.note}
                     if sig and (ap := db.get(SignalApproval, sig.id)) else None),
        "attachments": [{"id": x.id, "filename": x.filename, "size": x.size, "created_at": x.created_at}
                        for x in db.scalars(select(TradeAttachment).where(TradeAttachment.position_id == p.id).order_by(TradeAttachment.id)).all()],
        "candles": [{"time": int(t.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close} for t, r in df.iterrows()],
    }


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default=[], max_length=10)
    rating: int | None = Field(default=None, ge=1, le=5)


@pos_router.put("/{pid}/note")
def put_note(pid: int, body: NoteIn, user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    if db.get(Position, pid) is None:
        raise HTTPException(404, "Not found")
    n = db.get(TradeNote, pid) or TradeNote(position_id=pid)
    n.note, n.tags, n.rating, n.updated_by = body.note, [t.strip()[:30] for t in body.tags if t.strip()], body.rating, user.username
    db.add(n)
    db.commit()
    return {"status": "ok"}


IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_ATTACHMENT = 3 * 1024 * 1024


@pos_router.post("/{pid}/attachments")
async def add_attachment(pid: int, request: Request, file: UploadFile = File(...), user: User = Depends(require("trades.view")),
                         db: Session = Depends(get_db)):
    if db.get(Position, pid) is None:
        raise HTTPException(404, "Not found")
    if file.content_type not in IMAGE_TYPES:
        raise HTTPException(400, "Only PNG, JPEG, WEBP or GIF screenshots")
    data = await file.read(MAX_ATTACHMENT + 1)
    if len(data) > MAX_ATTACHMENT:
        raise HTTPException(400, "Max 3 MB per screenshot")
    sig = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif", b"RIFF": "image/webp"}
    if not any(data.startswith(k) for k in sig):
        raise HTTPException(400, "File content is not an image")
    if db.scalar(select(TradeAttachment.id).where(TradeAttachment.position_id == pid).offset(9).limit(1)):
        raise HTTPException(400, "Max 10 screenshots per trade")
    a = TradeAttachment(position_id=pid, created_by=user.username, filename=(file.filename or "screenshot")[:200],
                        content_type=file.content_type, size=len(data), data=data)
    db.add(a)
    audit(db, "journal.attachment_added", user=user, ip=client_ip(request), entity="position", entity_id=pid,
          details={"filename": a.filename, "size": a.size})
    db.commit()
    return {"id": a.id}


@pos_router.get("/attachments/{aid}")
def get_attachment(aid: int, user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    a = db.get(TradeAttachment, aid)
    if a is None or a.content_type not in IMAGE_TYPES:
        raise HTTPException(404, "Not found")
    return Response(a.data, media_type=a.content_type, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
                                                                "Content-Disposition": "inline", "Content-Security-Policy": "default-src 'none'"})


@pos_router.delete("/attachments/{aid}")
def del_attachment(aid: int, request: Request, user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    a = db.get(TradeAttachment, aid)
    if a is None:
        raise HTTPException(404, "Not found")
    audit(db, "journal.attachment_deleted", user=user, ip=client_ip(request), entity="position", entity_id=a.position_id,
          details={"filename": a.filename})
    db.delete(a)
    db.commit()
    return {"status": "ok"}
