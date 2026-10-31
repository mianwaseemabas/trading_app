"""Saudi stocks (Tadawul) — watch and research only, never traded by this app.

Prices come from Yahoo Finance's public chart endpoint (symbols like 2222.SR, index ^TASI.SR), delayed and
unofficial; the page says so. Candles are stored like other markets so charts, AI chart explanations and
line alerts work, but the trading engine and the order ticket ignore these symbols.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .. import state
from ..models import Instrument, Quote, utcnow
from . import market_data as md

log = logging.getLogger("tadawul")
POPULAR = {"^TASI.SR": "TASI index", "2222.SR": "Saudi Aramco", "1120.SR": "Al Rajhi Bank", "2010.SR": "SABIC", "7010.SR": "stc",
           "1180.SR": "Saudi National Bank", "2082.SR": "ACWA Power", "1211.SR": "Ma'aden", "2280.SR": "Almarai", "4013.SR": "Dr. Sulaiman Al Habib"}
VALID = re.compile(r"^(\^TASI|\d{4})\.SR$")
_last: dict = {}


def symbols(db: Session) -> list[str]:
    return list(state.get(db, "tadawul").get("symbols", []))


def is_watch_only(db: Session, symbol: str) -> bool:
    return symbol.upper() in set(symbols(db))


def market_open(now: datetime | None = None) -> bool:
    t = (now or utcnow()).astimezone(ZoneInfo("Asia/Riyadh"))
    return t.weekday() in (6, 0, 1, 2, 3) and (10, 0) <= (t.hour, t.minute) <= (15, 20)


def _fetch(symbol: str, interval: str, rng: str, http=None) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval={interval}&range={rng}"
    if http:
        return http(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (QivynexTrade watch-only)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _rows(data: dict) -> tuple[list[dict], dict]:
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise ValueError(err.get("description") or "no data")
    r = res[0]
    q = (r.get("indicators") or {}).get("quote", [{}])[0]
    out = []
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, low, c = (q.get(k, [None])[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, low, c):
            continue
        out.append({"time": datetime.fromtimestamp(ts, timezone.utc), "open": float(o), "high": float(h), "low": float(low), "close": float(c),
                    "tick_volume": float((q.get("volume") or [0])[i] or 0)})
    return out, r.get("meta") or {}


def refresh(db: Session, force: bool = False, http=None) -> dict:
    import os
    if http is None and os.environ.get("APP_ENV") == "test":
        return state.get(db, "tadawul")
    syms = symbols(db)
    st = state.get(db, "tadawul")
    status = st.get("status", {})
    every = 300 if market_open() else 3600
    for s in syms:
        if not force and time.time() - _last.get(s, 0) < every:
            continue
        _last[s] = time.time()
        try:
            meta = {}
            for tf, interval, rng in (("H1", "60m", "1mo"), ("D1", "1d", "2y")):
                rows, meta = _rows(_fetch(s, interval, rng, http))
                if tf == "H1":  # Yahoo hourly bars start at :00/:30 local; align to the hour for storage
                    rows = [{**x, "time": x["time"].replace(minute=0, second=0)} for x in rows]
                # drop the still-forming bar
                step = md.TF_SECONDS[tf]
                rows = [x for x in rows if (utcnow() - x["time"]).total_seconds() >= step]
                if rows:
                    md.upsert_candles(db, s, tf, rows, "yahoo")
            inst = db.get(Instrument, s)
            if inst is None:
                db.add(Instrument(symbol=s, digits=2, point=0.01, tick_size=0.01, contract_size=1, currency_base=s,
                                  currency_profit=meta.get("currency") or "SAR"))
            px = meta.get("regularMarketPrice")
            if px:
                q = db.get(Quote, s) or Quote(symbol=s)
                q.bid = q.ask = float(px)
                mt = meta.get("regularMarketTime")
                q.time = datetime.fromtimestamp(mt, timezone.utc) if mt else utcnow()
                q.collected_at, q.source = utcnow(), "yahoo"
                db.add(q)
            status[s] = {"ok": True, "at": utcnow().isoformat(), "name": meta.get("longName") or meta.get("shortName") or POPULAR.get(s, s),
                         "currency": meta.get("currency") or "SAR"}
        except Exception as e:  # noqa: BLE001
            status[s] = {"ok": False, "at": utcnow().isoformat(), "error": str(e)[:200], "name": POPULAR.get(s, s)}
            log.info("tadawul %s: %s", s, e)
    st["status"] = {k: v for k, v in status.items() if k in syms}
    state.put(db, "tadawul", st)
    return st
