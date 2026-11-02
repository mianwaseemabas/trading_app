"""Crypto desk: spot trading on Binance / OKX / Bybit next to the forex system.

Modes (setting `crypto.mode`):
  OFF    — market data only (watchlist, charts, analysis)
  PAPER  — virtual USDT account filled at real exchange prices (no keys needed)
  TEST   — orders on the exchange's test environment (Binance Spot Testnet, OKX demo, Bybit testnet)
  LIVE   — real money; needs keys, "allow real" and your password

Positions live in the shared `positions` table with mode CRYPTO_PAPER / CRYPTO_TEST /
CRYPTO_LIVE, so the journal, detail view and performance pages work unchanged. The same
risk engine sizes and approves every order; strategy setups always wait for approval.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import state
from ...audit import audit, event
from ...models import CryptoLink, Instrument, Order, Position, Quote, Signal, SignalApproval, utcnow
from ...secretbox import decrypt
from .. import market_data as md
from ..chart import record_tick
from ..notifier import notify
from ..risk_engine import AccountState, OpenPos, RiskContext, evaluate
from . import exchanges as ex

log = logging.getLogger("crypto")
MODE_OF = {"PAPER": "CRYPTO_PAPER", "TEST": "CRYPTO_TEST", "LIVE": "CRYPTO_LIVE"}
CRYPTO_MODES = set(MODE_OF.values())
_mem: dict = {"candles_at": {}, "backoff_until": 0.0, "bal_at": 0.0, "prot_at": {}, "chg_at": 0.0}


def is_crypto_mode(mode: str | None) -> bool:
    return bool(mode) and mode.startswith("CRYPTO_")


def cfg(db: Session) -> dict:
    return state.get(db, "crypto")


def pos_mode(db: Session) -> str | None:
    return MODE_OF.get(cfg(db)["mode"])


def creds(db: Session, name: str) -> dict:
    k = state.get(db, "crypto_keys").get(name, {})
    return {f: decrypt(k[f + "_enc"]) if k.get(f + "_enc") else "" for f in ("api_key", "secret", "passphrase")}


def has_keys(db: Session, name: str) -> bool:
    c = state.get(db, "crypto_keys").get(name, {})
    return bool(c.get("api_key_enc") and c.get("secret_enc") and (name != "okx" or c.get("passphrase_enc")))


def client(db: Session, *, private: bool = False, mode: str | None = None, http=None) -> ex.Exchange:
    c = cfg(db)
    mode = mode or c["mode"]
    testnet = mode == "TEST"
    return ex.make(c["exchange"], creds(db, c["exchange"]) if private else None, testnet=testnet, http=http)


# ------------------------------------------------------------------ market data
def ensure_instrument(db: Session, x: ex.Exchange, symbol: str, price: float) -> Instrument:
    i = x.info(symbol)
    inst = db.get(Instrument, symbol) or Instrument(symbol=symbol)
    digits = max(0, len(ex.fmt_px(i.tick, i.tick).split(".")[1]) if "." in ex.fmt_px(i.tick, i.tick) else 0)
    min_by_notional = (i.min_notional * 1.1 / price) if price and i.min_notional else 0
    vol_min = max(i.min_qty, min_by_notional)
    steps = -(-vol_min // i.step) if i.step else 0
    inst.digits, inst.point, inst.tick_size, inst.tick_value = digits, i.tick, i.tick, None
    inst.contract_size, inst.volume_step, inst.volume_min, inst.volume_max = 1.0, i.step, round(steps * i.step, 12) or i.min_qty, 1e9
    inst.currency_base, inst.currency_profit, inst.trade_allowed, inst.source = i.base, i.quote, i.trading, "crypto"
    db.add(inst)
    return inst


def refresh_market(db: Session, http=None) -> None:
    """Quotes every tick, candles once a minute per symbol/timeframe. Public endpoints only."""
    c = cfg(db)
    if not c.get("symbols") or time.time() < _mem["backoff_until"]:
        return
    x = client(db, http=http)
    st = state.get(db, "crypto_status")
    try:
        for sym in c["symbols"]:
            bid, ask = x.book(sym)
            q = db.get(Quote, sym) or Quote(symbol=sym)
            q.bid, q.ask, q.time, q.collected_at, q.source = bid, ask, utcnow(), utcnow(), c["exchange"][:16]
            db.add(q)
            record_tick(db, sym, bid, ask)
            ensure_instrument(db, x, sym, ask)
            for tf in c.get("timeframes", ["H1"]):
                key = (c["exchange"], c["mode"] == "TEST", sym, tf)
                if time.time() - _mem["candles_at"].get(key, 0) < 60:
                    continue
                have = md.load_candles(db, sym, tf, limit=2)
                rows = x.klines(sym, tf, 500 if len(have) < 2 else 5)
                md.upsert_candles(db, sym, tf, rows, f"{c['exchange']}{'-test' if c['mode'] == 'TEST' else ''}"[:16])
                _mem["candles_at"][key] = time.time()
        if time.time() - _mem["chg_at"] > 300:
            st["changes"] = {s: x.change_24h(s) for s in c["symbols"]}
            _mem["chg_at"] = time.time()
        st.update(market_ok=True, market_error="", market_at=utcnow().isoformat(), market_error_since="")
    except Exception as e:  # network / exchange errors must never stop the forex engine
        _mem["backoff_until"] = time.time() + 60
        st.update(market_ok=False, market_error=str(e)[:300], market_at=utcnow().isoformat(),
                  market_error_since=st.get("market_error_since") or utcnow().isoformat())
        log.warning("crypto market data: %s", e)
    state.put(db, "crypto_status", st)


# --------------------------------------------------------------------- account
def account(db: Session, mode: str, http=None) -> tuple[AccountState | None, dict]:
    c = cfg(db)
    quote = "USDT"
    quotes = md.latest_quotes(db)
    positions = open_positions(db, mode)
    if mode == "CRYPTO_PAPER":
        cash = float(c["paper_cash"])
        value = sum(p.volume * (quotes[p.symbol].bid if p.symbol in quotes else p.open_price) for p in positions)
        return AccountState(cash + value, cash + value, cash, quote, 1), {"source": "paper", "cash": cash, "in_positions": value}
    st = state.get(db, "crypto_status")
    if time.time() - _mem["bal_at"] > 30 or st.get("balance_mode") != mode:
        try:
            st["balances"] = client(db, private=True, mode="TEST" if mode == "CRYPTO_TEST" else "LIVE", http=http).balances()
            st.update(balance_mode=mode, balance_at=utcnow().isoformat(), keys_ok=True, keys_error="")
        except Exception as e:
            st.update(keys_ok=False, keys_error=str(e)[:300])
        _mem["bal_at"] = time.time()
        state.put(db, "crypto_status", st)
    if st.get("balance_mode") != mode or not st.get("balances") and not st.get("keys_ok"):
        return None, {"error": st.get("keys_error") or "no exchange balance yet"}
    bal = st.get("balances") or {}
    cash = float(bal.get(quote, 0))
    value = 0.0
    for sym in c["symbols"]:
        base = ex.Exchange.split(sym)[0]
        if bal.get(base) and sym in quotes:
            value += bal[base] * quotes[sym].bid
    return AccountState(cash + value, cash + value, cash, quote, 1), {"source": c["exchange"], "cash": cash, "in_coins": value,
                                                                       "balances": bal, "at": st.get("balance_at")}


def open_positions(db: Session, mode: str) -> list[Position]:
    return db.scalars(select(Position).where(Position.mode == mode, Position.status == "OPEN")).all()


# ------------------------------------------------------------------------ risk
def build_context(db: Session, symbol: str, side: str, sl: float | None, tp: float | None, mode: str,
                  duplicate: bool = False) -> RiskContext:
    from .. import trading
    c = cfg(db)
    risk = dict(state.risk_for(db, mode))
    tr = state.get(db, "trading")
    controls = state.get(db, "controls")
    q = db.get(Quote, symbol)
    inst = db.get(Instrument, symbol)
    acct, meta = account(db, mode)
    acct = acct or AccountState(0, 0, 0, "USDT", 1)
    anchor = trading.update_anchors(db, mode, acct)
    day_loss, week_loss = trading.losses(acct, anchor, risk["include_unrealized_in_limits"])
    price = (q.ask if q else 0) or 1
    if inst and inst.point:
        risk["max_spread_points"] = float(c["max_spread_pct"]) / 100 * price / inst.point
        risk["max_slippage_points"] = max(1, round(float(c["slippage_pct"]) / 100 * price / inst.point))
    risk["commission_per_lot"] = 2 * float(c["fee_pct"]) / 100 * price  # round-trip fee per 1 coin
    tf = (c.get("signal_timeframes") or ["H1"])[0]
    df = md.load_candles(db, symbol, tf, limit=1)
    fresh = bool(len(df)) and (utcnow() - df.index[-1].to_pydatetime()).total_seconds() <= 3 * md.TF_SECONDS[tf]
    extra = [
        ("spot account: buy only (no short selling)", side == "BUY", "spot = you own the coins; selling short is not possible"),
        ("market enabled for trading", symbol not in controls.get("disabled_symbols", []) and symbol in c["symbols"], symbol),
        ("crypto trading switched on", c["mode"] != "OFF", c["mode"]),
    ]
    if mode != "CRYPTO_PAPER":
        extra.append(("exchange keys connected", has_keys(db, c["exchange"]) and meta.get("error") is None, meta.get("error", "")))
    if mode == "CRYPTO_LIVE":
        from ..livegate import is_approved
        extra.append(("real-money crypto allowed (Brokers page)", bool(c.get("allow_real")), ""))
        extra.append(("live gate approved and not expired", is_approved(db), "real money needs a current live-gate approval"))
    positions = [OpenPos(p.symbol, p.side, p.unrealized_pnl or 0) for p in open_positions(db, mode)]
    spec = trading.spec_of(inst) if inst else None
    if spec is None:
        from ..risk_engine import InstrumentSpec
        spec = InstrumentSpec(symbol, 0.01, 0.01, None, 1, 1e9, 1e9, 1, "", "USDT", False)
    return RiskContext(
        side=side, symbol=symbol, bid=q.bid if q else 0, ask=q.ask if q else 0,
        quote_age_s=(utcnow() - q.collected_at).total_seconds() if q else 1e9, stop_loss=sl, take_profit=tp, spec=spec,
        account=acct, cfg=risk, open_positions=positions, kill_switch=tr["kill_switch"], locked=tr["locked"],
        lock_reason=tr["lock_reason"], mode=mode, day_loss=day_loss, week_loss=week_loss,
        day_anchor_equity=anchor["day_equity"], week_anchor_equity=anchor["week_equity"], stale_quote_seconds=60,
        data_ok=fresh, data_detail="" if fresh else f"no recent {tf} candle", profit_to_account_rate=1.0,
        base_to_account_rate=None, duplicate=duplicate, extra_checks=extra, trades_today=trading.trades_today(db, mode),
    )


def preview(db: Session, symbol: str, sl: float, tp: float | None, sizing_mode: str, sizing_value: float | None) -> tuple:
    from .. import trading
    mode = pos_mode(db) or "CRYPTO_PAPER"
    ctx = build_context(db, symbol, "BUY", sl, tp, mode)
    d = evaluate(ctx)
    trading.apply_sizing(d, ctx, sizing_mode, sizing_value)
    return d, ctx, mode


# ------------------------------------------------------------------- execution
def _close_position(db: Session, p: Position, price: float, reason: str, fee: float, by: str = "system") -> None:
    gross = (price - p.open_price) * p.volume
    p.status, p.closed_at, p.close_price, p.close_reason = "CLOSED", utcnow(), price, reason
    p.commission = round((p.commission or 0) + fee, 6)
    p.realized_pnl = round(gross - p.commission, 2)
    p.unrealized_pnl, p.current_price = 0, price
    if p.mode == "CRYPTO_PAPER":
        c = state.get_locked(db, "crypto")
        c["paper_cash"] = round(float(c["paper_cash"]) + p.volume * price - fee, 6)
        state.put(db, "crypto", c)
    audit(db, "position.closed", user=by, entity="position", entity_id=p.id,
          details={"mode": p.mode, "symbol": p.symbol, "reason": reason, "pnl": p.realized_pnl, "price": price})
    notify(f"✅ {p.mode} closed {p.volume} {p.symbol} ({reason}) P&L {p.realized_pnl:+.2f} USDT",
           state.get(db, "notifications")["notify_orders"])


def place(db: Session, *, symbol: str, sl: float, tp: float | None, sizing_mode: str = "auto", sizing_value: float | None = None,
          by: str, source: str = "manual", signal: Signal | None = None, note: str = "", http=None) -> tuple[Position | None, list, dict]:
    """Risk-check and buy. Returns (position or None, checks, outcomes)."""
    from .. import trading
    mode = pos_mode(db)
    if mode is None:
        raise ValueError("Crypto trading is OFF — choose PAPER, TEST or LIVE on the Brokers page")
    dup = signal is not None and db.scalar(select(Order.id).where(Order.client_id == f"X{signal.id}-{mode}")) is not None
    ctx = build_context(db, symbol, "BUY", sl, tp, mode, duplicate=dup)
    d = evaluate(ctx)
    trading.apply_sizing(d, ctx, sizing_mode, sizing_value)
    if not d.approved:
        return None, d.checks, {}
    out = trading.potential_outcomes(d, ctx)
    c = cfg(db)
    if signal is None:
        signal = Signal(symbol=symbol, timeframe="MAN", candle_time=utcnow(), strategy=source, strategy_version=f"{source}-{by}",
                        action="BUY", entry_ref=d.entry_price, stop_loss=sl, take_profit=tp, status="ORDERED", trading_mode=mode,
                        features={"note": note, "placed_by": by, "sizing": {"mode": sizing_mode, "value": sizing_value},
                                  "risk": {k: v for k, v in d.to_dict().items() if k != "checks"}},
                        reasons=[{"check": f"{source} order", "passed": True, "detail": note or by}], risk_checks=d.checks)
        db.add(signal)
        db.flush()
    o = Order(client_id=f"X{signal.id}-{mode}", signal_id=signal.id, mode=mode, action="OPEN", symbol=symbol, side="BUY",
              volume=d.volume, requested_price=d.entry_price, stop_loss=sl, take_profit=tp, risk_amount=d.risk_amount,
              status="SENT", expires_at=utcnow() + timedelta(minutes=1))
    db.add(o)
    db.flush()
    fee_rate = float(c["fee_pct"]) / 100
    if mode == "CRYPTO_PAPER":
        inst = db.get(Instrument, symbol)
        price, qty = round(d.entry_price, inst.digits if inst else 8), d.volume
        fee = qty * price * fee_rate
        cc = state.get_locked(db, "crypto")
        cc["paper_cash"] = round(float(cc["paper_cash"]) - qty * price - fee, 6)
        state.put(db, "crypto", cc)
        p = Position(mode=mode, order_id=o.id, symbol=symbol, side="BUY", volume=qty, open_price=price, stop_loss=sl,
                     take_profit=tp, commission=round(fee, 6), current_price=price)
        db.add(p)
        db.flush()
        db.add(CryptoLink(position_id=p.id, exchange="paper", environment="paper", prot_ids=[], prot_kind="paper", exchange_tp=True))
        o.status, o.fill_price, o.filled_volume, o.sl_verified = "FILLED", price, qty, True
    else:
        x = client(db, private=True, mode="TEST" if mode == "CRYPTO_TEST" else "LIVE", http=http)
        try:
            fill = x.market_buy(symbol, d.volume, o.client_id)
        except Exception as e:
            o.status, o.error = "REJECTED", f"exchange: {e}"[:2000]
            event(db, "crypto", f"{symbol} buy rejected: {e}", "WARNING")
            audit(db, "order.rejected", entity="order", entity_id=o.client_id, details={"error": o.error, "symbol": symbol})
            return None, d.checks + [{"check": "exchange accepted the order", "passed": False, "detail": str(e)}], out
        o.status, o.fill_price, o.filled_volume, o.broker_ticket = "FILLED", fill.avg_price, fill.qty, fill.order_id[:32]
        p = Position(mode=mode, order_id=o.id, symbol=symbol, side="BUY", volume=fill.qty, open_price=fill.avg_price,
                     stop_loss=sl, take_profit=tp, commission=round(fill.fee_quote, 6), current_price=fill.avg_price,
                     broker_ticket=fill.order_id[:32])
        db.add(p)
        db.flush()
        try:
            prot = x.protect(symbol, fill.qty, sl, tp, o.client_id)
            db.add(CryptoLink(position_id=p.id, exchange=c["exchange"], environment="test" if mode == "CRYPTO_TEST" else "live",
                              prot_ids=prot.ids, prot_kind=prot.kind, exchange_tp=prot.exchange_tp, buy_order_id=fill.order_id))
            o.sl_verified = True
        except Exception as e:  # fail closed: never hold coins without a stop at the exchange
            log.error("protection failed for %s: %s — selling", o.client_id, e)
            o.error = f"stop-loss could not be placed ({e}); position sold again"[:2000]
            try:
                sold = x.market_sell(symbol, fill.qty, f"F{o.id}")
                _close_position(db, p, sold.avg_price, "protection_failed", sold.fee_quote)
            except Exception as e2:
                event(db, "crypto", f"URGENT: {symbol} bought without stop-loss and emergency sell failed: {e2}", "ERROR")
                from ..alerts import raise_alert
                raise_alert(db, "PROTECTION_FAILURE", f"URGENT {symbol}: no stop-loss and emergency sell failed",
                            f"Check {c['exchange']} NOW and sell manually. {e2}", severity="critical", dedup=f"prot:{o.client_id}")
                db.add(CryptoLink(position_id=p.id, exchange=c["exchange"], environment=mode, prot_ids=[], prot_kind="none", exchange_tp=False))
    audit(db, "order.filled", entity="order", entity_id=o.client_id,
          details={"mode": mode, "symbol": symbol, "side": "BUY", "volume": o.filled_volume, "price": o.fill_price, "sl": sl, "tp": tp})
    notify(f"🪙 {mode} BUY {o.filled_volume} {symbol} @ {o.fill_price} SL {sl} TP {tp or '-'} risk {d.risk_amount:.2f} USDT",
           state.get(db, "notifications")["notify_orders"])
    return p, d.checks, out


def close(db: Session, p: Position, reason: str, by: str, http=None) -> None:
    if p.status != "OPEN":
        return
    if p.mode == "CRYPTO_PAPER":
        q = db.get(Quote, p.symbol)
        if q is None:
            raise ValueError("no price available")
        _close_position(db, p, q.bid, reason, p.volume * q.bid * float(cfg(db)["fee_pct"]) / 100, by)
        return
    link = db.get(CryptoLink, p.id)
    x = client(db, private=True, mode="TEST" if p.mode == "CRYPTO_TEST" else "LIVE", http=http)
    if link and link.prot_ids:
        x.cancel_protection(p.symbol, link.prot_ids, link.prot_kind)
    try:
        sold = x.market_sell(p.symbol, p.volume, f"C{p.id}-{int(time.time())}")
    except Exception as e:
        if link and link.prot_ids:  # put the stop back
            try:
                prot = x.protect(p.symbol, p.volume, p.stop_loss, p.take_profit, f"R{p.id}")
                link.prot_ids, link.prot_kind, link.exchange_tp = prot.ids, prot.kind, prot.exchange_tp
            except Exception as e2:
                event(db, "crypto", f"URGENT: {p.symbol} close failed and stop could not be restored: {e2}", "ERROR")
        raise ValueError(f"exchange refused the sell: {e}") from e
    _close_position(db, p, sold.avg_price, reason, sold.fee_quote, by)


def modify(db: Session, p: Position, sl: float, tp: float | None, by: str, reason: str, http=None) -> None:
    old = (p.stop_loss, p.take_profit)
    if p.mode != "CRYPTO_PAPER":
        link = db.get(CryptoLink, p.id)
        x = client(db, private=True, mode="TEST" if p.mode == "CRYPTO_TEST" else "LIVE", http=http)
        if link and link.prot_ids:
            x.cancel_protection(p.symbol, link.prot_ids, link.prot_kind)
        try:
            prot = x.protect(p.symbol, p.volume, sl, tp, f"M{p.id}-{int(time.time())}")
        except Exception as e:
            log.error("re-protect failed for position %s: %s — selling", p.id, e)
            sold = x.market_sell(p.symbol, p.volume, f"F{p.id}-{int(time.time())}")
            _close_position(db, p, sold.avg_price, "protection_failed", sold.fee_quote, by)
            raise ValueError(f"the new stop could not be placed ({e}); the position was sold to stay safe") from e
        if link is None:
            link = CryptoLink(position_id=p.id, exchange=cfg(db)["exchange"], environment=p.mode)
            db.add(link)
        link.prot_ids, link.prot_kind, link.exchange_tp = prot.ids, prot.kind, prot.exchange_tp
    p.stop_loss, p.take_profit = sl, tp
    audit(db, "position.sl_tp_modified", user=by, entity="position", entity_id=p.id,
          details={"from": old, "to": (sl, tp), "reason": reason, "mode": p.mode})


def monitor(db: Session, http=None) -> None:
    """Mark-to-market, paper exits, exchange protection checks and app-side take-profits."""
    quotes = md.latest_quotes(db)
    fee_rate = float(cfg(db)["fee_pct"]) / 100
    for mode in CRYPTO_MODES:
        for p in open_positions(db, mode):
            q = quotes.get(p.symbol)
            if q is None:
                continue
            bid = q.bid
            p.current_price = bid
            p.unrealized_pnl = round((bid - p.open_price) * p.volume - (p.commission or 0) - bid * p.volume * fee_rate, 2)
            if mode == "CRYPTO_PAPER":
                if p.stop_loss and bid <= p.stop_loss:
                    _close_position(db, p, bid, "stop_loss", p.volume * bid * fee_rate)
                elif p.take_profit and bid >= p.take_profit:
                    _close_position(db, p, bid, "take_profit", p.volume * bid * fee_rate)
                continue
            link = db.get(CryptoLink, p.id)
            if link is None or not link.prot_ids:
                continue
            try:
                x = client(db, private=True, mode="TEST" if mode == "CRYPTO_TEST" else "LIVE", http=http)
                if p.take_profit and not link.exchange_tp and bid >= p.take_profit:
                    close(db, p, "take_profit", "system", http=http)
                    continue
                if time.time() - _mem["prot_at"].get(p.id, 0) < 15:
                    continue
                _mem["prot_at"][p.id] = time.time()
                s = x.protection_state(p.symbol, link.prot_ids, link.prot_kind)
                if s.get("filled"):
                    _close_position(db, p, s.get("exit_price") or bid, s.get("reason", "stop_loss"),
                                    p.volume * (s.get("exit_price") or bid) * fee_rate)
                elif not s.get("active"):
                    base = ex.Exchange.split(p.symbol)[0]
                    held = x.balances().get(base, 0)
                    if held >= p.volume * 0.98:
                        prot = x.protect(p.symbol, p.volume, p.stop_loss, p.take_profit, f"R{p.id}-{int(time.time())}")
                        link.prot_ids, link.prot_kind, link.exchange_tp = prot.ids, prot.kind, prot.exchange_tp
                        event(db, "crypto", f"{p.symbol}: stop-loss was missing at the exchange and has been placed again", "WARNING")
                    else:
                        _close_position(db, p, bid, "closed_outside_app", 0)
                        event(db, "crypto", f"{p.symbol}: coins no longer on the exchange — position marked closed", "WARNING")
            except Exception as e:
                log.warning("crypto monitor %s: %s", p.symbol, e)


# --------------------------------------------------------------------- signals
def evaluate_signals(db: Session) -> None:
    from .. import news_trading
    c = cfg(db)
    mode = pos_mode(db)
    if not c.get("signals", True):
        return
    scfg = {**state.get(db, "strategy")}
    if c.get("strategy"):
        scfg["name"] = c["strategy"]
    tfs = c.get("signal_timeframes", ["H1"])
    news_on = news_trading.cfg(db).get("breakout")
    ntf = news_trading.news_timeframe(tfs) or news_trading.news_timeframe(c.get("timeframes", []))
    for sym in c["symbols"]:
        for tf in tfs:
            _evaluate_one(db, sym, tf, scfg, mode)
        if news_on and ntf:
            ncfg = news_trading.strategy_cfg(db, scfg, sym, ntf, crypto=True)
            if ncfg["news_events"]:
                _evaluate_one(db, sym, ntf, ncfg, mode)


def _evaluate_one(db: Session, sym: str, tf: str, scfg: dict, mode: str | None) -> None:
    from .. import strategy as strat
    from ..indicators import required_history
    df = md.load_candles(db, sym, tf, limit=required_history(scfg) + 50)
    if len(df) < required_history(scfg) // 2:
        return
    last = df.index[-1].to_pydatetime()
    if not md.is_closed(last, tf):
        df = df.iloc[:-1]
        last = df.index[-1].to_pydatetime()
    if (utcnow() - (last + md.tf_delta(tf))).total_seconds() > md.TF_SECONDS[tf]:
        return
    if db.scalar(select(Signal.id).where(Signal.symbol == sym, Signal.timeframe == tf, Signal.candle_time == last,
                                         Signal.strategy_version == scfg["version"])):
        return
    ind = strat.prepare(df, scfg)
    res = strat.evaluate(ind, len(ind) - 1, scfg)
    sig = Signal(symbol=sym, timeframe=tf, candle_time=last, strategy=scfg["name"], strategy_version=scfg["version"],
                 action=res.action, entry_ref=res.entry_ref, stop_loss=res.stop_loss, take_profit=res.take_profit,
                 features={**res.features, "asset": "crypto"}, reasons=res.reasons, status="NO_TRADE",
                 trading_mode=mode or "CRYPTO_OFF")
    db.add(sig)
    db.flush()
    if res.action == "NONE":
        return
    if res.action == "SELL" or mode is None:
        sig.status = "SIGNAL_ONLY"
        sig.reasons = list(sig.reasons) + [{"check": "spot account cannot sell short" if res.action == "SELL" else "crypto trading OFF",
                                            "passed": False}]
        return
    from .. import assess
    ctx = build_context(db, sym, "BUY", res.stop_loss, res.take_profit, mode)
    d = evaluate(ctx)
    sig.risk_checks = d.checks
    outcome, _a = assess.route(db, sig, d, ctx)
    if outcome != "QUALIFIED":
        return
    controls = state.get(db, "controls")
    from ..automation import announce, auto_ok
    if auto_ok(db, mode, _a):
        p, _checks, _ = place(db, symbol=sym, sl=sig.stop_loss, tp=sig.take_profit, by="auto", source=sig.strategy, signal=sig)
        if p is not None:
            sig.status = "ORDERED"
            assess.lifecycle(db, sig.id, "AUTO_ORDERED", f"{mode} · no approval (auto-trade on)")
            announce(db, sig, p.volume, p.open_price, mode)
            return
    assess.request_approval(db, sig, d, int(controls.get("approval_minutes", 60)))


def tick(db: Session) -> None:
    from .. import trading
    refresh_market(db)
    db.commit()
    c = cfg(db)
    monitor(db)
    db.commit()
    mode = pos_mode(db)
    if mode:
        trading.check_loss_limits(db, mode)
        db.commit()
    evaluate_signals(db)
    db.commit()
