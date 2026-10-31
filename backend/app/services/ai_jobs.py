"""Scheduled AI helpers: the morning brief and the weekly trade-journal coach.

Both work without AI too (a plain data summary is sent), and both go out through the normal alert channels
(Telegram / email / phone app) as AI_BRIEF / AI_COACH alerts.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..models import NewsEvent, NewsHeadline, Order, Position, Signal, SignalApproval, TradeNote, utcnow

log = logging.getLogger("ai_jobs")

COACH_PROMPT = """You are the trading-journal coach in the user's private app Qivynex Trade. You get the user's closed trades
for the last week plus statistics computed by the app. Write exactly three numbered LESSONS, each: the pattern you see
(with the numbers that show it), why it matters, and one concrete rule to try next week. Base every lesson on the data;
if the sample is small, say so and keep lessons about process (stops, sizing, journaling, waiting for setups).
Never promise profit, never suggest removing stops, martingale, averaging down or bigger risk to recover losses.
Then one line 'Next week's focus:'. Plain words, max 220 words. Reply in English unless the notes are in Urdu."""

BRIEF_PROMPT = """You are the morning-brief writer in the user's private trading app Qivynex Trade (forex + crypto spot).
From the APP DATA write a short brief for a beginner, using these headings exactly:
TODAY'S RISK EVENTS (times in the user's timezone given), MARKETS (one line per market: trend on H4/H1 in plain words and
what would change it), YOUR TRADES (open positions and waiting setups — or 'none'), PLAN (2-3 bullet rules for today, e.g.
'no new USD trades 30 min before CPI'). Facts only from the data, no predictions, no profit promises. Max 230 words."""


def cfg(db: Session) -> dict:
    return state.get(db, "ai_jobs")


def _local(db: Session, dt: datetime | None = None) -> datetime:
    tz = ZoneInfo(cfg(db).get("timezone") or "Asia/Riyadh")
    return (dt or utcnow()).astimezone(tz)


def _ai(db: Session, system: str, prompt: str) -> str | None:
    ai = state.get(db, "ai")
    if not ai.get("enabled") or not ai.get("api_key_enc"):
        return None
    from ..routers.ai import _call
    from ..secretbox import decrypt
    return _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), system, [{"role": "user", "content": prompt}])


# ------------------------------------------------------------------ morning brief
def brief_data(db: Session) -> dict:
    from . import report
    from .headlines import watched_assets
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    now = utcnow()
    watched = watched_assets(db)
    ccys = sorted({a for a in watched if len(a) == 3})
    events = db.scalars(select(NewsEvent).where(NewsEvent.time >= now, NewsEvent.time <= now + timedelta(hours=24),
                                                NewsEvent.impact == "High", NewsEvent.currency.in_(ccys or ["USD"]))
                        .order_by(NewsEvent.time)).all()
    heads = db.scalars(select(NewsHeadline).where(NewsHeadline.published >= now - timedelta(hours=14), NewsHeadline.impact == "High")
                       .order_by(NewsHeadline.published.desc()).limit(25)).all()
    markets = []
    for sym in list(strat["symbols"]) + (cc.get("symbols", []) if cc.get("mode") != "OFF" else []):
        row = {"symbol": sym}
        for tf in ("H4", "H1"):
            try:
                c = report.tf_context(db, sym, tf, strat)
            except Exception:  # noqa: BLE001
                c = None
            if c:
                row[tf] = {"trend": c["trend"], "structure": c["structure"], "rsi": c["rsi"] and round(c["rsi"]),
                           "close": c["close"], "atr_pct": c["atr_pct"]}
        markets.append(row)
    pos = db.scalars(select(Position).where(Position.status == "OPEN")).all()
    waiting = db.execute(select(Signal, SignalApproval).join(SignalApproval, SignalApproval.signal_id == Signal.id)
                         .where(SignalApproval.status == "WAITING")).all()
    return {
        "user_local_time": _local(db).strftime("%a %d %b %H:%M"), "timezone": cfg(db).get("timezone"),
        "session": report.sessions_now(),
        "high_impact_events_next_24h": [{"local_time": _local(db, e.time).strftime("%H:%M"), "currency": e.currency, "title": e.title,
                                         "forecast": e.forecast, "previous": e.previous} for e in events],
        "high_impact_headlines": [{"title": h.title, "source": h.source, "assets": h.assets} for h in heads
                                  if set(h.assets) & watched][:8],
        "markets": markets,
        "open_positions": [{"symbol": p.symbol, "side": p.side, "mode": p.mode, "open": p.open_price, "sl": p.stop_loss,
                            "tp": p.take_profit, "pnl": round(p.unrealized_pnl or 0, 2)} for p in pos],
        "waiting_setups": [{"id": s.id, "symbol": s.symbol, "action": s.action, "tf": s.timeframe} for s, _ in waiting],
    }


