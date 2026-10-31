"""Strategy race: all strategies paper-trade the same markets at the same time, side by side.

Each closed candle every strategy is evaluated; a signal opens a virtual trade (1R risk) at the candle close plus the
current spread as cost. Trades resolve on later candles (stop or target; if both are touched in one candle the stop is
assumed first — conservative). Results are in R, so markets with different prices compare fairly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import state
from ..models import Quote, RaceTrade, utcnow
from . import market_data as md

log = logging.getLogger("race")
MAX_BARS = 200


def cfg(db: Session) -> dict:
    return state.get(db, "race")


def markets(db: Session) -> list[tuple[str, bool]]:
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    return [(s, False) for s in strat["symbols"]] + ([(s, True) for s in cc.get("symbols", [])] if cc.get("mode", "OFF") != "OFF" else [])


def open_new(db: Session) -> int:
    from .strategy import STRATEGIES, evaluate, prepare
    c = cfg(db)
    if not c.get("enabled", True):
        return 0
    strat = state.get(db, "strategy")
    last = dict(c.get("last", {}))
    n = 0
    for sym, crypto in markets(db):
        for tf in c.get("timeframes", ["H1"]):
            df = md.load_candles(db, sym, tf, limit=450)
            if len(df) < 260:
                continue
            ct = df.index[-1].to_pydatetime()
            key = f"{sym}:{tf}"
            if last.get(key) == ct.isoformat():
                continue
            last[key] = ct.isoformat()
            q = db.get(Quote, sym)
            spread = (q.ask - q.bid) if q and q.ask > q.bid else 0.0
            for name in STRATEGIES:
                try:
                    pdf = prepare(df, {**strat, "name": name})
                    r = evaluate(pdf, len(pdf) - 1, {**strat, "name": name})
                except Exception as e:  # noqa: BLE001
                    log.info("race %s %s: %s", name, sym, e)
                    continue
                if r.action not in ("BUY", "SELL") or (crypto and r.action == "SELL") or not r.stop_loss or not r.take_profit:
                    continue
                busy = db.scalar(select(RaceTrade.id).where(RaceTrade.strategy == name, RaceTrade.symbol == sym, RaceTrade.timeframe == tf,
                                                           RaceTrade.status == "OPEN"))
                if busy:
                    continue
                entry = float(df["close"].iloc[-1])
                risk = abs(entry - r.stop_loss)
                if risk <= 0:
                    continue
                fee = entry * 0.002 if crypto else 0.0  # ~0.1% per side on spot exchanges
                db.execute(insert(RaceTrade).values(strategy=name, symbol=sym, timeframe=tf, side=r.action, candle_time=ct, entry=entry,
                                                    stop_loss=float(r.stop_loss), take_profit=float(r.take_profit),
                                                    cost_r=round((spread + fee) / risk, 4))
                           .on_conflict_do_nothing(constraint="uq_race_trade"))
                n += 1
    c["last"] = last
    c.setdefault("started_at", utcnow().isoformat())
    state.put(db, "race", c)
    return n


def resolve(db: Session) -> int:
    done = 0
    for t in db.scalars(select(RaceTrade).where(RaceTrade.status == "OPEN")).all():
        df = md.load_candles(db, t.symbol, t.timeframe, start=t.candle_time + timedelta(seconds=1))
        if df.empty:
            continue
        risk = abs(t.entry - t.stop_loss)
        for i, (ts, row) in enumerate(df.iterrows()):
            if t.side == "BUY":
                hit_sl, hit_tp = row.low <= t.stop_loss, row.high >= t.take_profit
            else:
                hit_sl, hit_tp = row.high >= t.stop_loss, row.low <= t.take_profit
            if hit_sl or hit_tp:
                t.status, t.exit_price = ("LOSS", t.stop_loss) if hit_sl else ("WIN", t.take_profit)
            elif i + 1 >= MAX_BARS:
                t.status, t.exit_price = "EXPIRED", float(row.close)
            else:
                continue
            gross = (t.exit_price - t.entry) / risk * (1 if t.side == "BUY" else -1)
            t.r, t.exit_time = round(gross - t.cost_r, 3), ts.to_pydatetime()
            done += 1
            break
    return done


def run(db: Session) -> None:
    open_new(db)
    db.flush()
    resolve(db)


def leaderboard(db: Session) -> dict:
    from .strategy import STRATEGIES
    rows = db.scalars(select(RaceTrade).order_by(RaceTrade.candle_time)).all()
    board = []
    for name in STRATEGIES:
        ts = [t for t in rows if t.strategy == name]
        closed = [t for t in ts if t.status != "OPEN"]
        rs = [t.r for t in closed]
        eq, peak, dd, curve = 0.0, 0.0, 0.0, []
        for t in closed:
            eq += t.r
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
            curve.append(round(eq, 2))
        gp, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
        board.append({"strategy": name, "trades": len(closed), "open": len(ts) - len(closed),
                      "wins": sum(1 for r in rs if r > 0), "win_rate_pct": round(100 * sum(1 for r in rs if r > 0) / len(rs)) if rs else None,
                      "total_r": round(sum(rs), 2), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
                      "profit_factor": round(gp / gl, 2) if gl else None, "max_dd_r": round(dd, 2), "curve": curve[-200:],
                      "reliable": len(rs) >= 30})
    board.sort(key=lambda b: (b["trades"] > 0, b["total_r"]), reverse=True)
    recent = [{"id": t.id, "strategy": t.strategy, "symbol": t.symbol, "tf": t.timeframe, "side": t.side, "time": t.candle_time,
               "entry": t.entry, "sl": t.stop_loss, "tp": t.take_profit, "status": t.status, "r": t.r} for t in rows[-60:][::-1]]
    return {"board": board, "recent": recent, "config": {k: v for k, v in cfg(db).items() if k != "last"}}


def reset(db: Session, timeframes: list[str]) -> None:
    db.query(RaceTrade).delete()
    state.put(db, "race", {"enabled": True, "timeframes": timeframes, "started_at": utcnow().isoformat(), "last": {}})


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None
