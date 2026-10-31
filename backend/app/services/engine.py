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
from ..models import BridgeNonce, LoginAttempt, Order, Signal, SignalApproval, SmcEvent, UserSession, utcnow
from . import market_data as md
from . import strategy as strat
from . import trading
from .indicators import required_history
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

    ind = strat.prepare(df, cfg)
    res = strat.evaluate(ind, len(ind) - 1, cfg)
    try:
        log_smc_events(db, symbol, tf, ind, cfg)
    except Exception:
        log.exception("SMC event logging failed for %s %s", symbol, tf)
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
        try:  # still score it, so research/shadow statistics include every signal
            from . import assess
            assess.lifecycle(db, sig.id, "DETECTED", f"signal only ({mode})")
            assess.assess(db, sig)
        except Exception:
            log.exception("assessment failed for signal %s", sig.id)
        return sig

    dup = db.scalar(select(Order.id).where(Order.client_id == f"S{sig.id}-{mode}")) is not None
    ctx = trading.build_context(db, symbol=symbol, side=res.action, tf=tf, sl=res.stop_loss, tp=res.take_profit,
                                mode=mode, duplicate=dup)
    decision = risk_evaluate(ctx)
    sig.risk_checks = decision.checks
    sig.features = {**sig.features, "risk": {k: v for k, v in decision.to_dict().items() if k != "checks"}}
    from . import assess
    outcome, a = assess.route(db, sig, decision, ctx)
    if outcome != "QUALIFIED":
        if outcome in ("REJECTED", "BLOCKED_DATA"):
            event(db, "risk", f"{symbol} {tf} {res.action} {outcome.lower()}: {'; '.join(a.reasons[:2])}", "INFO")
        return sig
    from .automation import announce, auto_ok
    if not auto_ok(db, mode, a):
        assess.request_approval(db, sig, decision, int(controls.get("approval_minutes", 60)))
        event(db, "approval", f"{symbol} {tf} {res.action} setup waiting for your approval ({a.score}/100)", "INFO")
        return sig
    o = trading.place_order(db, sig, decision, mode)
    sig.status = "ORDERED"
    assess.lifecycle(db, sig.id, "AUTO_ORDERED", f"{mode} · no approval (auto-trade on)")
    announce(db, sig, o.filled_volume or o.volume, o.fill_price or o.requested_price, mode)
    return sig


LOG_KINDS = {"sweep_bsl", "sweep_ssl", "bos", "choch", "fvg", "swing_high", "swing_low", "displacement"}


def log_smc_events(db: Session, symbol: str, tf: str, ind, cfg: dict) -> None:
    """Store SMC detections from the last few closed candles (idempotent)."""
    from sqlalchemy.dialects.postgresql import insert
    from . import smc
    if "atr" not in ind:
        return
    window = ind.tail(250)
    rows = []
    for e in smc.analyze(window, cfg)["events"]:
        if e["kind"] not in LOG_KINDS or e["i"] < 20:  # skip the warm-up part of the window
            continue
        data = {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in e.items()
                if k not in ("i", "time", "kind", "dir", "level", "price", "zone_low", "zone_high")}
        rows.append({"symbol": symbol, "timeframe": tf, "kind": e["kind"], "dir": e["dir"], "time": e["time"].to_pydatetime(),
                     "level": e.get("level", e.get("price")), "zone_low": e.get("zone_low"), "zone_high": e.get("zone_high"), "data": data})
    if rows:
        db.execute(insert(SmcEvent).values(rows).on_conflict_do_nothing(constraint="uq_smc_event"))


def expire_approvals(db: Session) -> None:
    from .alerts import raise_alert
    from .assess import lifecycle
    now = utcnow()
    for a in db.scalars(select(SignalApproval).where(SignalApproval.status == "WAITING", SignalApproval.expires_at < now + timedelta(minutes=10),
                                                     SignalApproval.expires_at >= now)).all():
        sig = db.get(Signal, a.signal_id)
        raise_alert(db, "APPROVAL_EXPIRING", f"{sig.symbol if sig else ''} setup expires in <10 min", "Review it on Setups to approve.",
                    dedup=f"expiring:{a.signal_id}", signal_id=a.signal_id, link="#/setups")
    for a in db.scalars(select(SignalApproval).where(SignalApproval.status == "WAITING", SignalApproval.expires_at < now)).all():
        a.status, a.decided_at, a.note = "EXPIRED", now, a.note or "not approved in time"
        sig = db.get(Signal, a.signal_id)
        if sig is not None and sig.status == "AWAITING":
            sig.status = "EXPIRED"
            lifecycle(db, sig.id, "EXPIRED", "no decision before expiry")
            raise_alert(db, "SIGNAL_EXPIRED", f"{sig.symbol} {sig.action} setup expired", "No decision was made in time — recorded for research.",
                        dedup=f"exp:{sig.id}", symbol=sig.symbol, signal_id=sig.id)