def plain_brief(d: dict) -> str:
    out = [f"Time: {d['user_local_time']} ({d['timezone']}) · liquidity {d['session']['liquidity']}", "", "TODAY'S RISK EVENTS"]
    out += [f"• {e['local_time']} {e['currency']} {e['title']}" for e in d["high_impact_events_next_24h"]] or ["• none high-impact"]
    out += ["", "MARKETS"]
    for m in d["markets"]:
        h4, h1 = m.get("H4", {}), m.get("H1", {})
        out.append(f"• {m['symbol']}: H4 {h4.get('trend', '?')} · H1 {h1.get('trend', '?')} · RSI {h1.get('rsi', '?')}")
    out += ["", "YOUR TRADES"]
    out += [f"• {p['symbol']} {p['side']} ({p['mode']}) P/L {p['pnl']}" for p in d["open_positions"]] or ["• no open positions"]
    if d["waiting_setups"]:
        out.append(f"• {len(d['waiting_setups'])} setup(s) waiting for you: " + ", ".join(f"#{s['id']} {s['symbol']} {s['action']}" for s in d["waiting_setups"]))
    return "\n".join(out)


def morning_brief(db: Session, *, force: bool = False) -> str:
    d = brief_data(db)
    text = None
    try:
        text = _ai(db, BRIEF_PROMPT, "APP DATA:\n" + json.dumps(d, default=str)[:12000])
    except Exception as e:  # noqa: BLE001
        log.warning("AI brief failed, sending plain brief: %s", e)
    text = (text or plain_brief(d)).strip()
    from .alerts import raise_alert
    day = _local(db).strftime("%Y-%m-%d")
    raise_alert(db, "AI_BRIEF", f"☀️ Morning brief · {_local(db).strftime('%a %d %b')}", text,
                dedup=f"brief:{day}" + (f":{utcnow().timestamp():.0f}" if force else ""), link="#/terminal")
    return text


# ------------------------------------------------------------------ weekly coach
def week_trades(db: Session, days: int = 7) -> list[dict]:
    since = utcnow() - timedelta(days=days)
    rows = db.execute(select(Position, Order, TradeNote).outerjoin(Order, Order.id == Position.order_id)
                      .outerjoin(TradeNote, TradeNote.position_id == Position.id)
                      .where(Position.status == "CLOSED", Position.closed_at >= since).order_by(Position.closed_at)).all()
    out = []
    for p, o, n in rows:
        risk = (o.risk_amount if o else 0) or 0
        lt = _local(db, p.opened_at)
        out.append({"symbol": p.symbol, "side": p.side, "mode": p.mode, "opened_local": lt.strftime("%a %H:%M"), "weekday": lt.strftime("%A"),
                    "hours_held": round(((p.closed_at or p.opened_at) - p.opened_at).total_seconds() / 3600, 1),
                    "close_reason": p.close_reason, "net_pnl": round(p.realized_pnl or 0, 2),
                    "r": round((p.realized_pnl or 0) / risk, 2) if risk else None, "strategy_trade": bool(o and o.signal_id),
                    "note": (n.note[:200] if n else ""), "tags": (n.tags if n else [])})
    return out


def week_stats(trades: list[dict]) -> dict:
    def agg(key):
        g = defaultdict(lambda: {"n": 0, "wins": 0, "net": 0.0})
        for t in trades:
            x = g[t[key]]
            x["n"] += 1
            x["wins"] += t["net_pnl"] > 0
            x["net"] = round(x["net"] + t["net_pnl"], 2)
        return dict(g)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    rs = [t["r"] for t in trades if t["r"] is not None]
    return {"trades": len(trades), "win_rate_pct": round(100 * len(wins) / len(trades)) if trades else None,
            "net": round(sum(t["net_pnl"] for t in trades), 2), "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "avg_hours_winners": round(sum(t["hours_held"] for t in wins) / len(wins), 1) if wins else None,
            "avg_hours_losers": round(sum(t["hours_held"] for t in losses) / len(losses), 1) if losses else None,
            "closed_manually": sum(1 for t in trades if t["close_reason"] in ("manual", "user")),
            "by_weekday": agg("weekday"), "by_symbol": agg("symbol"), "by_reason": agg("close_reason"),
            "with_journal_note": sum(1 for t in trades if t["note"])}


