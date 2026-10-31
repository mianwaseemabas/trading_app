"""News-driven automation, two parts — both rule-based at the moment an order is decided:

1. News breakout (a strategy add-on): after a HIGH-impact release (economic calendar) or a HIGH-impact headline,
   wait until the first spike is over (≥ the news-filter 'after' minutes), mark the spike range, and trade only a
   clean candle CLOSE beyond that range in the next 2 hours. Stop at the other side of the range (max 2.5 ATR),
   target 1.5R. No prediction of the news itself — it trades the market's confirmed reaction.
2. News direction filter (needs the AI assistant): Claude tags each high-impact headline for your markets as
   bullish / bearish / neutral per asset. A setup whose direction is contradicted by a strong recent tag is
   rejected; an aligned tag is noted. The AI can only block or annotate — it never creates or sends an order.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..models import NewsEvent, NewsHeadline, utcnow
from .instruments_meta import headline_assets, news_currencies

log = logging.getLogger("news_trading")
NAME = "news_breakout"


def cfg(db: Session) -> dict:
    return state.get(db, "news_auto")


# ------------------------------------------------------------------ 1. news breakout
def event_times(db: Session, symbol: str, crypto: bool = False, hours: int = 3) -> list[dict]:
    now = utcnow()
    since = now - timedelta(hours=hours)
    out = []
    ccys = ["USD"] if crypto else news_currencies(symbol)
    for e in db.scalars(select(NewsEvent).where(NewsEvent.impact == "High", NewsEvent.currency.in_(ccys),
                                                NewsEvent.time >= since, NewsEvent.time <= now)).all():
        out.append({"t": int(e.time.timestamp()), "what": f"{e.currency} {e.title}"})
    if crypto:
        assets = {symbol.replace("USDT", "").replace("USDC", ""), "CRYPTO"}
    else:
        assets = headline_assets(symbol)
        if not (len(symbol) == 6 and symbol.isalpha()):
            assets -= {"USD"}  # gold/oil/indices: only their own headlines
    for h in db.scalars(select(NewsHeadline).where(NewsHeadline.impact == "High", NewsHeadline.published >= since,
                                                   NewsHeadline.published <= now)).all():
        if set(h.assets) & assets:
            out.append({"t": int(h.published.timestamp()), "what": f"{h.source}: {h.title[:90]}"})
    return sorted(out, key=lambda x: -x["t"])


def evaluate(df: pd.DataFrame, i: int, c: dict):
    """(action, entry, sl, tp, features, reasons) on closed candle i. c needs news_events, tf_seconds, wait_min."""
    reasons = []
    tf = int(c.get("tf_seconds", 900))
    wait = int(c.get("wait_min", 15)) * 60
    window = int(c.get("window_min", 120)) * 60
    t_close = int(df.index[i].timestamp()) + tf
    atr = float(df["atr"].iloc[i]) if "atr" in df and df["atr"].iloc[i] == df["atr"].iloc[i] else None
    close = float(df["close"].iloc[i])
    ev = next((e for e in c.get("news_events", []) if e["t"] + wait <= t_close <= e["t"] + window), None)
    reasons.append({"check": "high-impact news released 15 min – 2 h ago", "passed": bool(ev), "detail": ev["what"] if ev else "none"})
    if not ev or not atr:
        return "NONE", None, None, None, {}, reasons
    times = [int(t.timestamp()) for t in df.index]
    in_range = [k for k in range(i + 1) if times[k] + tf > ev["t"] and times[k] < ev["t"] + wait]
    if not in_range:
        return "NONE", None, None, None, {}, reasons
    hi = float(df["high"].iloc[in_range].max())
    lo = float(df["low"].iloc[in_range].min())
    height = hi - lo
    ok_h = 0.3 * atr <= height <= 4 * atr
    reasons.append({"check": "news spike range sensible (0.3–4 ATR)", "passed": ok_h, "detail": f"range {height:.5g}, ATR {atr:.5g}"})
    if not ok_h or i <= in_range[-1]:
        return "NONE", None, None, None, {}, reasons
    prev = df["close"].iloc[in_range[-1] + 1:i]
    first_up = close > hi + 0.1 * atr and not (prev > hi).any()
    first_dn = close < lo - 0.1 * atr and not (prev < lo).any()
    reasons.append({"check": "first candle close beyond the news range", "passed": first_up or first_dn,
                    "detail": f"close {close:.5g} vs range {lo:.5g}–{hi:.5g}"})
    if not (first_up or first_dn):
        return "NONE", None, None, None, {}, reasons
    if first_up:
        sl = max(lo - 0.1 * atr, close - 2.5 * atr)
        tp = close + 1.5 * (close - sl)
        act = "BUY"
    else:
        sl = min(hi + 0.1 * atr, close + 2.5 * atr)
        tp = close - 1.5 * (sl - close)
        act = "SELL"
    feats = {"news": ev["what"], "news_time": ev["t"], "range_high": hi, "range_low": lo, "atr": atr, "close": close}
    return act, close, sl, tp, feats, reasons


def strategy_cfg(db: Session, base: dict, symbol: str, tf: str, crypto: bool = False) -> dict:
    from . import market_data as md
    controls = state.get(db, "controls")
    c = cfg(db)
    return {**base, "name": NAME, "version": f"{base.get('version', 'v')}-news",
            "news_events": event_times(db, symbol, crypto), "tf_seconds": md.TF_SECONDS[tf],
            "wait_min": max(int(c.get("wait_min", 15)), int(controls.get("news_after_min", 15))), "window_min": int(c.get("window_min", 120))}


def news_timeframe(tfs: list[str]) -> str | None:
    for tf in ("M15", "M30", "H1"):
        if tf in tfs:
            return tf
    return None


# ------------------------------------------------------------------ 2. AI direction tags
PROMPT = """You tag market-moving headlines for a trading app. For each headline give the likely short-term direction
for each listed asset: "bullish", "bearish" or "neutral", and a confidence 0-1. Be conservative: use neutral and low
confidence when unclear. Assets: currencies (USD, EUR, GBP, JPY, CHF, AUD, CAD), GOLD, OIL, INDICES, BTC, ETH, SOL, CRYPTO.
Reply ONLY with JSON: {"items":[{"id":<id>,"effects":{"USD":"bullish"},"confidence":0.0,"why":"<max 12 words>"}]}"""


def classify(db: Session, call=None) -> int:
    """Tag new high-impact headlines about your markets (worker, every few minutes; needs the AI assistant)."""
    c = cfg(db)
    ai = state.get(db, "ai")
    if not c.get("ai_filter") or not ai.get("enabled") or not ai.get("api_key_enc"):
        return 0
    from .headlines import watched_assets
    tags = state.get(db, "news_ai") or {}
    watched = watched_assets(db)
    rows = [h for h in db.scalars(select(NewsHeadline).where(NewsHeadline.impact == "High", NewsHeadline.published >= utcnow() - timedelta(hours=8))
                                  .order_by(NewsHeadline.published.desc()).limit(40)).all()
            if str(h.id) not in tags and set(h.assets) & watched][:10]
    if not rows:
        return 0
    if call is None:
        from ..routers.ai import _call
        from ..secretbox import decrypt

        def call(system, prompt):
            return _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), system, [{"role": "user", "content": prompt}])
    text = call(PROMPT, json.dumps([{"id": h.id, "title": h.title, "summary": h.summary[:300], "assets": h.assets} for h in rows]))
    try:
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        log.info("news AI reply not JSON: %s", text[:200])
        return 0
    by_id = {h.id: h for h in rows}
    n = 0
    for it in data.get("items", []):
        h = by_id.get(it.get("id"))
        if not h:
            continue
        eff = {k.upper(): v for k, v in (it.get("effects") or {}).items() if v in ("bullish", "bearish", "neutral")}
        tags[str(h.id)] = {"t": int(h.published.timestamp()), "effects": eff, "confidence": float(it.get("confidence") or 0),
                           "why": str(it.get("why", ""))[:120], "title": h.title[:140]}
        n += 1
    cutoff = (utcnow() - timedelta(hours=24)).timestamp()
    state.put(db, "news_ai", {k: v for k, v in tags.items() if v["t"] >= cutoff})
    return n


def bias(db: Session, symbol: str, side: str, hours: int = 4) -> dict | None:
    """Does recent tagged news support or contradict a BUY/SELL on this market? None = no strong tag."""
    c = cfg(db)
    if not c.get("ai_filter"):
        return None
    tags = state.get(db, "news_ai") or {}
    since = (utcnow() - timedelta(hours=hours)).timestamp()
    s = symbol.upper()
    if s.endswith(("USDT", "USDC")):
        legs = {s[:-4]: 1, "CRYPTO": 1}
    elif len(s) == 6 and s.isalpha():
        legs = {s[:3]: 1, s[3:]: -1}  # buying EURUSD = EUR up / USD down
    else:
        legs = {a: 1 for a in headline_assets(s) - {"USD"}}
        legs["USD"] = -1 if s in ("XAUUSD", "GOLD", "XAGUSD", "SILVER") else 0
    want = 1 if side == "BUY" else -1
    score, why = 0.0, []
    for t in tags.values():
        if t["t"] < since or t["confidence"] < float(c.get("min_confidence", 0.7)):
            continue
        for asset, d in t["effects"].items():
            sign = legs.get(asset)
            if not sign or d == "neutral":
                continue
            v = (1 if d == "bullish" else -1) * sign * want * t["confidence"]
            score += v
            why.append(f"{'supports' if v > 0 else 'against'}: {asset} {d} — {t['title'][:70]}")
    if not why:
        return None
    return {"opposing": score < 0, "aligned": score > 0, "score": round(score, 2), "detail": "; ".join(why[:3])}
