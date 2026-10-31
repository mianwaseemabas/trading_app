"""Opportunity Ranking: is a setup ACTIONABLE right now — not just "did a pattern happen"?

Every few minutes the scanner evaluates every strategy on every market and timeframe you trade. A setup is kept while
it is fresh (fired within the last 3 closed candles) and price is still between its stop and target. Each one then goes
through the same assessment the live engine uses — data quality, trend/structure/momentum evidence, spread and costs,
stop distance, your risk budget and limits, news — and gets a decision:

  TRADE CANDIDATE · WATCH · REJECT (a hard check failed, e.g. spread, risk budget, news) · NO TRADE (weak evidence)

Candidates are ranked by quality score, historical evidence for that strategy (strategy race, strategy lab, past signal
outcomes) and freshness. Nothing here places an order by itself unless you switch on 'auto-trade proven opportunities',
which only acts on strategies with positive measured results and still goes through approval/auto-trade + risk engine.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..models import Quote, RaceTrade, Signal, utcnow
from . import market_data as md

log = logging.getLogger("opportunities")
TFS = ("M15", "H1", "H4")
FRESH_BARS = 3
DECISION_ORDER = {"TRADE CANDIDATE": 0, "WATCH": 1, "REJECT": 2, "NO TRADE": 3}


def cfg(db: Session) -> dict:
    return state.get(db, "opportunity_cfg")


def _markets(db: Session) -> list[tuple[str, bool]]:
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    return [(s, False) for s in strat["symbols"]] + ([(s, True) for s in cc.get("symbols", [])] if cc.get("mode", "OFF") != "OFF" else [])


def _key(sym: str, tf: str, strategy: str, side: str, t) -> str:
    return hashlib.sha1(f"{sym}|{tf}|{strategy}|{side}|{t.isoformat()}".encode()).hexdigest()[:12]


def evidence(db: Session, strategy: str, symbol: str, tf: str) -> dict:
    """Measured history for this strategy: race (live paper, in R), lab (backtest final test), past signal outcomes."""
    rs = db.execute(select(func.count(), func.coalesce(func.sum(RaceTrade.r), 0.0)).where(
        RaceTrade.strategy == strategy, RaceTrade.status != "OPEN")).one()
    race_n, race_r = int(rs[0]), float(rs[1])
    lab = next((r for r in state.get(db, "strategy_lab").get("results", [])
                if r.get("strategy") == strategy and r.get("symbol") == symbol and r.get("tf") == tf), None)
    ft = (lab or {}).get("final_test") or {}
    lines, level = [], "unknown"
    if race_n:
        lines.append(f"Strategy race: {race_r:+.1f}R over {race_n} paper trades")
    if lab:
        lines.append(f"Lab {symbol} {tf}: {lab.get('verdict')} (final test {ft.get('trades', 0)} trades, PF {ft.get('profit_factor')})")
    good_lab = bool(lab and lab.get("promising"))
    if (race_n >= 30 and race_r > 0) or good_lab:
        level = "proven"
    elif race_n >= 10 and race_r < 0 or (lab and (ft.get("net_pnl") or 0) < 0 and ft.get("trades", 0) >= 10):
        level = "negative"
    elif race_n or lab:
        level = "early"
    pts = {"proven": 100, "early": 55, "unknown": 40, "negative": 10}[level]
    return {"level": level, "points": pts, "lines": lines or ["No measured results yet for this strategy — paper test first."]}


def _card(db: Session, sig: Signal, a, ev: dict, fresh_bars: int, route: dict, key: str) -> dict:
    eco = a.economics or {}
    failed = [g for g in a.gates if not g["passed"]]
    if failed:
        decision = "REJECT"
    elif a.verdict in ("PAPER_TEST", "RESEARCH_SUPPORTED"):
        decision = "TRADE CANDIDATE"
    elif a.verdict == "WATCH":
        decision = "WATCH"
    else:
        decision = "NO TRADE"
    freshness = {0: 100, 1: 70, 2: 45}.get(fresh_bars, 30)
    rank = round(a.score * 0.6 + ev["points"] * 0.25 + freshness * 0.15, 1)
    items = []
    for b in a.breakdown:
        share = b["points"] / b["max"] if b["max"] else 0
        items.append({"ok": "pass" if share >= 0.6 else "warn" if share >= 0.3 else "fail", "text": f"{b['component']}: {b['evidence']}"})
    for g in failed:
        items.append({"ok": "fail", "text": f"{g['gate']} — {g['detail']}"})
    q = a.data or {}
    if q.get("spread_pips") is not None:
        items.append({"ok": "pass" if q.get("quality") == "OK" else "warn", "text": f"Spread now {q.get('spread_pips')} pips · data {q.get('quality')}"})
    return {
        "key": key, "symbol": sig.symbol, "tf": sig.timeframe, "strategy": sig.strategy, "side": sig.action,
        "candle_time": sig.candle_time.isoformat(), "fresh_bars": fresh_bars, "decision": decision, "rank": rank, "score": a.score,
        "verdict": a.verdict, "entry": eco.get("entry") or sig.entry_ref, "stop": sig.stop_loss, "target": sig.take_profit,
        "economics": {k: eco.get(k) for k in ("available", "quantity", "unit", "gross_target", "gross_stop", "net_target", "net_stop", "gross_rr", "net_rr",
                                               "spread_cost_in_entry", "commission_round_trip", "slippage_allowance", "break_even_win_rate_pct",
                                               "risk_budget", "risk_pct_of_equity", "reason")},
        "evidence": items, "history": ev, "reasons": (a.reasons or [])[:4], "route": route,
        "do_nothing": "Not trading keeps the full risk budget and costs nothing.",
    }


def scan(db: Session, force: bool = False) -> dict:
    from . import strategy as strat
    from .assess import assess
    from .indicators import required_history
    from .intel import route_for
    from .strategy import STRATEGIES
    st = state.get(db, "opportunities")
    if not force and st.get("at") and time.time() - st.get("ts", 0) < 240:
        return st
    t0 = time.time()
    base = state.get(db, "strategy")
    cards, n_setups = {}, 0
    for sym, crypto in _markets(db):
        q = db.get(Quote, sym)
        if not q or not q.bid:
            continue
        route = route_for(db, sym)
        for tf in TFS:
            df = md.load_candles(db, sym, tf, limit=required_history(base) + 60)
            if len(df) < required_history(base) // 2:
                continue
            if not md.is_closed(df.index[-1].to_pydatetime(), tf):
                df = df.iloc[:-1]
            if (utcnow() - (df.index[-1].to_pydatetime() + md.tf_delta(tf))).total_seconds() > 3 * md.TF_SECONDS[tf]:
                continue  # stale market (closed / no data)
            for name in STRATEGIES:
                c = {**base, "name": name}
                try:
                    ind = strat.prepare(df, c)
                except Exception as e:  # noqa: BLE001
                    log.info("prepare %s %s: %s", name, sym, e)
                    continue
                for back in range(FRESH_BARS):
                    i = len(ind) - 1 - back
                    try:
                        r = strat.evaluate(ind, i, c)
                    except Exception:  # noqa: BLE001
                        break
                    if r.action not in ("BUY", "SELL") or not r.stop_loss or not r.take_profit:
                        continue
                    if crypto and r.action == "SELL":
                        break  # spot: cannot sell short
                    # still valid? price must not have reached stop or target since the signal candle
                    after = ind.iloc[i + 1:]
                    px = q.ask if r.action == "BUY" else q.bid
                    hit = (after["low"] <= r.stop_loss).any() or (after["high"] >= r.take_profit).any() if r.action == "BUY" else \
                        (after["high"] >= r.stop_loss).any() or (after["low"] <= r.take_profit).any()
                    inside = r.stop_loss < px < r.take_profit if r.action == "BUY" else r.take_profit < px < r.stop_loss
                    if hit or not inside:
                        break
                    n_setups += 1
                    ct = ind.index[i].to_pydatetime()
                    sig = Signal(symbol=sym, timeframe=tf, candle_time=ct, strategy=name, strategy_version="scan", action=r.action,
                                 entry_ref=px, stop_loss=float(r.stop_loss), take_profit=float(r.take_profit), features=r.features,
                                 reasons=r.reasons, status="SCAN", trading_mode=route.get("mode") or "PAPER")
                    try:
                        a = assess(db, sig, save=False)
                    except Exception as e:  # noqa: BLE001
                        log.info("assess %s %s %s: %s", name, sym, tf, e)
                        break
                    key = _key(sym, tf, name, r.action, ct)
                    cards[key] = _card(db, sig, a, evidence(db, name, sym, tf), back, route, key)
                    break
    items = sorted(cards.values(), key=lambda x: (DECISION_ORDER[x["decision"]], -x["rank"]))[:60]
    counts = {d: sum(1 for x in cards.values() if x["decision"] == d) for d in DECISION_ORDER}
    st = {"at": utcnow().isoformat(), "ts": time.time(), "items": items, "counts": counts, "setups": n_setups,
          "markets": len(_markets(db)), "seconds": round(time.time() - t0, 1)}
    state.put(db, "opportunities", st)
    db.flush()
    _auto(db, items)
    return st


def queue(db: Session, key: str, by: str = "you") -> dict:
    """Turn a ranked opportunity into a real setup: it is re-assessed and then follows your normal rules —
    approval on 'Setups to approve', or auto-trade if that account allows it. The risk engine checks it again."""
    from . import assess as A
    from . import trading
    from .automation import announce, auto_ok
    from .risk_engine import evaluate
    item = next((x for x in state.get(db, "opportunities").get("items", []) if x["key"] == key), None)
    if not item:
        return {"ok": False, "detail": "This opportunity is no longer in the list — scan again"}
    route = item["route"]
    mode = route.get("mode")
    crypto = mode.startswith("CRYPTO_") if mode else False
    if route["kind"] in ("watch", "off", "signals") or not mode:
        return {"ok": False, "detail": f"Orders for {item['symbol']} are switched off ({route['label']})"}
    from datetime import datetime
    ver = f"opp-{item['strategy']}"[:32]
    ct = datetime.fromisoformat(item["candle_time"])
    if db.scalar(select(Signal.id).where(Signal.symbol == item["symbol"], Signal.timeframe == item["tf"], Signal.candle_time == ct,
                                         Signal.strategy_version == ver)):
        return {"ok": False, "detail": "Already sent"}
    q = db.get(Quote, item["symbol"])
    px = q.ask if item["side"] == "BUY" else q.bid
    sig = Signal(symbol=item["symbol"], timeframe=item["tf"], candle_time=ct, strategy=item["strategy"], strategy_version=ver,
                 action=item["side"], entry_ref=px, stop_loss=item["stop"], take_profit=item["target"],
                 features={"source": "opportunity ranking", "rank": item["rank"], "by": by}, reasons=[], status="NO_TRADE", trading_mode=mode)
    db.add(sig)
    db.flush()
    if crypto:
        from .crypto import desk
        ctx = desk.build_context(db, sig.symbol, "BUY", sig.stop_loss, sig.take_profit, mode)
    else:
        ctx = trading.build_context(db, symbol=sig.symbol, side=sig.action, tf=sig.timeframe, sl=sig.stop_loss, tp=sig.take_profit, mode=mode)
    d = evaluate(ctx)
    sig.risk_checks = d.checks
    outcome, a = A.route(db, sig, d, ctx)
    if outcome != "QUALIFIED":
        return {"ok": False, "detail": f"Re-check at today's price: {outcome.lower()} — " + "; ".join((a.reasons or [])[:2]), "signal_id": sig.id}
    if auto_ok(db, mode, a):
        if crypto:
            from .crypto import desk
            p, _c, _o = desk.place(db, symbol=sig.symbol, sl=sig.stop_loss, tp=sig.take_profit, by="auto", source=sig.strategy, signal=sig)
            if p is None:
                return {"ok": False, "detail": "Exchange order not placed — see Alerts", "signal_id": sig.id}
            vol, price = p.volume, p.open_price
        else:
            o = trading.place_order(db, sig, d, mode)
            vol, price = o.filled_volume or o.volume, o.fill_price or o.requested_price
        sig.status = "ORDERED"
        A.lifecycle(db, sig.id, "AUTO_ORDERED", f"{mode} · from opportunity ranking")
        announce(db, sig, vol, price, mode)
        return {"ok": True, "detail": f"Placed automatically on {route['label']} (auto-trade is on there)", "signal_id": sig.id}
    A.request_approval(db, sig, d, int(state.get(db, "controls").get("approval_minutes", 60)))
    return {"ok": True, "detail": "Sent to 'Setups to approve' — decide there", "signal_id": sig.id}


def _auto(db: Session, items: list[dict]) -> None:
    """Optional: auto-send the best TRADE CANDIDATE whose strategy has PROVEN measured results (one per scan)."""
    if not cfg(db).get("auto_proven"):
        return
    done = set(state.get(db, "opportunities_sent") or [])
    for it in items:
        if it["decision"] == "TRADE CANDIDATE" and it["history"]["level"] == "proven" and it["key"] not in done and it["route"].get("auto"):
            res = queue(db, it["key"], by="auto (proven opportunity)")
            done.add(it["key"])
            state.put(db, "opportunities_sent", sorted(done)[-300:])
            log.info("auto opportunity %s: %s", it["key"], res)
            break


def job(db: Session) -> None:
    scan(db)


def _since(minutes: int):
    return utcnow() - timedelta(minutes=minutes)