def weekly_coach(db: Session, *, force: bool = False) -> str:
    trades = week_trades(db)
    stats = week_stats(trades)
    text = None
    if trades:
        try:
            text = _ai(db, COACH_PROMPT, "STATS:\n" + json.dumps(stats) + "\n\nTRADES:\n" + json.dumps(trades[:80], default=str)[:10000])
        except Exception as e:  # noqa: BLE001
            log.warning("AI coach failed: %s", e)
    if not text:
        if not trades:
            text = ("No closed trades this week, so there is nothing to learn from yet.\n"
                    "1. Take 3–5 practice trades next week (Terminal → Place).\n2. Write one journal line per trade.\n"
                    "3. Next Sunday the coach will find your patterns.")
        else:
            text = (f"{stats['trades']} trades · win rate {stats['win_rate_pct']}% · net {stats['net']} · avg R {stats['avg_r']}\n"
                    f"Winners held {stats['avg_hours_winners']} h, losers {stats['avg_hours_losers']} h · "
                    f"{stats['with_journal_note']}/{stats['trades']} with a journal note.\n(Turn on the AI assistant for written lessons.)")
    from .alerts import raise_alert
    wk = _local(db).strftime("%G-W%V")
    raise_alert(db, "AI_COACH", f"🧭 Weekly coach · {wk}", text.strip(), dedup=f"coach:{wk}" + (f":{utcnow().timestamp():.0f}" if force else ""),
                link="#/trades")
    return text


# ------------------------------------------------------------------ scheduler (worker, every minute)
def scheduled(db: Session) -> None:
    c = cfg(db)
    now = _local(db)
    hm = now.strftime("%H:%M")
    if c.get("brief_enabled") and hm >= c.get("brief_time", "07:30") and c.get("last_brief") != now.strftime("%Y-%m-%d"):
        c["last_brief"] = now.strftime("%Y-%m-%d")
        state.put(db, "ai_jobs", c)
        db.commit()
        morning_brief(db)
    c = cfg(db)
    if (c.get("coach_enabled") and now.isoweekday() % 7 == int(c.get("coach_day", 0)) and hm >= c.get("coach_time", "20:00")
            and c.get("last_coach") != now.strftime("%G-W%V")):
        c["last_coach"] = now.strftime("%G-W%V")
        state.put(db, "ai_jobs", c)
        db.commit()
        weekly_coach(db)


def explain_chart(db: Session, symbol: str, tf: str) -> dict:
    """Ask the chart: what price is doing, key levels, reasons both ways (AI writes, app data only)."""
    from . import chart, smc
    from .indicators import add_indicators
    strat = state.get(db, "strategy")
    df, source = chart.candles(db, symbol, tf, 300)
    if len(df) < 30:
        raise ValueError("Not enough candles on this timeframe yet")
    ind = add_indicators(df, strat)
    last = ind.iloc[-1]
    smc_state = smc.analyze(ind.tail(300), strat)["state"] if len(ind) >= 60 else {}
    tail = ind.tail(40)
    data = {"symbol": symbol, "timeframe": tf, "data_source": source, "last_close": float(last.close),
            "change_pct_window": round((tail.close.iloc[-1] / tail.close.iloc[0] - 1) * 100, 3),
            "high_window": float(tail.high.max()), "low_window": float(tail.low.min()),
            "ema_fast": float(last.ema_fast), "ema_slow": float(last.ema_slow), "rsi": round(float(last.rsi), 1) if last.rsi == last.rsi else None,
            "macd_hist": float(last.get("macd_hist", 0) or 0), "atr": float(last.atr) if last.atr == last.atr else None,
            "smc": {k: v for k, v in smc_state.items() if k in ("trend", "bsl", "ssl", "open_fvgs")},
            "last_candles": [{"t": t.isoformat(), "o": r.open, "h": r.high, "l": r.low, "c": r.close} for t, r in tail.tail(12).iterrows()]}
    prompt = ("Explain this chart for a beginner using exactly these headings: WHAT PRICE IS DOING, KEY LEVELS, REASONS IT COULD GO UP, "
              "REASONS IT COULD GO DOWN, WHAT TO WATCH NEXT. Use only the numbers given. No prediction, no trade advice, max 200 words.\n\n"
              "APP DATA:\n" + json.dumps(data, default=str))
    text = _ai(db, "You explain charts in plain words inside the Qivynex Trade app. You never predict prices or tell the user to trade.", prompt)
    if not text:
        trend = "up" if data["last_close"] > data["ema_slow"] and data["ema_fast"] > data["ema_slow"] else \
            "down" if data["last_close"] < data["ema_slow"] and data["ema_fast"] < data["ema_slow"] else "sideways/mixed"
        text = (f"WHAT PRICE IS DOING: {data['change_pct_window']}% over the last 40 candles; trend by EMAs: {trend}; RSI {data['rsi']}.\n"
                f"KEY LEVELS: recent high {data['high_window']}, recent low {data['low_window']}.\n"
                "Turn on the AI assistant for a full written explanation.")
    return {"symbol": symbol, "tf": tf, "explanation": text, "data": data}


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
