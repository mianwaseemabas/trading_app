"""Order creation, paper broker, account/limit accounting and order signing."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit, event
from ..config import settings
from ..models import AccountSnapshot, Candle, Instrument, Order, Position, Quote, Signal, utcnow
from . import market_data as md
from .notifier import notify
from .risk_engine import AccountState, InstrumentSpec, OpenPos, RiskContext, RiskDecision, floor_to_step
from .risk_engine import evaluate as risk_evaluate

PAPER_LEVERAGE = 100
MT5_MODES = {"MT5_DEMO", "MT5_LIVE"}
ORDER_MODES = {"PAPER", "MT5_DEMO", "MT5_LIVE"}


# ------------------------------------------------------------------ helpers
def spec_of(inst: Instrument) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=inst.symbol,
        point=inst.point,
        tick_size=inst.tick_size,
        tick_value=inst.tick_value if inst.source == "mt5" else None,
        contract_size=inst.contract_size,
        volume_min=inst.volume_min,
        volume_max=inst.volume_max,
        volume_step=inst.volume_step,
        currency_base=inst.currency_base,
        currency_profit=inst.currency_profit,
        trade_allowed=inst.trade_allowed,
    )


def is_fx(inst: Instrument | None) -> bool:
    """Forex pairs carry a base currency; stocks, indices, commodities and CFDs do not."""
    return bool(inst is None or inst.currency_base)


def asset_cfg(cfg: dict, inst: Instrument | None, price: float) -> dict:
    """Risk settings are written for forex (points, $ per standard lot). For other assets the
    spread/slippage limits become a % of price and no per-lot commission is applied."""
    if is_fx(inst) or not inst or not inst.point or not price:
        return cfg
    out = dict(cfg)
    out["max_spread_points"] = float(cfg.get("max_spread_pct_other", 0.2)) / 100 * price / inst.point
    out["max_slippage_points"] = max(1, round(float(cfg.get("slippage_pct_other", 0.05)) / 100 * price / inst.point))
    out["commission_per_lot"] = 0.0
    return out


def value_per_unit(db: Session, inst: Instrument, account_ccy: str, quotes: dict) -> float | None:
    if inst.source == "mt5" and inst.tick_value and inst.tick_size:
        return inst.tick_value / inst.tick_size
    rate = md.conversion_rate(inst.currency_profit, account_ccy, quotes)
    return inst.contract_size * rate if rate else None


def sign_payload(payload: dict) -> str:
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(settings.bridge_secret.encode(), msg, hashlib.sha256).hexdigest()


def order_payload(o: Order) -> dict:
    return {
        "client_id": o.client_id,
        "action": o.action,
        "mode": o.mode,
        "symbol": o.symbol,
        "side": o.side,
        "volume": o.volume,
        "requested_price": o.requested_price,
        "stop_loss": o.stop_loss,
        "take_profit": o.take_profit,
        "max_slippage_points": o.max_slippage_points,
        "position_ticket": o.position_ticket,
        "expires_at": o.expires_at.isoformat() if o.expires_at else None,
    }


def open_positions(db: Session, mode: str) -> list[Position]:
    return db.scalars(select(Position).where(Position.mode == mode, Position.status == "OPEN")).all()


# --------------------------------------------------------------- accounting
def account_state(db: Session, mode: str) -> tuple[AccountState | None, dict]:
    """Return current account state for a trading mode, plus metadata."""
    if mode.startswith("CRYPTO_"):
        from .crypto import desk
        return desk.account(db, mode)
    if mode in MT5_MODES:
        snap = db.scalar(
            select(AccountSnapshot).where(AccountSnapshot.source == "mt5").order_by(AccountSnapshot.at.desc()).limit(1)
        )
        if snap is None:
            return None, {"error": "no MT5 account data yet"}
        age = (utcnow() - snap.at).total_seconds()
        return (
            AccountState(snap.equity, snap.balance, snap.free_margin, snap.currency, snap.leverage or 0),
            {"age_s": age, "trade_mode": snap.trade_mode, "login": snap.login, "server": snap.server,
             "margin": snap.margin, "source": "mt5"},
        )
    pa = state.get(db, "paper_account")
    positions = open_positions(db, "PAPER")
    unreal = sum(p.unrealized_pnl or 0 for p in positions)
    margin = 0.0
    for p in positions:
        inst = db.get(Instrument, p.symbol)
        rate = 1.0
        if inst and inst.currency_base != pa["currency"]:
            rate = p.current_price or p.open_price
        margin += p.volume * (inst.contract_size if inst else 100000) * rate / PAPER_LEVERAGE
    equity = pa["balance"] + unreal
    return (
        AccountState(equity, pa["balance"], equity - margin, pa["currency"], PAPER_LEVERAGE),
        {"margin": margin, "source": "paper", "trade_mode": "paper"},
    )


def _period_keys():
    now = utcnow()
    iso = now.isocalendar()
    return now.date().isoformat(), f"{iso[0]}-W{iso[1]:02d}"


def update_anchors(db: Session, mode: str, acct: AccountState) -> dict:
    """Maintain start-of-day / start-of-week equity anchors (UTC) per mode."""
    anchors = state.get(db, "anchors")
    day, week = _period_keys()
    a = anchors.get(mode, {})
    changed = False
    if a.get("day") != day:
        a.update(day=day, day_equity=acct.equity, day_balance=acct.balance)
        changed = True
    if a.get("week") != week:
        a.update(week=week, week_equity=acct.equity, week_balance=acct.balance)
        changed = True
    if changed:
        anchors[mode] = a
        state.put(db, "anchors", anchors)
    return a


def losses(acct: AccountState, anchor: dict, include_unrealized: bool) -> tuple[float, float]:
    if include_unrealized:
        return anchor["day_equity"] - acct.equity, anchor["week_equity"] - acct.equity
    return anchor["day_balance"] - acct.balance, anchor["week_balance"] - acct.balance


def check_loss_limits(db: Session, mode: str) -> dict | None:
    """Lock new trading if daily/weekly loss limits are breached. Returns loss info."""
    if mode not in ORDER_MODES and not mode.startswith("CRYPTO_"):
        return None
    acct, _ = account_state(db, mode)
    if acct is None:
        return None
    risk = state.get(db, "risk")
    anchor = update_anchors(db, mode, acct)
    day_loss, week_loss = losses(acct, anchor, risk["include_unrealized_in_limits"])
    day_lim = anchor["day_equity"] * risk["daily_loss_pct"] / 100
    week_lim = anchor["week_equity"] * risk["weekly_loss_pct"] / 100
    info = {"day_loss": day_loss, "week_loss": week_loss, "day_limit": day_lim, "week_limit": week_lim, "anchor": anchor}
    reason = None
    if day_loss >= day_lim:
        reason = f"Daily loss limit reached ({day_loss:.2f} >= {day_lim:.2f}) in {mode}"
    elif week_loss >= week_lim:
        reason = f"Weekly loss limit reached ({week_loss:.2f} >= {week_lim:.2f}) in {mode}"
    if reason:
        tr = state.get_locked(db, "trading")
        if not tr["locked"]:
            tr.update(locked=True, lock_reason=reason)
            state.put(db, "trading", tr)
            audit(db, "risk.locked", details={"reason": reason, **{k: v for k, v in info.items() if k != "anchor"}})
            event(db, "risk", reason, "WARNING")
            notify(f"⛔ {reason}. New trades blocked until manual review/reset.")
    return info


# ----------------------------------------------------------------- risk ctx
def data_freshness(db: Session, symbol: str, tf: str, data_cfg: dict, mode: str) -> tuple[bool, str]:
    last = db.scalar(
        select(Candle.time).where(Candle.symbol == symbol, Candle.timeframe == tf).order_by(Candle.time.desc()).limit(1)
    )
    if last is None:
        return False, "no candles"
    age = (utcnow() - (md.as_utc(last) + md.tf_delta(tf))).total_seconds()
    if age > md.TF_SECONDS[tf] + 120:
        return False, f"last closed {tf} candle is {age/60:.0f} min old"
    if mode in MT5_MODES:
        snap_at = db.scalar(select(func.max(AccountSnapshot.at)).where(AccountSnapshot.source == "mt5"))
        if snap_at is None or (utcnow() - snap_at).total_seconds() > data_cfg["stale_heartbeat_seconds"]:
            return False, "broker connector heartbeat stale"
    return True, "ok"


def build_context(db: Session, *, symbol: str, side: str, tf: str, sl: float | None, tp: float | None,
                  mode: str, duplicate: bool = False) -> RiskContext | None:
    cfg = state.get(db, "risk")
    tr = state.get(db, "trading")
    data_cfg = state.get(db, "data")
    inst = md.ensure_instrument(db, symbol)
    quotes = md.latest_quotes(db)
    q = quotes.get(symbol)
    acct, meta = account_state(db, mode)
    if acct is None:
        acct = AccountState(0, 0, 0)
    anchor = update_anchors(db, mode, acct)
    day_loss, week_loss = losses(acct, anchor, cfg["include_unrealized_in_limits"])
    data_ok, data_detail = data_freshness(db, symbol, tf, data_cfg, mode)
    if data_cfg["source"] == "simulator" and mode in MT5_MODES:
        data_ok, data_detail = False, "simulator data cannot be used for MT5 orders"
    rec = state.get(db, "reconciliation") if mode in MT5_MODES else {"ok": True, "detail": ""}
    if mode in MT5_MODES and meta.get("trade_mode"):
        expected = "demo" if mode == "MT5_DEMO" else "real"
        if meta["trade_mode"] != expected:
            rec = {"ok": False, "detail": f"Broker account is '{meta['trade_mode']}' but mode is {mode}"}
    positions = [OpenPos(p.symbol, p.side, p.unrealized_pnl or 0) for p in open_positions(db, mode)]
    pending = db.scalars(
        select(Order).where(Order.mode == mode, Order.action == "OPEN", Order.status.in_(["PENDING", "SENT"]))
    ).all()
    positions += [OpenPos(o.symbol, o.side, 0) for o in pending]
    controls = state.get(db, "controls")
    from .news import news_check
    extra = [("pair enabled for trading", symbol not in controls.get("disabled_symbols", []), symbol)]
    if controls.get("session_filter"):
        h, a, b = utcnow().hour, int(controls["session_start_utc"]), int(controls["session_end_utc"])
        inside = a <= h < b if a < b else (h >= a or h < b)
        extra.append(("inside trading hours", inside, f"{h:02d}:00 UTC, allowed {a:02d}:00–{b:02d}:00"))
    news_ok, news_detail = news_check(db, symbol, controls)
    extra.append(("no high-impact news window", news_ok, news_detail))
    cfg = asset_cfg(cfg, inst, q.ask if q else 0)
    return RiskContext(
        side=side,
        extra_checks=extra,
        symbol=symbol,
        bid=q.bid if q else 0,
        ask=q.ask if q else 0,
        quote_age_s=(utcnow() - q.collected_at).total_seconds() if q else 1e9,
        stop_loss=sl,
        take_profit=tp,
        spec=spec_of(inst),
        account=acct,
        cfg=cfg,
        open_positions=positions,
        kill_switch=tr["kill_switch"],
        locked=tr["locked"],
        lock_reason=tr["lock_reason"],
        mode=mode,
        day_loss=day_loss,
        week_loss=week_loss,
        day_anchor_equity=anchor["day_equity"],
        week_anchor_equity=anchor["week_equity"],
        stale_quote_seconds=data_cfg["stale_quote_seconds"],
        data_ok=data_ok,
        data_detail=data_detail,
        reconciliation_ok=bool(rec.get("ok", True)),
        reconciliation_detail=rec.get("detail", ""),
        profit_to_account_rate=md.conversion_rate(inst.currency_profit, acct.currency, quotes),
        base_to_account_rate=md.conversion_rate(inst.currency_base, acct.currency, quotes) if inst.currency_base else None,
        duplicate=duplicate,
    )


# ------------------------------------------------------------------- orders
def place_order(db: Session, sig: Signal, decision: RiskDecision, mode: str) -> Order:
    cfg = state.get(db, "risk")
    o = Order(
        client_id=f"S{sig.id}-{mode}",
        signal_id=sig.id,
        mode=mode,
        action="OPEN",
        symbol=sig.symbol,
        side=sig.action,
        volume=decision.volume,
        requested_price=decision.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        max_slippage_points=int(cfg["max_slippage_points"]),
        risk_amount=decision.risk_amount,
        expires_at=utcnow() + timedelta(seconds=int(cfg["order_ttl_seconds"])),
    )
    db.add(o)
    db.flush()
    if mode == "PAPER":
        paper_fill(db, o)
    else:
        o.signature = sign_payload(order_payload(o))
    audit(db, "order.created", entity="order", entity_id=o.client_id,
          details={"mode": mode, "symbol": o.symbol, "side": o.side, "volume": o.volume,
                   "sl": o.stop_loss, "tp": o.take_profit, "risk": o.risk_amount})
    notify(f"{'📝' if mode == 'PAPER' else '📤'} {mode} {o.side} {o.volume} {o.symbol} SL {o.stop_loss:.5f} "
           f"TP {o.take_profit or 0:.5f} risk {o.risk_amount:.2f}", state.get(db, "notifications")["notify_orders"])
    return o


def paper_fill(db: Session, o: Order) -> None:
    q = db.get(Quote, o.symbol)
    inst = db.get(Instrument, o.symbol)
    if q is None or inst is None:
        o.status, o.error = "REJECTED", "no quote"
        return
    price = q.ask if o.side == "BUY" else q.bid
    o.status = "FILLED"
    o.fill_price = price
    o.filled_volume = o.volume
    o.slippage_points = round(abs(price - o.requested_price) / inst.point, 1)
    o.sl_verified = True
    pos = Position(mode="PAPER", order_id=o.id, symbol=o.symbol, side=o.side, volume=o.volume,
                   open_price=price, stop_loss=o.stop_loss, take_profit=o.take_profit, current_price=price,
                   broker_ticket=f"P{o.id}")
    db.add(pos)
    db.flush()
    attach_management(db, pos, management_for_order(db, o))


def close_paper_position(db: Session, p: Position, price: float, reason: str) -> None:
    pa = state.get_locked(db, "paper_account")
    inst = db.get(Instrument, p.symbol)
    quotes = md.latest_quotes(db)
    vpu = value_per_unit(db, inst, pa["currency"], quotes) or 0
    gross = (price - p.open_price if p.side == "BUY" else p.open_price - price) * p.volume * vpu
    commission = state.get(db, "risk")["commission_per_lot"] * p.volume if is_fx(inst) else 0.0
    p.status, p.closed_at, p.close_price, p.close_reason = "CLOSED", utcnow(), price, reason
    p.commission = commission
    p.realized_pnl = round(gross - commission, 2)
    p.unrealized_pnl = 0
    pa["balance"] = round(pa["balance"] + p.realized_pnl, 2)
    state.put(db, "paper_account", pa)
    audit(db, "position.closed", entity="position", entity_id=p.id,
          details={"mode": "PAPER", "symbol": p.symbol, "reason": reason, "pnl": p.realized_pnl})
    notify(f"✅ PAPER closed {p.side} {p.volume} {p.symbol} ({reason}) P&L {p.realized_pnl:+.2f}",
           state.get(db, "notifications")["notify_orders"])


def update_paper_positions(db: Session) -> None:
    quotes = md.latest_quotes(db)
    ccy = state.get(db, "paper_account")["currency"]
    for p in open_positions(db, "PAPER"):
        q = quotes.get(p.symbol)
        inst = db.get(Instrument, p.symbol)
        if q is None or inst is None:
            continue
        exit_px = q.bid if p.side == "BUY" else q.ask
        p.current_price = exit_px
        if p.side == "BUY":
            if p.stop_loss and exit_px <= p.stop_loss:
                close_paper_position(db, p, exit_px, "stop_loss")
                continue
            if p.take_profit and exit_px >= p.take_profit:
                close_paper_position(db, p, exit_px, "take_profit")
                continue
        else:
            if p.stop_loss and exit_px >= p.stop_loss:
                close_paper_position(db, p, exit_px, "stop_loss")
                continue
            if p.take_profit and exit_px <= p.take_profit:
                close_paper_position(db, p, exit_px, "take_profit")
                continue
        vpu = value_per_unit(db, inst, ccy, quotes) or 0
        p.unrealized_pnl = round((exit_px - p.open_price if p.side == "BUY" else p.open_price - exit_px) * p.volume * vpu, 2)


def request_close(db: Session, p: Position, reason: str, by: str) -> Order | None:
    """Close a position: immediately for PAPER, via a signed close order for MT5."""
    if p.status != "OPEN":
        return None
    if p.mode.startswith("CRYPTO_"):
        from .crypto import desk
        desk.close(db, p, reason, by)
        return None
    if p.mode == "PAPER":
        q = db.get(Quote, p.symbol)
        if q is None:
            raise ValueError("no quote available")
        close_paper_position(db, p, q.bid if p.side == "BUY" else q.ask, reason)
        return None
    existing = db.scalar(select(Order).where(Order.position_id == p.id, Order.action == "CLOSE",
                                             Order.status.in_(["PENDING", "SENT"])))
    if existing:
        return existing
    o = Order(
        client_id=f"C{p.id}-{int(utcnow().timestamp())}",
        mode=p.mode,
        action="CLOSE",
        position_ticket=p.broker_ticket,
        position_id=p.id,
        symbol=p.symbol,
        side="SELL" if p.side == "BUY" else "BUY",
        volume=p.volume,
        requested_price=p.current_price or p.open_price,
        stop_loss=None,
        take_profit=None,
        max_slippage_points=int(state.get(db, "risk")["max_slippage_points"]) * 3,
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(o)
    db.flush()
    o.signature = sign_payload(order_payload(o))
    audit(db, "order.close_requested", user=by, entity="position", entity_id=p.id, details={"reason": reason})
    return o


def expire_orders(db: Session) -> None:
    now = utcnow()
    for o in db.scalars(select(Order).where(Order.status == "PENDING", Order.expires_at < now)).all():
        o.status, o.error = "EXPIRED", "not picked up by the bridge before expiry"
        event(db, "execution", f"Order {o.client_id} expired", "WARNING")
    # Orders sent to the bridge but never acknowledged: flag reconciliation.
    stuck = db.scalars(
        select(Order).where(Order.status == "SENT", Order.updated_at < now - timedelta(minutes=2))
    ).all()
    if stuck:
        rec = state.get(db, "reconciliation")
        rec.update(ok=False, detail=f"{len(stuck)} order(s) sent to bridge without result", at=now.isoformat())
        state.put(db, "reconciliation", rec)


# ------------------------------------------------------------- pro features
SIZING_MODES = ("risk_pct", "risk_amount", "lots", "auto")


def apply_sizing(decision: RiskDecision, ctx: RiskContext, mode: str, value: float | None) -> None:
    """Resize an evaluated decision according to the user's sizing choice.

    The risk engine's volume is the MAXIMUM allowed by the risk policy; any choice is capped by it.
      auto        - use the maximum allowed by the policy (risk per trade)
      risk_pct    - risk this % of equity (<= policy)
      risk_amount - risk this many account-currency units, e.g. $2 (<= policy budget)
      lots        - fixed lots
    """
    if mode in (None, "", "auto") or value in (None, "") or decision.loss_per_lot <= 0:
        return
    spec = ctx.spec
    if mode == "risk_pct":
        target = float(value)
        amount = ctx.account.equity * target / 100
        vol = floor_to_step(amount / decision.loss_per_lot, spec.volume_step)
        label = f"risk {target}% = {amount:.2f}"
    elif mode == "risk_amount":
        amount = float(value)
        vol = floor_to_step(amount / decision.loss_per_lot, spec.volume_step)
        label = f"risk amount {amount:.2f}"
    elif mode == "lots":
        vol = floor_to_step(float(value), spec.volume_step)
        label = f"{vol} lots"
    else:
        raise ValueError("unknown sizing mode")
    ok = spec.volume_min - 1e-12 <= vol <= decision.volume + 1e-12
    detail = f"{label} -> {vol} lots (allowed {spec.volume_min}–{decision.volume} lots by your risk policy)"
    decision.checks.append({"check": "chosen size within risk policy", "passed": ok, "detail": detail})
    if ok:
        decision.volume = vol
        decision.risk_amount = round(vol * decision.loss_per_lot, 2)
    decision.approved = decision.approved and ok


def potential_outcomes(decision: RiskDecision, ctx: RiskContext) -> dict:
    """Money at stake if SL / TP is hit (approximate, includes commission)."""
    vpu = None
    if ctx.spec.tick_value and ctx.spec.tick_size:
        vpu = ctx.spec.tick_value / ctx.spec.tick_size
    elif ctx.profit_to_account_rate:
        vpu = ctx.spec.contract_size * ctx.profit_to_account_rate
    if not vpu or not decision.volume:
        return {}
    comm = float(ctx.cfg.get("commission_per_lot", 0)) * decision.volume
    loss = abs(decision.entry_price - (ctx.stop_loss or decision.entry_price)) * decision.volume * vpu + comm
    gain = abs((ctx.take_profit or decision.entry_price) - decision.entry_price) * decision.volume * vpu - comm if ctx.take_profit else None
    return {"loss_if_sl": round(loss, 2), "gain_if_tp": round(gain, 2) if gain is not None else None,
            "reward_risk": round(gain / loss, 2) if gain and loss else None,
            "pip_value": round(vpu * decision.volume * (0.01 if ctx.symbol.endswith("JPY") else 0.0001), 4)}


def attach_management(db: Session, p: Position, management: dict | None) -> None:
    from ..models import PositionMgmt
    m = management or {}
    controls = state.get(db, "controls")
    be = m.get("breakeven_r", controls.get("default_breakeven_r"))
    tr = m.get("trailing_atr", controls.get("default_trailing_atr"))
    if (be or tr) and p.stop_loss and db.get(PositionMgmt, p.id) is None:
        db.add(PositionMgmt(position_id=p.id, initial_sl=p.stop_loss, breakeven_r=be or None, trailing_atr=tr or None))


def management_for_order(db: Session, o: Order) -> dict | None:
    sig = db.get(Signal, o.signal_id) if o.signal_id else None
    return (sig.features or {}).get("management") if sig else None


def request_modify(db: Session, p: Position, new_sl: float | None, new_tp: float | None, by: str, reason: str,
                   allow_widen: bool = False) -> Order | None:
    """Change SL/TP of an open position. The stop-loss may only be tightened (never increase risk)."""
    if p.status != "OPEN":
        raise ValueError("position is not open")
    sl = p.stop_loss if new_sl is None else new_sl
    tp = p.take_profit if new_tp is None else (new_tp or None)
    q = db.get(Quote, p.symbol)
    px = (q.bid if p.side == "BUY" else q.ask) if q else (p.current_price or p.open_price)
    if sl is None:
        raise ValueError("a stop-loss is required")
    if p.side == "BUY":
        if sl >= px:
            raise ValueError(f"stop-loss must be below the current price {px}")
        if not allow_widen and p.stop_loss and sl < p.stop_loss - 1e-12:
            raise ValueError("the stop-loss can only be moved closer (tightened), never further away")
        if tp is not None and tp <= px:
            raise ValueError(f"take-profit must be above the current price {px}")
    else:
        if sl <= px:
            raise ValueError(f"stop-loss must be above the current price {px}")
        if not allow_widen and p.stop_loss and sl > p.stop_loss + 1e-12:
            raise ValueError("the stop-loss can only be moved closer (tightened), never further away")
        if tp is not None and tp >= px:
            raise ValueError(f"take-profit must be below the current price {px}")
    if p.mode.startswith("CRYPTO_"):
        from .crypto import desk
        desk.modify(db, p, sl, tp, by, reason)
        return None
    if p.mode == "PAPER":
        old = (p.stop_loss, p.take_profit)
        p.stop_loss, p.take_profit = sl, tp
        audit(db, "position.sl_tp_modified", user=by, entity="position", entity_id=p.id,
              details={"from": old, "to": (sl, tp), "reason": reason, "mode": "PAPER"})
        return None
    pending = db.scalar(select(Order).where(Order.position_id == p.id, Order.action == "MODIFY",
                                            Order.status.in_(["PENDING", "SENT"])))
    if pending:
        pending.status, pending.error = "CANCELLED", "superseded by a newer modification"
    o = Order(client_id=f"M{p.id}-{int(utcnow().timestamp() * 1000)}", mode=p.mode, action="MODIFY",
              position_ticket=p.broker_ticket, position_id=p.id, symbol=p.symbol, side=p.side, volume=p.volume,
              requested_price=px, stop_loss=sl, take_profit=tp, max_slippage_points=0,
              expires_at=utcnow() + timedelta(minutes=2))
    db.add(o)
    db.flush()
    o.signature = sign_payload(order_payload(o))
    audit(db, "order.modify_requested", user=by, entity="position", entity_id=p.id,
          details={"sl": sl, "tp": tp, "reason": reason})
    return o
