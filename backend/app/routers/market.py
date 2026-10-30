from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import Candle, Instrument, User, utcnow
from ..services import market_data as md
from ..services.indicators import add_indicators

router = APIRouter(prefix="/api/market", tags=["market"])


def _num(x):
    try:
        f = float(x)
        return None if f != f else round(f, 6)
    except (TypeError, ValueError):
        return None


@router.get("/candles")
def candles(
    symbol: str = Query(..., max_length=32),
    timeframe: str = Query("H1"),
    limit: int = Query(500, ge=10, le=5000),
    user: User = Depends(require("market.view")),
    db: Session = Depends(get_db),
):
    if timeframe not in md.TF_SECONDS:
        raise HTTPException(400, "Unknown timeframe")
    cfg = state.get(db, "strategy")
    warm = 2 * cfg["ema_slow"]
    df = md.load_candles(db, symbol, timeframe, limit=limit + warm)
    if df.empty:
        return {"candles": [], "gaps": {"missing": 0}}
    ind = add_indicators(df, cfg).tail(limit)
    out = []
    for t, r in ind.iterrows():
        out.append({
            "time": int(t.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
            "ema_fast": _num(r.ema_fast), "ema_slow": _num(r.ema_slow), "rsi": _num(r.rsi),
            "macd": _num(r.macd), "macd_signal": _num(r.macd_signal), "macd_hist": _num(r.macd_hist), "atr": _num(r.atr),
        })
    return {"candles": out, "gaps": md.gap_report(df.tail(limit), timeframe)}


@router.get("/quotes")
def quotes(user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    now = utcnow()
    insts = {i.symbol: i for i in db.scalars(select(Instrument)).all()}
    out = []
    for q in md.latest_quotes(db).values():
        inst = insts.get(q.symbol)
        out.append({
            "symbol": q.symbol, "bid": q.bid, "ask": q.ask, "time": q.time, "source": q.source,
            "age_s": round((now - q.collected_at).total_seconds(), 1),
            "spread_points": round((q.ask - q.bid) / inst.point, 1) if inst else None,
        })
    return sorted(out, key=lambda x: x["symbol"])


@router.get("/instruments")
def instruments(user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Instrument).order_by(Instrument.symbol)).all()
    stats = {
        (s, tf): (n, first, last)
        for s, tf, n, first, last in db.execute(
            select(Candle.symbol, Candle.timeframe, func.count(), func.min(Candle.time), func.max(Candle.time))
            .group_by(Candle.symbol, Candle.timeframe)
        ).all()
    }
    return {
        "instruments": [
            {c: getattr(i, c) for c in ("symbol", "digits", "point", "contract_size", "tick_size", "tick_value",
                                        "volume_min", "volume_max", "volume_step", "currency_base", "currency_profit",
                                        "trade_allowed", "source", "updated_at")}
            for i in rows
        ],
        "history": [
            {"symbol": s, "timeframe": tf, "count": n, "first": f, "last": l} for (s, tf), (n, f, l) in sorted(stats.items())
        ],
    }


def _parse_time(v: str) -> datetime:
    v = v.strip()
    if v.isdigit():
        return datetime.fromtimestamp(int(v), tz=timezone.utc)
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return md.as_utc(datetime.strptime(v, fmt))
        except ValueError:
            pass
    return md.as_utc(datetime.fromisoformat(v.replace("Z", "+00:00")))


@router.post("/import-csv")
async def import_csv(
    request: Request,
    symbol: str = Form(..., max_length=32),
    timeframe: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require("backtest.run")),
    db: Session = Depends(get_db),
):
    """Import historical candles. Accepts MT5 'Export bars' CSV (tab/comma separated,
    <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> ...) or time,open,high,low,close[,volume,spread]."""
    if timeframe not in md.TF_SECONDS:
        raise HTTPException(400, "Unknown timeframe")
    if state.get(db, "data")["source"] != "mt5":
        raise HTTPException(400, "Switch the data source to MT5 before importing broker history")
    raw = await file.read(30 * 1024 * 1024 + 1)
    if len(raw) > 30 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 30 MB)")
    text = raw.decode("utf-8-sig", errors="replace")
    dialect = "\t" if text.count("\t") > text.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=dialect)
    header = [h.strip().strip("<>").lower() for h in next(reader, [])]
    idx = {h: i for i, h in enumerate(header)}
    rows = []
    try:
        for r in reader:
            if not r or not any(x.strip() for x in r):
                continue
            if "date" in idx and "time" in idx:
                t = _parse_time(f"{r[idx['date']]} {r[idx['time']]}")
            elif "date" in idx:
                t = _parse_time(r[idx["date"]])
            else:
                t = _parse_time(r[idx["time"]])
            rows.append({
                "time": t, "open": r[idx["open"]], "high": r[idx["high"]], "low": r[idx["low"]], "close": r[idx["close"]],
                "tick_volume": r[idx["tickvol"]] if "tickvol" in idx else (r[idx["volume"]] if "volume" in idx else 0),
                "spread": r[idx["spread"]] if "spread" in idx else 0,
            })
    except (KeyError, ValueError, IndexError) as e:
        raise HTTPException(400, f"Could not parse CSV: {e}. Expected columns date,time,open,high,low,close")
    symbol = symbol.strip().upper()
    md.ensure_instrument(db, symbol)
    n, errors = md.upsert_candles(db, symbol, timeframe, rows, "mt5")
    audit(db, "market.csv_imported", user=user, ip=client_ip(request),
          details={"symbol": symbol, "timeframe": timeframe, "stored": n, "rejected": len(errors)})
    db.commit()
    return {"stored": n, "rejected": len(errors), "errors": errors[:20]}
