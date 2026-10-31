"""Chart candles for any timeframe from 5 seconds to 1 day.

Order of sources (first that has enough data wins):
1. stored candles (broker / exchange / simulator),
2. crypto: the exchange's public candles fetched live (1s, 1m, 5m, 30m…),
3. a lower stored timeframe resampled (M30 from M15, M5 from M1),
4. the app's own recorded price updates (ticks) resampled — seconds charts for forex come from here.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import state
from ..models import Tick, utcnow
from . import market_data as md

log = logging.getLogger("chart")

CHART_TFS = {"S5": 5, "S15": 15, "S30": 30, "M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
LOWER = {"M30": "M15", "M5": "M1", "H1": "M15", "M15": "M5", "M1": None}
TICK_KEEP_HOURS = 6
_cache: dict = {}
_last_tick: dict = {}


def record_tick(db: Session, symbol: str, bid: float, ask: float, at=None) -> None:
    """Store a price update (at most one per symbol per second)."""
    at = at or utcnow()
    key = (symbol, int(at.timestamp()))
    if _last_tick.get(symbol) == key[1] or not bid or not ask:
        return
    _last_tick[symbol] = key[1]
    db.add(Tick(symbol=symbol, time=at, bid=float(bid), ask=float(ask)))


def prune_ticks(db: Session) -> None:
    db.execute(delete(Tick).where(Tick.time < utcnow() - timedelta(hours=TICK_KEEP_HOURS)))


def _resample(df: pd.DataFrame, sec: int) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "tick_volume" in df:
        agg["tick_volume"] = "sum"
    return df[list(agg)].resample(f"{sec}s", origin="epoch", label="left", closed="left").agg(agg).dropna(subset=["open"])


def from_ticks(db: Session, symbol: str, sec: int, limit: int) -> pd.DataFrame:
    since = utcnow() - timedelta(seconds=sec * (limit + 2))
    rows = db.execute(select(Tick.time, Tick.bid).where(Tick.symbol == symbol, Tick.time >= since).order_by(Tick.time)).all()
    if not rows:
        return pd.DataFrame()
    s = pd.Series([r.bid for r in rows], index=pd.to_datetime([r.time for r in rows], utc=True))
    o = s.resample(f"{sec}s", origin="epoch", label="left", closed="left").ohlc().dropna()
    o["tick_volume"] = s.resample(f"{sec}s", origin="epoch", label="left", closed="left").count()
    return o.tail(limit)


def _exchange(db: Session, symbol: str, tf: str, limit: int) -> pd.DataFrame:
    from .crypto import desk
    from .crypto import exchanges as ex
    sec = CHART_TFS[tf]
    key = (symbol, tf, limit)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < (2 if sec < 60 else 10):
        return hit[1]
    x = desk.client(db)
    if sec < 60:
        if ex.TF.get("S1", (None,))[0] is None or x.name != "binance":
            return pd.DataFrame()
        rows = x.klines(symbol, "S1", min(1000, sec * limit))
    else:
        rows = x.klines(symbol, tf, limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("time")
    df.index = pd.to_datetime(df.index, utc=True)
    if sec < 60:
        df = _resample(df, sec)
    _cache[key] = (time.time(), df.tail(limit))
    return df.tail(limit)


def candles(db: Session, symbol: str, tf: str, limit: int) -> tuple[pd.DataFrame, str]:
    from .report import candles_tf
    sec = CHART_TFS[tf]
    if tf in md.TF_SECONDS:
        df = candles_tf(db, symbol, tf, limit=limit)
        if len(df) >= min(limit, 40):
            return df, "stored"
    else:
        df = pd.DataFrame()
    crypto_syms = state.get(db, "crypto").get("symbols", [])
    if symbol in crypto_syms and state.get(db, "crypto").get("mode", "OFF") != "OFF":
        try:
            ex_df = _exchange(db, symbol, tf, limit)
            if len(ex_df) >= 10:
                return ex_df, "exchange"
        except Exception as e:  # noqa: BLE001
            log.info("exchange chart %s %s: %s", symbol, tf, e)
    low = LOWER.get(tf)
    if low and sec >= 60:
        base = md.load_candles(db, symbol, low, limit=limit * sec // md.TF_SECONDS[low] + 2)
        if len(base) >= 20:
            return _resample(base, sec).tail(limit), f"built from {low}"
    tk = from_ticks(db, symbol, sec, limit)
    if len(tk) >= 3:
        return tk, "live price updates"
    return df, "stored"
