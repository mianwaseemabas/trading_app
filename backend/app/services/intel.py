"""Trading intelligence: watchlist, sentiment, risk score, insights, achievements.

Everything is computed from stored prices with fixed, explainable rules and every
output carries its evidence. Nothing here predicts prices; the AI brief (optional)
only explains these numbers in words.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from datetime import timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..models import Instrument, Position, Quote, SignalApproval, TradeNote, utcnow
from . import market_data as md
from . import report, smc
from .indicators import add_indicators

FX = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "SGD", "HKD", "MXN", "ZAR", "TRY", "CNH", "PLN"}
INDICES = {"US500", "US100", "US30", "US2000", "DE40", "UK100", "FR40", "EU50", "J225", "HK50", "AU200", "CN50", "SPX500", "NAS100"}
COMMODITIES = {"GOLD", "SILVER", "OIL_CRUDE", "OIL_BRENT", "NATURALGAS", "COPPER", "XAUUSD", "XAGUSD", "PLATINUM"}
CRYPTO_CFD = {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "ADAUSD", "DOGEUSD", "BNBUSD"}
_cache: dict = {}


def asset_class(symbol: str, crypto_syms: set[str]) -> str:
    if symbol in crypto_syms:
        return "crypto"
    if symbol in CRYPTO_CFD:
        return "crypto CFD"
    if symbol in INDICES:
        return "index"
    if symbol in COMMODITIES:
        return "commodity"
    if len(symbol) == 6 and symbol[:3] in FX and symbol[3:] in FX:
        return "forex"
    return "stock"


def digits_of(db: Session, symbol: str) -> int:
    inst = db.get(Instrument, symbol)
    if inst and inst.digits is not None:
        return int(inst.digits)
    return 3 if symbol.endswith("JPY") else 5


def _change_24h(db: Session, symbol: str, price: float | None) -> tuple[float | None, list[float]]:
    df = md.load_candles(db, symbol, "H1", limit=25)
    closes = [float(x) for x in df["close"].tolist()] if len(df) else []
    if price and len(closes) >= 24:
        ref = closes[-24]
        return round((price - ref) / ref * 100, 3), closes[-24:]
    return None, closes


def watchlist(db: Session) -> list[dict]:
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    cst = state.get(db, "crypto_status")
    fav = set(state.get(db, "favorites") or [])
    crypto_syms = set(cc.get("symbols", []))
    out = []
    for sym in list(dict.fromkeys(strat["symbols"] + cc.get("symbols", []))):
        q = db.get(Quote, sym)
        bid = q.bid if q else None
        chg, spark = _change_24h(db, sym, bid)
        if sym in crypto_syms and (cst.get("changes") or {}).get(sym) is not None:
            chg = round(float(cst["changes"][sym]), 3)
        out.append({"symbol": sym, "asset": asset_class(sym, crypto_syms), "venue": cc["exchange"] if sym in crypto_syms else "broker",
                    "bid": bid, "ask": q.ask if q else None, "age_s": (utcnow() - q.collected_at).total_seconds() if q else None,
                    "change_24h": chg, "digits": digits_of(db, sym), "favorite": sym in fav, "spark": spark[-24:]})
    return out


# ------------------------------------------------------------------ sentiment
def crypto_fear_greed() -> dict | None:
    """Crypto Fear & Greed index from alternative.me (cached 1 h; optional)."""
    c = _cache.get("fng")
    if c and time.time() - c[0] < 3600:
        return c[1]
    try:
        with urllib.request.urlopen("https://api.alternative.me/fng/?limit=1", timeout=5) as r:
            d = json.loads(r.read())["data"][0]
        val = {"value": int(d["value"]), "label": d["value_classification"], "source": "alternative.me (crypto market)"}
    except Exception:
        val = None
    _cache["fng"] = (time.time(), val)
    return val


def _label(v: float) -> str:
    return "Extreme fear" if v < 25 else "Fear" if v < 45 else "Neutral" if v <= 55 else "Greed" if v <= 75 else "Extreme greed"


def sentiment(db: Session, symbols: list[str]) -> dict:
    cfg = state.get(db, "strategy")
    rsis, ups, downs = [], 0, 0
    for sym in symbols:
        df = md.load_candles(db, sym, "H1", limit=max(300, cfg["ema_slow"] + 50))
        if len(df) < 60:
            continue
        r = add_indicators(df, cfg).iloc[-1]
        if not math.isnan(r["rsi"]):
            rsis.append(float(r["rsi"]))
        if not math.isnan(r["ema_slow"]):
            if r["close"] > r["ema_slow"] and r["ema_fast"] > r["ema_slow"]:
                ups += 1
            elif r["close"] < r["ema_slow"] and r["ema_fast"] < r["ema_slow"]:
                downs += 1
    gauge = round(float(np.mean(rsis)), 0) if rsis else None
    total = ups + downs
    return {
        "momentum": {"value": gauge, "label": _label(gauge) if gauge is not None else "no data",
                     "how": "average H1 RSI of your watchlist (50 = neutral)"},
        "breadth": {"up": ups, "down": downs, "up_pct": round(ups / total * 100) if total else None,
                    "how": "markets in an H1 uptrend vs downtrend (EMA 50/200)"},
        "bias": "Bullish" if total and ups / total >= 0.6 else "Bearish" if total and downs / total >= 0.6 else "Mixed",
        "crypto_fear_greed": crypto_fear_greed(),
    }


# ----------------------------------------------------------------- risk score
def risk_score(db: Session, symbol: str) -> dict:
    cfg = state.get(db, "strategy")
    df = md.load_candles(db, symbol, "H1", limit=400)
    q = db.get(Quote, symbol)
    parts, score = [], 0.0
    if len(df) >= 60:
        ind = add_indicators(df, cfg)
        atr = ind["atr"].dropna()
        cur = float(atr.iloc[-1])
        pct = float((atr < cur).mean() * 100)
        score += 0.5 * pct
        parts.append({"factor": "volatility", "value": round(pct), "detail": f"H1 ATR is higher than {pct:.0f}% of the last {len(atr)} hours"})
        if q and cur > 0:
            ratio = (q.ask - q.bid) / cur * 100
            s = min(100, ratio * 5)
            score += 0.2 * s
            parts.append({"factor": "spread", "value": round(s), "detail": f"spread = {ratio:.1f}% of an average hourly move"})
        vol_pct = pct
    else:
        vol_pct = None
        parts.append({"factor": "volatility", "value": None, "detail": "not enough history"})
    news = report.upcoming_news(db, symbol, 2, ("High",))
    n = 100 if any(abs(x["minutes"]) <= 60 for x in news) else 50 if news else 0
    score += 0.2 * n
    parts.append({"factor": "news", "value": n, "detail": f"high-impact news: {news[0]['title']} in {news[0]['minutes']} min" if news else "no high-impact news in 2 h"})
    ses = report.sessions_now()
    crypto = symbol in set(state.get(db, "crypto").get("symbols", []))
    liq = 0 if crypto else (20 if ses["liquidity"].startswith("high") else 50 if ses["liquidity"].startswith("medium") else 100)
    score += 0.1 * liq
    parts.append({"factor": "liquidity", "value": liq, "detail": "24/7 crypto market" if crypto else f"session liquidity: {ses['liquidity']}"})
    score = round(min(100, score))
    return {"symbol": symbol, "score": score, "level": "Low" if score < 35 else "Medium" if score < 65 else "High",
            "volatility_pct": round(vol_pct) if vol_pct is not None else None,
            "liquidity": "High" if liq <= 20 else "Medium" if liq <= 50 else "Low", "parts": parts,
            "spread": round(q.ask - q.bid, 8) if q else None}


# -------------------------------------------------------------------- insights
def insights(db: Session, symbol: str) -> dict:
    cfg = state.get(db, "strategy")
    ctx = {tf: report.tf_context(db, symbol, tf, cfg) for tf in ("H4", "H1", "M15")}
    items = []
    trends = {tf: c["trend"] for tf, c in ctx.items() if c}
    votes = []
    for tf, t in trends.items():
        votes.append(1 if t == "up" else -1 if t == "down" else 0)
    h1 = ctx.get("H1")
    if h1:
        votes.append(1 if h1["structure"] == "bullish" else -1 if h1["structure"] == "bearish" else 0)
        if h1["rsi"] is not None:
            votes.append(1 if h1["rsi"] > 55 else -1 if h1["rsi"] < 45 else 0)
    bull, bear = votes.count(1), votes.count(-1)
    direction = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"
    agree = round(max(bull, bear) / len(votes) * 100) if votes else 0
    t0 = next(iter(trends.values()), None)
    if len(trends) >= 2 and len(set(trends.values())) == 1 and t0 in ("up", "down"):
        items.append({"tone": "ok" if t0 == "up" else "bad", "text": f"{'/'.join(trends)} trends all {t0} on {symbol}"})
    df = md.load_candles(db, symbol, "H1", limit=300)
    if len(df) > 60:
        ind = add_indicators(df, cfg)
        ev = smc.analyze(ind, cfg)["events"]
        recent = [e for e in ev if e["i"] >= len(ind) - 12 and e["kind"] in ("bos", "choch", "sweep_bsl", "sweep_ssl")]
        for e in recent[-3:]:
            name = {"bos": "Break of structure", "choch": "Change of character", "sweep_bsl": "Buy-side liquidity swept",
                    "sweep_ssl": "Sell-side liquidity swept"}[e["kind"]]
            items.append({"tone": "ok" if e["dir"] == "bull" else "bad",
                          "text": f"{name} ({'bullish' if e['dir'] == 'bull' else 'bearish'}) at {e.get('level', 0):.{digits_of(db, symbol)}f}, "
                                  f"{len(ind) - 1 - e['i']} h ago"})
        atr = ind["atr"].dropna()
        if len(atr) > 50 and float((atr < atr.iloc[-1]).mean()) > 0.85:
            items.insert(0, {"tone": "warn", "text": "High volatility detected — bigger moves and wider stops than usual"})
        if h1 and h1["rsi"] is not None and (h1["rsi"] > 70 or h1["rsi"] < 30):
            items.append({"tone": "warn", "text": f"H1 RSI {h1['rsi']:.0f}: {'overbought' if h1['rsi'] > 70 else 'oversold'} — chasing is risky"})
    for n in report.upcoming_news(db, symbol, 3, ("High",))[:2]:
        items.append({"tone": "warn", "text": f"High-impact news {n['currency']}: {n['title']} in {n['minutes']} min"})
    waiting = db.scalar(select(func.count()).select_from(SignalApproval).where(SignalApproval.status == "WAITING")) or 0
    if waiting:
        items.append({"tone": "info", "text": f"{waiting} setup(s) waiting for your approval", "link": "#/setups"})
    if not items:
        items.append({"tone": "muted", "text": "No notable condition detected right now — patience is a position too"})
    return {"symbol": symbol, "direction": direction, "agreement": agree, "votes": {"bullish": bull, "bearish": bear, "total": len(votes)},
            "how": "agreement = share of 5 factors (H4/H1/M15 trend, H1 SMC structure, H1 RSI) pointing the same way",
            "items": items[:6], "timeframes": ctx}


# ----------------------------------------------------------------- achievements
def achievements(db: Session) -> dict:
    closed = db.scalars(select(Position).where(Position.status == "CLOSED", Position.realized_pnl.is_not(None))).all()
    n = len(closed)
    noted = db.scalar(select(func.count()).select_from(TradeNote).where(TradeNote.note != "")) or 0
    planned = sum(1 for p in closed if p.close_reason in ("stop_loss", "take_profit"))
    reviewed = db.scalar(select(func.count()).select_from(SignalApproval).where(SignalApproval.status.in_(["APPROVED", "DECLINED"]),
                                                                           SignalApproval.note != "")) or 0
    exp = (sum(p.realized_pnl for p in closed) / n) if n else 0
    items = [
        {"key": "first", "label": "First trade", "done": n >= 1, "progress": min(1, n), "goal": 1, "how": "close your first trade"},
        {"key": "ten", "label": "10 trades", "done": n >= 10, "progress": min(n, 10), "goal": 10, "how": "close 10 trades"},
        {"key": "journal", "label": "Journal keeper", "done": noted >= 5, "progress": min(noted, 5), "goal": 5, "how": "write notes on 5 trades"},
        {"key": "plan", "label": "Followed the plan", "done": planned >= 20, "progress": min(planned, 20), "goal": 20,
         "how": "20 trades ended by their stop-loss or take-profit (no panic exits)"},
        {"key": "review", "label": "Thoughtful reviewer", "done": reviewed >= 10, "progress": min(reviewed, 10), "goal": 10,
         "how": "approve or decline 10 setups with a written reason"},
        {"key": "master", "label": "Master", "done": n >= 50 and exp > 0, "progress": min(n, 50), "goal": 50,
         "how": "positive average result over 50+ trades"},
    ]
    done = sum(1 for i in items if i["done"])
    nxt = next((i for i in items if not i["done"]), None)
    return {"items": items, "level": done + 1, "progress_pct": round((nxt["progress"] / nxt["goal"]) * 100) if nxt else 100,
            "next": nxt["how"] if nxt else "all done"}


# --------------------------------------------------------------------- account
def account_bar(db: Session, venue: str) -> dict:
    from . import trading
    if venue == "crypto":
        from .crypto import desk
        mode = desk.pos_mode(db) or "CRYPTO_PAPER"
        acct, meta = desk.account(db, mode)
        positions = desk.open_positions(db, mode)
        mode_label = {"CRYPTO_PAPER": "Crypto practice", "CRYPTO_TEST": "Crypto testnet", "CRYPTO_LIVE": "Crypto LIVE"}[mode]
        name = state.get(db, "crypto")["exchange"]
        margin = meta.get("in_positions", meta.get("in_coins", 0))
    else:
        tr = state.get(db, "trading")
        mode = tr["mode"] if tr["mode"] in trading.ORDER_MODES else "PAPER"
        acct, meta = trading.account_state(db, mode)
        positions = trading.open_positions(db, mode)
        mode_label = {"PAPER": "Practice", "MT5_DEMO": "Broker demo", "MT5_LIVE": "Broker LIVE"}[mode]
        name = state.get(db, "brokers")["active"] if mode != "PAPER" else "paper"
        margin = meta.get("margin")
    if acct is None:
        return {"mode": mode, "mode_label": mode_label, "venue": name, "error": meta.get("error")}
    open_pl = round(sum(p.unrealized_pnl or 0 for p in positions), 2)
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    realized = db.scalar(select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(
        Position.mode == mode, Position.status == "CLOSED", Position.closed_at >= day_start)) or 0
    anchor = state.get(db, "anchors").get(mode, {})
    base = anchor.get("day_equity") or acct.equity or 1
    today = round(float(realized) + open_pl, 2)
    return {"mode": mode, "mode_label": mode_label, "venue": name, "currency": acct.currency, "balance": round(acct.balance, 2),
            "equity": round(acct.equity, 2), "free_margin": round(acct.free_margin, 2), "margin": round(margin or 0, 2),
            "margin_level": round(acct.equity / margin * 100, 1) if margin else None, "leverage": acct.leverage,
            "open_pl": open_pl, "open_positions": len(positions), "today_pl": today, "today_roi": round(today / base * 100, 2) if base else None}
