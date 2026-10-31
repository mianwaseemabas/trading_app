"""Intelligence platform endpoints used by the Terminal screen."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import User
from ..secretbox import decrypt
from ..services import intel, market_data as md, report
from ..services.indicators import add_indicators

router = APIRouter(prefix="/api/intel", tags=["intel"])


def _venue(db: Session, symbol: str) -> str:
    from ..services.tadawul import is_watch_only
    if is_watch_only(db, symbol):
        return "watch"
    return "crypto" if symbol in set(state.get(db, "crypto").get("symbols", [])) else "broker"


@router.get("/watchlist")
def watchlist(user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    return intel.watchlist(db)


class FavIn(BaseModel):
    symbols: list[str] = Field(max_length=40)


@router.put("/favorites")
def favorites(body: FavIn, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    state.put(db, "favorites", sorted({s.upper() for s in body.symbols}), by=user.username)
    db.commit()
    return state.get(db, "favorites")


@router.get("/terminal")
def terminal(symbol: str = "EURUSD", user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    symbol = symbol.upper()
    venue = _venue(db, symbol)
    wl = intel.watchlist(db)
    movers = [w for w in wl if w["change_24h"] is not None]
    best = max(movers, key=lambda w: w["change_24h"]) if movers else None
    worst = min(movers, key=lambda w: w["change_24h"]) if movers else None
    return {
        "symbol": symbol, "venue": venue, "route": intel.route_for(db, symbol), "asset": next((w["asset"] for w in wl if w["symbol"] == symbol), "forex"),
        "digits": intel.digits_of(db, symbol),
        "account": intel.account_bar(db, "crypto" if venue == "crypto" else "broker"),
        "highlights": {"best": best, "worst": worst},
        "sentiment": intel.sentiment(db, [w["symbol"] for w in wl]),
        "insights": {k: v for k, v in intel.insights(db, symbol).items() if k != "timeframes"},
        "risk": intel.risk_score(db, symbol),
        "achievements": intel.achievements(db),
        "session": report.sessions_now(),
    }


@router.get("/candles")
def candles(symbol: str, tf: str = "H1", limit: int = Query(120, ge=20, le=1000), user: User = Depends(require("market.view")),
            db: Session = Depends(get_db)):
    from ..services import chart
    if tf not in chart.CHART_TFS:
        raise HTTPException(400, "Unknown timeframe")
    cfg = state.get(db, "strategy")
    df, source = chart.candles(db, symbol.upper(), tf, limit + 60)
    if df.empty:
        return {"candles": [], "source": source}
    ind = add_indicators(df, {**cfg, "ema_fast": 20, "ema_slow": cfg["ema_fast"]})
    mid = ind["close"].rolling(20).mean()
    sd = ind["close"].rolling(20).std()
    ind["bb_up"], ind["bb_lo"] = mid + 2 * sd, mid - 2 * sd
    ind = ind.tail(limit)
    nan = lambda v: None if v is None or v != v else float(v)  # noqa: E731
    vol = "tick_volume" in ind
    out = [{"time": int(t.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "ema20": nan(r.ema_fast),
            "ema50": nan(r.ema_slow), "bb_up": nan(r.bb_up), "bb_lo": nan(r.bb_lo), "rsi": nan(r.rsi), "macd": nan(r.get("macd")),
            "macd_signal": nan(r.get("macd_signal")), "macd_hist": nan(r.get("macd_hist")), "volume": nan(r.tick_volume) if vol else None}
           for t, r in ind.iterrows()]
    markers = []
    if len(ind):
        from ..models import Signal
        t0 = ind.index[0].to_pydatetime()
        for sg in db.scalars(select(Signal).where(Signal.symbol == symbol.upper(), Signal.timeframe == tf, Signal.action != "NONE",
                                                  Signal.candle_time >= t0).order_by(Signal.candle_time).limit(300)).all():
            markers.append({"time": int(sg.candle_time.timestamp()), "action": sg.action, "status": sg.status, "id": sg.id,
                            "strategy": sg.strategy})
    return {"symbol": symbol.upper(), "tf": tf, "source": source, "seconds": chart.CHART_TFS[tf], "candles": out, "markers": markers}


_rate: dict = {}


class BriefIn(BaseModel):
    symbol: str = Field(max_length=32)


@router.post("/brief")
def brief(body: BriefIn, request: Request, user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    """AI market brief: the language model explains the app's measured data (it cannot trade)."""
    from .ai import SYSTEM_PROMPT, _call
    ai = state.get(db, "ai")
    if not ai["enabled"] or not ai.get("api_key_enc"):
        raise HTTPException(400, "Turn on the AI assistant first (AI Assistant page) to get written briefs")
    last = _rate.get(user.id, 0)
    if time.time() - last < 10:
        raise HTTPException(429, "Please wait a few seconds between briefs")
    _rate[user.id] = time.time()
    sym = body.symbol.upper()
    data = {"symbol": sym, "insights": intel.insights(db, sym), "risk": intel.risk_score(db, sym),
            "sentiment": intel.sentiment(db, [sym]), "session": report.sessions_now(),
            "news": report.upcoming_news(db, sym, 24)}
    prompt = (f"Write a short market brief for {sym} for a beginner. Use exactly three headed sections: OBSERVED (only facts "
              "from the data), INTERPRETATION (what it may mean, both directions), UNCERTAINTY (what could make it wrong, "
              "including news). Finish with one line: what a disciplined trader would wait for. No price predictions, no "
              f"advice to trade.\n\nDATA:\n{json.dumps(data, default=str)[:9000]}")
    text = _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), SYSTEM_PROMPT, [{"role": "user", "content": prompt}])
    audit(db, "ai.brief", user=user, ip=client_ip(request), details={"symbol": sym})
    db.commit()
    return {"symbol": sym, "brief": text, "generated_at": time.time()}
