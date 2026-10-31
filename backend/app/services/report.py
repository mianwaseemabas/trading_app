"""Market overview, multi-timeframe context and setup reports.

Everything here is computed from stored candles and quotes with fixed rules, so
the same data always gives the same report. Text separates what was OBSERVED
(measured from data) from INTERPRETATION, and always lists reasons AGAINST.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..models import NewsEvent, Signal, utcnow
from . import market_data as md
from . import smc
from .indicators import add_indicators

# Approximate session hours in UTC (they shift by one hour with daylight-saving time).
SESSIONS = [("Sydney", 21, 6), ("Tokyo", 0, 9), ("London", 7, 16), ("New York", 12, 21)]


def _in(h: int, a: int, b: int) -> bool:
    return a <= h < b if a < b else (h >= a or h < b)


def sessions_now(now: datetime | None = None) -> dict:
    now = now or utcnow()
    h = now.hour
    open_ = [n for n, a, b in SESSIONS if _in(h, a, b)]
    weekend = now.weekday() == 5 or (now.weekday() == 6 and h < 21) or (now.weekday() == 4 and h >= 21)
    liquidity = "closed (weekend)" if weekend else ("high — London/New York overlap" if {"London", "New York"} <= set(open_)
                                                      else "medium" if ("London" in open_ or "New York" in open_) else "low (Asian hours)")
    return {"utc_time": now.isoformat(), "sessions": [{"name": n, "open": n in open_ and not weekend, "utc": f"{a:02d}:00–{b:02d}:00"}
                                                       for n, a, b in SESSIONS], "liquidity": liquidity, "weekend": weekend}


def session_of(t: datetime) -> str:
    h = t.hour
    if 12 <= h < 16:
        return "London/NY overlap"
    if 7 <= h < 12:
        return "London"
    if 16 <= h < 21:
        return "New York"
    if 0 <= h < 7:
        return "Asia"
    return "Rollover (21–24 UTC)"


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def candles_tf(db: Session, symbol: str, tf: str, limit: int = 500) -> pd.DataFrame:
    """Stored candles for tf; H4/D1 are built from H1 when not stored."""
    df = md.load_candles(db, symbol, tf, limit=limit)
    if len(df) >= 50 or tf not in ("H4", "D1"):
        return df
    h1 = md.load_candles(db, symbol, "H1", limit=limit * (4 if tf == "H4" else 24))
    if h1.empty:
        return df
    rule = "4h" if tf == "H4" else "1D"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    out = h1[list(agg)].resample(rule, origin="epoch", label="left", closed="left").agg(agg).dropna()
    # drop the still-forming last bucket
    if len(out) and out.index[-1] + md.tf_delta(tf) > utcnow():
        out = out.iloc[:-1]
    return out


def tf_context(db: Session, symbol: str, tf: str, cfg: dict) -> dict | None:
    df = candles_tf(db, symbol, tf, limit=max(400, int(cfg.get("ema_slow", 200)) * 2))
    if len(df) < 60:
        return None
    ind = add_indicators(df, cfg)
    r = ind.iloc[-1]
    c, ef, es, rsi, atr = (_f(r[k]) for k in ("close", "ema_fast", "ema_slow", "rsi", "atr"))
    trend = "unknown"
    if None not in (c, ef, es):
        trend = "up" if c > es and ef > es else "down" if c < es and ef < es else "mixed"
    st = smc.analyze(ind.tail(300), cfg)["state"]
    return {"tf": tf, "close": c, "trend": trend, "ema_fast": ef, "ema_slow": es, "rsi": rsi, "atr": atr,
            "atr_pct": round(atr / c * 100, 3) if atr and c else None, "structure": st["trend"],
            "bsl": st["bsl"], "ssl": st["ssl"], "candle_time": ind.index[-1].isoformat()}


def upcoming_news(db: Session, symbol: str, hours: int = 24, impacts=("High", "Medium")) -> list[dict]:
    from .instruments_meta import news_currencies
    ccys = news_currencies(symbol)
    now = utcnow()
    rows = db.scalars(select(NewsEvent).where(NewsEvent.currency.in_(ccys), NewsEvent.impact.in_(list(impacts)),
                                              NewsEvent.time >= now - timedelta(minutes=30),
                                              NewsEvent.time <= now + timedelta(hours=hours)).order_by(NewsEvent.time)).all()
    return [{"time": n.time.isoformat(), "currency": n.currency, "impact": n.impact, "title": n.title,
             "minutes": round((n.time - now).total_seconds() / 60)} for n in rows]


def overview(db: Session) -> dict:
    cfg = state.get(db, "strategy")
    quotes = md.latest_quotes(db)
    out = []
    for sym in cfg["symbols"]:
        q = quotes.get(sym)
        h1 = md.load_candles(db, sym, "H1", limit=30)
        change = None
        if len(h1) >= 25 and q:
            ref = float(h1["close"].iloc[-25])
            change = round(((q.bid - ref) / ref) * 100, 3)
        ctx = {tf: tf_context(db, sym, tf, cfg) for tf in ("H4", "H1", "M15")}
        trends = [c["trend"] for c in ctx.values() if c]
        align = "aligned up" if trends and all(t == "up" for t in trends) else "aligned down" if trends and all(t == "down" for t in trends) else "mixed"
        out.append({
            "symbol": sym, "bid": q.bid if q else None, "ask": q.ask if q else None,
            "spread_points": round((q.ask - q.bid) / md.ensure_instrument(db, sym).point, 1) if q else None,
            "change_24h_pct": change, "context": ctx, "alignment": align, "news": upcoming_news(db, sym, 24, ("High",))[:3],
        })
    return {"sessions": sessions_now(), "symbols": out, "strategy": cfg["name"]}


def setup_report(db: Session, sig: Signal) -> dict:
    """Structured setup review for a strategy signal (for the human decision)."""
    from . import trading
    from .risk_engine import evaluate
    cfg = state.get(db, "strategy")
    side = sig.action
    digits = 3 if sig.symbol.endswith("JPY") else 5
    pip = 0.01 if sig.symbol.endswith("JPY") else 0.0001
    entry, sl, tp = sig.entry_ref, sig.stop_loss, sig.take_profit
    stop = abs(entry - sl) if entry and sl else None
    ctx_tf = {tf: tf_context(db, sig.symbol, tf, cfg) for tf in ("H4", "H1", "M15")}
    want = "up" if side == "BUY" else "down"

    # Current risk check at today's price (what would happen if approved now)
    risk = None
    try:
        if (sig.trading_mode or "").startswith("CRYPTO_"):
            from .crypto import desk
            rc = desk.build_context(db, sig.symbol, side, sl, tp, desk.pos_mode(db) or "CRYPTO_PAPER")
        else:
            rc = trading.build_context(db, symbol=sig.symbol, side=side, tf=sig.timeframe, sl=sl, tp=tp,
                                       mode=state.get(db, "trading")["mode"] if state.get(db, "trading")["mode"] in trading.ORDER_MODES else "PAPER")
        d = evaluate(rc)
        risk = {"approved": d.approved, "volume": d.volume, "risk_amount": d.risk_amount, "risk_budget": d.risk_budget,
                "entry_now": d.entry_price, "failed": [c for c in d.checks if not c["passed"]], **trading.potential_outcomes(d, rc)}
        db.rollback()
    except Exception as e:  # report must still render
        db.rollback()
        risk = {"approved": False, "error": str(e)}

    for_ = [c["check"] + (f" ({c['detail']})" if c.get("detail") else "") for c in (sig.reasons or [])
            if isinstance(c, dict) and c.get("passed") and c.get("side", side) == side]
    against = []
    for tf, c in ctx_tf.items():
        if c and c["trend"] not in (want, "unknown"):
            against.append(f"{tf} trend is {c['trend']} (EMA {cfg['ema_fast']}/{cfg['ema_slow']}), not {want}")
    news = upcoming_news(db, sig.symbol, 6)
    for n in news:
        if n["impact"] == "High":
            against.append(f"High-impact news {n['currency']} '{n['title']}' in {n['minutes']} min")
    ses = sessions_now()
    if ses["liquidity"].startswith("low"):
        against.append("Asian hours: lower liquidity for EUR/GBP pairs; wider spreads possible")
    if risk and risk.get("entry_now") and entry and stop:
        moved = (risk["entry_now"] - entry) if side == "BUY" else (entry - risk["entry_now"])
        if moved > 0.5 * stop:
            against.append(f"Price already moved {moved / pip:.1f} pips in the trade direction since the signal (worse entry, lower reward)")
    h1 = ctx_tf.get("H1") or {}
    liq = h1.get("bsl" if side == "BUY" else "ssl") or []
    blocking = [lv for lv in liq if tp and ((side == "BUY" and entry < lv < tp) or (side == "SELL" and tp < lv < entry))]
    if blocking:
        against.append(f"Untaken {'buy' if side == 'BUY' else 'sell'}-side liquidity at {', '.join(f'{x:.{digits}f}' for x in blocking)} lies before the target — price may react there")

    smc_recent = []
    df = md.load_candles(db, sig.symbol, sig.timeframe, limit=300)
    if len(df) > 60:
        an = smc.analyze(add_indicators(df, cfg), cfg)
        smc_recent = [{"time": e["time"].isoformat(), "kind": e["kind"], "dir": e["dir"],
                       "level": e.get("level") or e.get("price"), "zone": [e.get("zone_low"), e.get("zone_high")] if e["kind"] == "fvg" else None}
                      for e in an["events"] if e["kind"] in ("sweep_bsl", "sweep_ssl", "bos", "choch", "fvg")][-10:]

    targets = []
    if stop:
        for r in (1, 2, 3):
            px = entry + r * stop if side == "BUY" else entry - r * stop
            targets.append({"r": r, "price": round(px, digits)})
    hypothesis = (f"{'Buy' if side == 'BUY' else 'Sell'} {sig.symbol} near {entry:.{digits}f}: the {sig.strategy} rules completed on the "
                  f"{sig.timeframe} candle of {sig.candle_time:%d %b %H:%M} UTC. The idea is wrong if price reaches "
                  f"{sl:.{digits}f} ({stop / pip:.1f} pips away)." if entry and sl and stop else "")
    return {
        "signal": {"id": sig.id, "symbol": sig.symbol, "timeframe": sig.timeframe, "side": side, "strategy": sig.strategy,
                   "version": sig.strategy_version, "candle_time": sig.candle_time.isoformat(), "status": sig.status},
        "hypothesis": hypothesis,
        "observed": {"entry_ref": entry, "stop_loss": sl, "take_profit": tp, "stop_pips": round(stop / pip, 1) if stop else None,
                     "atr": (sig.features or {}).get("atr"), "rsi": (sig.features or {}).get("rsi"), "smc": (sig.features or {}).get("smc")},
        "invalidation": f"A close beyond {sl:.{digits}f} invalidates the setup (stop-loss)." if sl else "",
        "targets": targets,
        "timeframes": ctx_tf,
        "reasons_for": for_,
        "reasons_against": against,
        "risk_now": risk,
        "news": news,
        "session": ses,
        "smc_recent": smc_recent,
        "uncertainty": ("These rules are research hypotheses. Liquidity sweeps, order blocks and FVGs describe price patterns; "
                        "they do not prove what banks or institutions are doing, and a past pattern does not guarantee the next move."),
    }
