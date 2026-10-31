"""Engine jobs for pro trading features: pending (limit/stop) orders and trade management."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit, event
from ..models import Order, PendingOrder, Position, PositionMgmt, Quote, Signal, utcnow
from . import market_data as md
from . import trading
from .indicators import add_indicators
from .notifier import notify
from .risk_engine import evaluate

log = logging.getLogger("pro")


def triggered(p: PendingOrder, q: Quote) -> bool:
    if p.side == "BUY":
        return q.ask <= p.trigger_price if p.order_type == "LIMIT" else q.ask >= p.trigger_price
    return q.bid >= p.trigger_price if p.order_type == "LIMIT" else q.bid <= p.trigger_price


def submit_order(db: Session, *, symbol: str, side: str, sl: float, tp: float | None, sizing_mode: str,
                 sizing_value: float | None, management: dict | None, note: str, by: str, source: str) -> tuple[Order | None, list, dict]:
    """Risk-check and place a discretionary order. Returns (order or None, checks, outcomes)."""
    mode = state.get(db, "trading")["mode"]
    tf = (state.get(db, "strategy").get("trade_timeframes") or ["M15"])[0]
    ctx = trading.build_context(db, symbol=symbol, side=side, tf=tf, sl=sl, tp=tp, mode=mode)
    d = evaluate(ctx)
    trading.apply_sizing(d, ctx, sizing_mode, sizing_value)
    if not d.approved:
        return None, d.checks, {}
    sig = Signal(symbol=symbol, timeframe="MAN", candle_time=utcnow(), strategy=source, strategy_version=f"{source}-{by}",
                 action=side, entry_ref=d.entry_price, stop_loss=sl, take_profit=tp,
                 features={"note": note, "placed_by": by, "management": management or {},
                           "sizing": {"mode": sizing_mode, "value": sizing_value},
                           "risk": {k: v for k, v in d.to_dict().items() if k != "checks"}},
                 reasons=[{"check": f"{source} order", "passed": True, "detail": note or by}],
                 status="ORDERED", risk_checks=d.checks, trading_mode=mode)
    db.add(sig)
    db.flush()
    o = trading.place_order(db, sig, d, mode)
    return o, d.checks, trading.potential_outcomes(d, ctx)


def process_pending(db: Session) -> None:
    now = utcnow()
    mode = state.get(db, "trading")["mode"]
    quotes = md.latest_quotes(db)
    for p in db.scalars(select(PendingOrder).where(PendingOrder.status == "WORKING")).all():
        if p.mode != mode:
            p.status, p.error = "CANCELLED", f"trading mode changed to {mode}"
            continue
        if p.expires_at and p.expires_at < now:
            p.status, p.error = "EXPIRED", "expired before the price reached the trigger"
            continue
        q = quotes.get(p.symbol)
        if q is None or (now - q.collected_at).total_seconds() > 120 or not triggered(p, q):
            continue
        o, checks, _ = submit_order(db, symbol=p.symbol, side=p.side, sl=p.stop_loss, tp=p.take_profit,
                                    sizing_mode=p.sizing_mode, sizing_value=p.sizing_value, management=p.management,
                                    note=f"{p.order_type} @ {p.trigger_price} {p.note}".strip(), by=p.created_by, source="pending")
        if o is None:
            failed = [c["check"] + (f" ({c['detail']})" if c.get("detail") else "") for c in checks if not c["passed"]]
            p.status, p.error = "REJECTED", "risk engine at trigger: " + "; ".join(failed)
            event(db, "pending", f"{p.order_type} {p.side} {p.symbol} rejected at trigger: {p.error}", "WARNING")
        else:
            p.status, p.order_client_id = "TRIGGERED", o.client_id
            audit(db, "pending.triggered", user=p.created_by, entity="pending", entity_id=p.id,
                  details={"symbol": p.symbol, "side": p.side, "type": p.order_type, "trigger": p.trigger_price, "order": o.client_id})
            notify(f"⏱ {p.order_type} {p.side} {p.symbol} triggered at {p.trigger_price}")


def _atr(db: Session, symbol: str, cache: dict) -> float | None:
    if symbol not in cache:
        cfg = state.get(db, "strategy")
        tf = (cfg.get("trade_timeframes") or ["H1"])[0]
        df = md.load_candles(db, symbol, tf, limit=200)
        cache[symbol] = float(add_indicators(df, cfg)["atr"].iloc[-1]) if len(df) > 30 else None
    return cache[symbol]


def manage_positions(db: Session) -> None:
    """Breakeven and trailing stop. Stops are only ever tightened."""
    mode = state.get(db, "trading")["mode"]
    if mode not in trading.ORDER_MODES:
        return
    quotes = md.latest_quotes(db)
    atr_cache: dict = {}
    for p in trading.open_positions(db, mode):
        m = db.get(PositionMgmt, p.id)
        q = quotes.get(p.symbol)
        if m is None or q is None or not p.stop_loss:
            continue
        if m.last_action_at and (utcnow() - m.last_action_at).total_seconds() < 30:
            continue  # give the broker time to apply the previous change
        px = q.bid if p.side == "BUY" else q.ask
        risk = abs(p.open_price - m.initial_sl)
        if risk <= 0:
            continue
        progress = (px - p.open_price if p.side == "BUY" else p.open_price - px) / risk  # in R
        target = None
        reason = ""
        if m.breakeven_r and not m.breakeven_done and progress >= m.breakeven_r:
            target, reason = p.open_price, f"breakeven at +{m.breakeven_r}R"
        if m.trailing_atr:
            atr = _atr(db, p.symbol, atr_cache)
            if atr:
                trail = px - m.trailing_atr * atr if p.side == "BUY" else px + m.trailing_atr * atr
                if target is None or (p.side == "BUY" and trail > target) or (p.side == "SELL" and trail < target):
                    target, reason = trail, f"trailing {m.trailing_atr}×ATR"
        if target is None:
            continue
        digits = 3 if p.symbol.endswith("JPY") else 5
        target = round(target, digits)
        better = target > p.stop_loss + 1e-9 if p.side == "BUY" else target < p.stop_loss - 1e-9
        if not better:
            continue
        try:
            trading.request_modify(db, p, target, None, "system", reason)
            m.last_action_at = utcnow()
            if reason.startswith("breakeven"):
                m.breakeven_done = True
        except ValueError as e:
            log.info("manage %s skipped: %s", p.id, e)