_periodic: dict = {}


def every(name: str, seconds: float) -> bool:
    import time as _t
    if _t.time() - _periodic.get(name, 0) >= seconds:
        _periodic[name] = _t.time()
        return True
    return False


def research_jobs(db: Session) -> None:
    """Data incidents, SMC event log, shadow outcomes, scheduled alerts and alert delivery (fail-safe)."""
    from . import alerts, assess, headlines, quality
    jobs = [("incidents", 30, quality.track_incidents), ("smc_log", 120, log_all_smc), ("outcomes", 60, assess.track_outcomes),
            ("scheduled", 60, alerts.scheduled), ("headlines", 60, headlines.refresh), ("reconcile", 300, reconcile_paper), ("deliver", 5, alerts.deliver_pending)]
    for name, sec, fn in jobs:
        if not every(name, sec):
            continue
        try:
            fn(db)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("job %s failed", name)


def log_all_smc(db: Session) -> None:
    """Persist SMC detections for every watched market/timeframe, independent of the trading mode."""
    from .indicators import add_indicators
    cfg = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    pairs = [(s, tf) for s in cfg["symbols"] for tf in cfg["timeframes"]] + [(s, tf) for s in cc.get("symbols", []) for tf in ("M15", "H1")]
    for s, tf in pairs:
        df = md.load_candles(db, s, tf, limit=260)
        if len(df) < 60:
            continue
        key = (s, tf)
        if _periodic.get(key) == df.index[-1]:
            continue
        log_smc_events(db, s, tf, add_indicators(df, cfg), cfg)
        _periodic[key] = df.index[-1]


def reconcile_paper(db: Session) -> None:
    """Paper ledger check: balance must equal start + realized P&L of closed paper trades."""
    from sqlalchemy import func

    from ..models import DataIncident, Position
    from .alerts import raise_alert
    pa = state.get(db, "paper_account")
    start = float(pa.get("start_balance", pa.get("initial_balance", 0)) or 0)
    if not start:
        return
    q = select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(Position.mode == "PAPER", Position.status == "CLOSED")
    if pa.get("reset_at"):
        from datetime import datetime as _dt
        q = q.where(Position.closed_at >= _dt.fromisoformat(pa["reset_at"]))
    realized = db.scalar(q) or 0
    diff = round(float(pa["balance"]) - (start + float(realized)), 2)
    key = "RECONCILIATION:PAPER"
    open_inc = db.scalar(select(DataIncident).where(DataIncident.key == key, DataIncident.ended_at.is_(None)))
    if abs(diff) > 0.05 and open_inc is None:
        inc = DataIncident(key=key, kind="RECONCILIATION", symbol="", detail=f"paper balance differs from ledger by {diff:+.2f}")
        db.add(inc)
        db.flush()
        raise_alert(db, "RECONCILIATION", "Paper ledger mismatch", inc.detail, severity="warning", dedup=f"rec:{inc.id}")
    elif abs(diff) <= 0.05 and open_inc is not None:
        open_inc.ended_at = utcnow()


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
    expire_approvals(db)
    db.commit()

    from . import news, pro
    try:
        news.refresh(db)
    except Exception as e:
        db.rollback()
        log.warning("news refresh failed: %s", e)
    research_jobs(db)
    from .livegate import is_approved
    if mode == "MT5_LIVE" and not is_approved(db):
        tr2 = state.get_locked(db, "trading")
        tr2["mode"] = "MT5_DEMO"
        state.put(db, "trading", tr2)
        event(db, "risk", "Live gate approval expired or revoked: mode switched to BROKER DEMO", "WARNING")
        db.commit()
        mode = "MT5_DEMO"
    from .crypto import desk
    try:
        desk.tick(db)
    except Exception:
        db.rollback()
        log.exception("crypto desk tick failed")
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
