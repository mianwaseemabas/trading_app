"""Market data helpers: candle storage/validation, quotes, instrument specs,
currency conversion, and an optional synthetic data simulator (testing only)."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Candle, Instrument, Quote, utcnow

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}

DEFAULT_SPECS = {
    "EURUSD": dict(digits=5, point=0.00001, tick_size=0.00001, contract_size=100000, currency_base="EUR", currency_profit="USD"),
    "GBPUSD": dict(digits=5, point=0.00001, tick_size=0.00001, contract_size=100000, currency_base="GBP", currency_profit="USD"),
    "USDJPY": dict(digits=3, point=0.001, tick_size=0.001, contract_size=100000, currency_base="USD", currency_profit="JPY"),
    "AUDUSD": dict(digits=5, point=0.00001, tick_size=0.00001, contract_size=100000, currency_base="AUD", currency_profit="USD"),
    "USDCHF": dict(digits=5, point=0.00001, tick_size=0.00001, contract_size=100000, currency_base="USD", currency_profit="CHF"),
    "USDCAD": dict(digits=5, point=0.00001, tick_size=0.00001, contract_size=100000, currency_base="USD", currency_profit="CAD"),
    # Commodities and indices (typical CFD sizes; the real broker values replace these once a broker is connected)
    "XAUUSD": dict(digits=2, point=0.01, tick_size=0.01, contract_size=100, currency_base="XAU", currency_profit="USD"),
    "GOLD": dict(digits=2, point=0.01, tick_size=0.01, contract_size=100, currency_base="XAU", currency_profit="USD"),
    "XAGUSD": dict(digits=3, point=0.001, tick_size=0.001, contract_size=5000, currency_base="XAG", currency_profit="USD"),
    "SILVER": dict(digits=3, point=0.001, tick_size=0.001, contract_size=5000, currency_base="XAG", currency_profit="USD"),
    "USOIL": dict(digits=2, point=0.01, tick_size=0.01, contract_size=1000, currency_base="WTI", currency_profit="USD"),
    "XTIUSD": dict(digits=2, point=0.01, tick_size=0.01, contract_size=1000, currency_base="WTI", currency_profit="USD"),
    "OIL_CRUDE": dict(digits=2, point=0.01, tick_size=0.01, contract_size=1000, currency_base="WTI", currency_profit="USD"),
    "UKOIL": dict(digits=2, point=0.01, tick_size=0.01, contract_size=1000, currency_base="BRN", currency_profit="USD"),
    "OIL_BRENT": dict(digits=2, point=0.01, tick_size=0.01, contract_size=1000, currency_base="BRN", currency_profit="USD"),
    "US500": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="SPX", currency_profit="USD"),
    "US100": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="NDX", currency_profit="USD"),
    "NAS100": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="NDX", currency_profit="USD"),
    "US30": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="DJI", currency_profit="USD"),
    "DE40": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="DAX", currency_profit="EUR"),
    "GER40": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="DAX", currency_profit="EUR"),
    "UK100": dict(digits=1, point=0.1, tick_size=0.1, contract_size=1, currency_base="FTS", currency_profit="GBP"),
}


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def tf_delta(tf: str) -> timedelta:
    return timedelta(seconds=TF_SECONDS[tf])


def is_closed(candle_time: datetime, tf: str, now: datetime | None = None) -> bool:
    return as_utc(candle_time) + tf_delta(tf) <= (now or utcnow())


def ensure_instrument(db: Session, symbol: str) -> Instrument:
    inst = db.get(Instrument, symbol)
    if inst is None:
        spec = DEFAULT_SPECS.get(symbol, {})
        if not spec and len(symbol) == 6:
            spec = dict(currency_base=symbol[:3], currency_profit=symbol[3:])
            if symbol.endswith("JPY"):
                spec.update(digits=3, point=0.001, tick_size=0.001)
        inst = Instrument(symbol=symbol, **spec)
        db.add(inst)
        db.flush()
    return inst


def validate_candle(c: dict, tf: str, now: datetime | None = None) -> str | None:
    """Return an error string, or None if the candle is valid."""
    now = now or utcnow()
    try:
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    except (KeyError, TypeError, ValueError):
        return "non-numeric OHLC"
    if any(math.isnan(x) or x <= 0 for x in (o, h, l, cl)):
        return "non-positive or NaN price"
    if h < max(o, cl) - 1e-12 or l > min(o, cl) + 1e-12 or h < l:
        return "inconsistent high/low"
    t = c["time"]
    if t > now + timedelta(minutes=5):
        return "candle time in the future"
    # Up to H1, bars must align to UTC boundaries. H4/D1 bars follow the broker's
    # server-time session and are legitimately offset from UTC.
    if tf in TF_SECONDS and TF_SECONDS[tf] <= 3600 and int(t.timestamp()) % TF_SECONDS[tf] != 0:
        return "candle time not aligned to timeframe"
    if (h - l) / cl > 0.2:
        return "abnormal range (>20%)"
    return None


def upsert_candles(db: Session, symbol: str, tf: str, rows: list[dict], source: str) -> tuple[int, list[str]]:
    """Insert/update candles after validation. Only closed candles are stored."""
    errors, good = [], []
    now = utcnow()
    for r in rows:
        r = dict(r)
        r["time"] = as_utc(r["time"])
        if not is_closed(r["time"], tf, now):
            continue
        err = validate_candle(r, tf, now)
        if err:
            errors.append(f"{r['time'].isoformat()}: {err}")
            continue
        good.append(
            dict(
                symbol=symbol,
                timeframe=tf,
                time=r["time"],
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=float(r.get("tick_volume", 0) or 0),
                spread_points=float(r.get("spread", r.get("spread_points", 0)) or 0),
                source=source,
                collected_at=now,
            )
        )
    for i in range(0, len(good), 1000):
        chunk = good[i : i + 1000]
        stmt = pg_insert(Candle).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "time"],
            set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "tick_volume", "spread_points", "source", "collected_at")},
        )
        db.execute(stmt)
    return len(good), errors


def load_candles(db: Session, symbol: str, tf: str, limit: int = 1000, start=None, end=None) -> pd.DataFrame:
    q = select(Candle).where(Candle.symbol == symbol, Candle.timeframe == tf)
    if start is not None:
        q = q.where(Candle.time >= start)
    if end is not None:
        q = q.where(Candle.time <= end)
    if start is None:
        q = q.order_by(Candle.time.desc()).limit(limit)
    else:
        q = q.order_by(Candle.time.asc())
    rows = db.scalars(q).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "tick_volume", "spread_points"])
    df = pd.DataFrame(
        [
            dict(time=r.time, open=r.open, high=r.high, low=r.low, close=r.close,
                 tick_volume=r.tick_volume, spread_points=r.spread_points)
            for r in rows
        ]
    )
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").set_index("time")


def gap_report(df: pd.DataFrame, tf: str) -> dict:
    """Count missing candles, ignoring weekend market closure."""
    if len(df) < 2:
        return {"missing": 0, "largest_gap_candles": 0}
    step = TF_SECONDS[tf]
    diffs = (df.index.to_series().diff().dt.total_seconds().dropna() / step).astype(int) - 1
    weekday_gaps = []
    for t, g in diffs.items():
        if g <= 0:
            continue
        # Gaps over a weekend (Fri close -> Sun/Mon open) are expected.
        if t.weekday() in (6, 0) and g * step <= 3 * 86400:
            continue
        weekday_gaps.append(g)
    return {"missing": int(sum(weekday_gaps)), "largest_gap_candles": int(max(weekday_gaps, default=0))}


def latest_quotes(db: Session) -> dict[str, Quote]:
    return {q.symbol: q for q in db.scalars(select(Quote)).all()}


def mid(q: Quote) -> float:
    return (q.bid + q.ask) / 2


def conversion_rate(ccy: str, account_ccy: str, quotes: dict[str, Quote]) -> float | None:
    """Value of 1 unit of `ccy` expressed in `account_ccy`, from live quotes."""
    if not ccy or ccy == account_ccy:
        return 1.0
    direct = quotes.get(ccy + account_ccy)
    if direct:
        return mid(direct)
    inverse = quotes.get(account_ccy + ccy)
    if inverse and mid(inverse) > 0:
        return 1 / mid(inverse)
    return None


# ------------------------------------------------------------------ simulator
SIM_START = {"EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 150.00, "AUDUSD": 0.6600, "USDCHF": 0.8800, "USDCAD": 1.3600,
             "XAUUSD": 2650.0, "GOLD": 2650.0, "XAGUSD": 31.0, "SILVER": 31.0, "USOIL": 72.0, "XTIUSD": 72.0, "OIL_CRUDE": 72.0,
             "UKOIL": 76.0, "OIL_BRENT": 76.0, "US500": 5900.0, "US100": 20800.0, "NAS100": 20800.0, "US30": 43000.0, "DE40": 19500.0,
             "GER40": 19500.0, "UK100": 8200.0}


def _sim_candles(start_price: float, start_time: datetime, n: int, tf: str, digits: int, rng: random.Random,
                 anchor: float | None = None) -> list[dict]:
    out = []
    price = start_price
    anchor = anchor or start_price
    vol = 0.0009 * math.sqrt(TF_SECONDS[tf] / 3600)
    drift_state = 0.0
    for k in range(n):
        t = start_time + k * tf_delta(tf)
        if t.weekday() == 5 or (t.weekday() == 6 and t.hour < 22) or (t.weekday() == 4 and t.hour >= 22):
            continue
        drift_state = 0.95 * drift_state + rng.gauss(0, vol * 0.04) - 0.002 * math.log(price / anchor)
        o = price
        c = o * (1 + drift_state + rng.gauss(0, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.4)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.4)))
        out.append(dict(time=t, open=round(o, digits), high=round(h, digits), low=round(l, digits),
                        close=round(c, digits), tick_volume=rng.randint(100, 3000), spread=12))
        price = c
    return out


def _resample(df: pd.DataFrame, tf: str) -> list[dict]:
    rule = {"M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}[tf]
    agg = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum"}
    ).dropna()
    return [dict(time=t.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close,
                 tick_volume=r.tick_volume, spread=12) for t, r in agg.iterrows()]


def run_simulator(db: Session, symbols: list[str], timeframes: list[str], backfill: int = 4000) -> None:
    """Generate synthetic candles/quotes. FOR UI AND PIPELINE TESTING ONLY.

    M15 candles are simulated; higher timeframes are aggregated from them so
    every timeframe and the live quote stay consistent.
    """
    now = utcnow()
    rng = random.Random()
    base_tf = "M15"
    step = TF_SECONDS[base_tf]
    for sym in symbols:
        inst = ensure_instrument(db, sym)
        last = db.scalar(
            select(Candle).where(Candle.symbol == sym, Candle.timeframe == base_tf).order_by(Candle.time.desc()).limit(1)
        )
        current_open = datetime.fromtimestamp((int(now.timestamp()) // step) * step, tz=timezone.utc)
        if last is None:
            start = current_open - backfill * tf_delta(base_tf)
            price = SIM_START.get(sym, 1.0)
        else:
            start = as_utc(last.time) + tf_delta(base_tf)
            price = last.close
        n = int((current_open - start).total_seconds() // step)
        if n > 0:
            upsert_candles(db, sym, base_tf, _sim_candles(price, start, n, base_tf, inst.digits, rng, SIM_START.get(sym)), "simulator")
            db.flush()
        for tf in timeframes:
            if tf in (base_tf, "M1", "M5"):
                continue
            last_tf = db.scalar(
                select(Candle.time).where(Candle.symbol == sym, Candle.timeframe == tf).order_by(Candle.time.desc()).limit(1)
            )
            since = as_utc(last_tf) if last_tf else None
            df = load_candles(db, sym, base_tf, start=since) if since else load_candles(db, sym, base_tf, limit=backfill)
            if len(df):
                upsert_candles(db, sym, tf, _resample(df, tf), "simulator")
        last_close = db.scalar(
            select(Candle.close).where(Candle.symbol == sym, Candle.timeframe == base_tf).order_by(Candle.time.desc()).limit(1)
        ) or SIM_START.get(sym, 1.0)
        m = last_close * (1 + rng.gauss(0, 0.0002))
        half = 6 * inst.point
        q = db.get(Quote, sym)
        if q is None:
            q = Quote(symbol=sym, bid=0, ask=0, time=now)
            db.add(q)
            db.flush()  # sessions don't autoflush: a second call before commit would otherwise add a duplicate quote
        q.bid, q.ask = round(m - half, inst.digits), round(m + half, inst.digits)
        q.time, q.collected_at, q.source = now, now, "simulator"
        from .chart import record_tick
        record_tick(db, sym, q.bid, q.ask, now)
