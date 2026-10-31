"""Trading engine tick: data -> indicators -> strategy -> risk -> execution.

Run by the single `worker` process (guarded by a PostgreSQL advisory lock).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import state
from ..audit import event
from ..models import BridgeNonce, LoginAttempt, Order, Signal, UserSession, utcnow
from . import market_data as md
from . import strategy as strat
from . import trading
from .indicators import add_indicators, required_history
from .notifier import notify
from .risk_engine import evaluate as risk_evaluate

log = logging.getLogger("engine")


def evaluate_symbol(db: Session, symbol: str, tf: str, cfg: dict, mode: str) -> Signal | None:
    df = md.load_candles(db, symbol, tf, limit=required_history(cfg) + 50)
    if len(df) < required_history(cfg) // 2:
        return None
    last_time = df.index[-1].to_pydatetime()
    if not md.is_closed(last_time, tf):
        df = df.iloc[:-1]
        last_time = df.index[-1].to_pydatetime()
    exists = db.scalar(
        select(Signal.id).where(
            Signal.symbol == symbol, Signal.timeframe == tf, Signal.candle_time == last_time,
            Signal.strategy_version == cfg["version"],
        )
    )
    if exists:
        return None
    # Do not act on old candles (e.g. after downtime or over the weekend).
    if (utcnow() - (last_time + md.tf_delta(tf))).total_seconds() > md.TF_SECONDS[tf]:
        return None

    ind = add_indicators(df, cfg)
    res = strat.evaluate(ind, len(ind) - 1, cfg)
    sig = Signal(
        symbol=symbol, timeframe=tf, candle_time=last_time, strategy=cfg["name"], strategy_version=cfg["version"],
        action=res.action, entry_ref=res.entry_ref, stop_loss=res.stop_loss, take_profit=res.take_profit,
        features={**res.features, "gaps": md.gap_report(df.tail(300), tf)}, reasons=res.reasons,
        status="NO_TRADE", trading_mode=mode,
    )
    db.add(sig)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None

    if res.action == "NONE":
        return sig
    controls = state.get(db, "controls")
    can_trade = mode in trading.ORDER_MODES and tf in cfg.get("trade_timeframes", []) and controls.get("auto_trading", True)
    if not can_trade:
        sig.status = "SIGNAL_ONLY"
        if not controls.get("auto_trading", True):
            sig.reasons = list(sig.reasons) + [{"check": "auto trading switched off", "passed": False}]
        notify(f"📈 Signal {res.action} {symbol} {tf} (not traded: mode {mode})",
               state.get(db, "notifications")["notify_signals"])
        return sig

    dup = db.scalar(select(Order.id).where(Order.client_id == f"S{sig.id}-{mode}")) is not None
    ctx = trading.build_context(db, symbol=symbol, side=res.action, tf=tf, sl=res.stop_loss, tp=res.take_profit,
                                mode=mode, duplicate=dup)
    decision = risk_evaluate(ctx)
    sig.risk_checks = decision.checks
    sig.features = {**sig.features, "risk": {k: v for k, v in decision.to_dict().items() if k != "checks"}}
    if not decision.approved:
        sig.status = "REJECTED"
        failed = [c["check"] for c in decision.checks if not c["passed"]]
        event(db, "risk", f"{symbol} {tf} {res.action} rejected: {', '.join(failed)}", "INFO")
        return sig
    trading.place_order(db, sig, decision, mode)
    sig.status = "ORDERED"
    return sig


def tick(db: Session) -> None:
    tr = state.get(db, "trading")
    data_cfg = state.get(db, "data")
    cfg = state.get(db, "strategy")
    mode = tr["mode"]

    if data_cfg["source"] == "simulator":
        md.run_simulator(db, cfg["symbols"], cfg["timeframes"])
        db.commit()

    trading.update_paper_positions(db)
    trading.expire_orders(db)
    trading.check_loss_limits(db, mode)
    db.commit()

    from . import news, pro
    try:
        news.refresh(db)
    except Exception as e:
        db.rollback()
        log.warning("news refresh failed: %s", e)
    for job in (pro.process_pending, pro.manage_positions):
        try:
            job(db)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("%s failed", job.__name__)

    if mode == "OFF":
        return
    for symbol in cfg["symbols"]:
        md.ensure_instrument(db, symbol)
        for tf in cfg["timeframes"]:
            try:
                evaluate_symbol(db, symbol, tf, cfg, mode)
                db.commit()
            except Exception as e:  # fail closed for this symbol, keep the engine alive
                db.rollback()
                log.exception("evaluate %s %s failed", symbol, tf)
                event(db, "engine", f"Evaluation failed for {symbol} {tf}: {e}", "ERROR")
                db.commit()


def housekeeping(db: Session) -> None:
    now = utcnow()
    db.execute(delete(BridgeNonce).where(BridgeNonce.at < now - timedelta(minutes=10)))
    db.execute(delete(UserSession).where(UserSession.expires_at < now - timedelta(days=7)))
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < now - timedelta(days=90)))
    db.commit()
