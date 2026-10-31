"""Signal intelligence: score breakdown, hard gates, cost-aware economics, verdict, lifecycle and outcomes.

Principles (from the owner's master plan):
* The score is a QUALITY score, never a win probability.
* p_win comes only from resolved comparable outcomes; with fewer than `min_sample` it is UNKNOWN.
* Any failed hard gate => NO_TRADE. "Doing nothing" is a valid, recorded result.
* Every signal (also rejected / expired / never traded) gets a shadow outcome for unbiased research.
"""
from __future__ import annotations

import math
from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..models import Instrument, Order, Signal, SignalAssessment, SignalEvent, SignalOutcome, utcnow
from . import market_data as md
from . import quality, report, smc
from .indicators import add_indicators

MODEL = "score-v1"


from .strategies_extra import adx  # noqa: E402


def lifecycle(db: Session, sig_id: int, st: str, detail: str = "") -> None:
    db.add(SignalEvent(signal_id=sig_id, state=st, detail=detail[:500]))


# ------------------------------------------------------------------ stats
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1)


def comparable(db: Session, strategy: str, symbol: str | None = None, side: str | None = None) -> dict:
    q = select(SignalOutcome, Signal).join(Signal, Signal.id == SignalOutcome.signal_id).where(
        SignalOutcome.status.in_(["TARGET", "STOP", "EXPIRED"]), Signal.strategy == strategy, SignalOutcome.r_net.is_not(None))
    if symbol:
        q = q.where(Signal.symbol == symbol)
    if side:
        q = q.where(Signal.action == side)
    rows = db.execute(q).all()
    rs = [o.r_net for o, _ in rows]
    n = len(rs)
    min_n = int(state.get(db, "assessment").get("min_sample", 30))
    if n == 0:
        return {"n": 0, "min_sample": min_n, "p_win": "UNKNOWN", "note": "no resolved comparable signals yet"}
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    exp = sum(rs) / n
    sd = (sum((r - exp) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else None
    ci = (round(exp - 1.96 * sd / math.sqrt(n), 3), round(exp + 1.96 * sd / math.sqrt(n), 3)) if sd is not None else None
    out = {"n": n, "min_sample": min_n, "wins": len(wins), "win_rate_pct": round(len(wins) / n * 100, 1), "win_rate_ci95": wilson(len(wins), n),
           "avg_win_r": round(sum(wins) / len(wins), 3) if wins else None, "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
           "expectancy_r": round(exp, 3), "expectancy_ci95": ci,
           "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(losses) > 0 else None}
    out["p_win"] = out["win_rate_pct"] if n >= min_n else "UNKNOWN"
    if out["avg_win_r"] and out["avg_loss_r"]:
        out["break_even_win_rate_pct"] = round(out["avg_loss_r"] / (out["avg_win_r"] + out["avg_loss_r"]) * 100, 1)
    return out


def reliability(stats: dict, strategy_enabled: bool = True) -> str:
    if not strategy_enabled:
        return "PAUSED"
    if stats["n"] < stats["min_sample"]:
        return "INSUFFICIENT_EVIDENCE"
    if stats["expectancy_r"] <= 0 or (stats.get("expectancy_ci95") and stats["expectancy_ci95"][1] <= 0):
        return "RESEARCH_ONLY"
    return "PAPER_CANDIDATE"


# ------------------------------------------------------------- assessment
def _risk_for(db: Session, sig: Signal):
    """Run the risk engine for this signal at today's price (no order is placed)."""
    from . import trading
    from .risk_engine import evaluate
    if (sig.trading_mode or "").startswith("CRYPTO_"):
        from .crypto import desk
        ctx = desk.build_context(db, sig.symbol, sig.action, sig.stop_loss, sig.take_profit, desk.pos_mode(db) or "CRYPTO_PAPER")
    else:
        mode = state.get(db, "trading")["mode"]
        ctx = trading.build_context(db, symbol=sig.symbol, side=sig.action, tf=sig.timeframe, sl=sig.stop_loss, tp=sig.take_profit,
                                    mode=mode if mode in trading.ORDER_MODES else "PAPER")
    d = evaluate(ctx)
    return d, ctx


def economics(d, ctx) -> dict:
    """Cost-aware money at stake for the risk-engine size (instrument-specific)."""
    from .risk_engine import value_per_price_unit
    vpu = value_per_price_unit(ctx)
    if vpu and ctx.stop_loss and not d.volume and d.loss_per_lot:
        mn = ctx.spec.volume_min
        return {"available": False, "min_size_loss": round(mn * d.loss_per_lot, 2), "risk_budget": d.risk_budget,
                "reason": f"even the smallest size the broker allows ({mn}) would risk about {mn * d.loss_per_lot:.2f} "
                          f"(incl. costs), more than your risk budget {d.risk_budget:.2f} — reduce the stop distance, raise the budget "
                          f"deliberately, or skip this trade"}
    if not vpu or not d.volume or not ctx.stop_loss:
        return {"available": False, "reason": "size or contract value unavailable"}
    entry = d.entry_price
    vol = d.volume
    comm = float(ctx.cfg.get("commission_per_lot", 0)) * vol
    slip = float(ctx.cfg.get("max_slippage_points", 0)) * ctx.spec.point * vol * vpu
    gross_stop = abs(entry - ctx.stop_loss) * vol * vpu
    gross_target = abs((ctx.take_profit or entry) - entry) * vol * vpu if ctx.take_profit else None
    costs = comm + slip
    net_stop = gross_stop + costs
    net_target = (gross_target - comm) if gross_target is not None else None
    eq = ctx.account.equity or 0
    out = {"available": True, "side": ctx.side, "entry_side_of_market": "ask" if ctx.side == "BUY" else "bid", "entry": entry,
           "bid": ctx.bid, "ask": ctx.ask, "stop": ctx.stop_loss, "target": ctx.take_profit, "quantity": vol,
           "unit": "coins" if ctx.spec.contract_size == 1 and ctx.account.leverage == 1 else ("lots" if ctx.spec.currency_base else "units"),
           "min_qty": ctx.spec.volume_min, "qty_step": ctx.spec.volume_step, "spread_cost_in_entry": round(abs(ctx.ask - ctx.bid) * vol * vpu, 2),
           "commission_round_trip": round(comm, 2), "slippage_allowance": round(slip, 2), "gross_stop": round(gross_stop, 2),
           "gross_target": round(gross_target, 2) if gross_target is not None else None, "net_stop": round(net_stop, 2),
           "net_target": round(net_target, 2) if net_target is not None else None,
           "gross_rr": round(gross_target / gross_stop, 2) if gross_target and gross_stop else None,
           "net_rr": round(net_target / net_stop, 2) if net_target and net_stop > 0 else None,
           "risk_budget": d.risk_budget, "risk_pct_of_equity": round(net_stop / eq * 100, 2) if eq else None,
           "cost_r": round(costs / gross_stop, 3) if gross_stop else None,
           "warning": "Losses can exceed the planned stop when prices gap or slip."}
    if out["net_target"] and out["net_stop"]:
        out["break_even_win_rate_pct"] = round(out["net_stop"] / (out["net_target"] + out["net_stop"]) * 100, 1)
    return out


def assess(db: Session, sig: Signal, *, d=None, ctx=None, save: bool = True) -> SignalAssessment:
    cfg = state.get(db, "strategy")
    acfg = state.get(db, "assessment")
    side = sig.action
    want = "up" if side == "BUY" else "down"
    if d is None:
        d, ctx = _risk_for(db, sig)
    crypto_syms = set(state.get(db, "crypto").get("symbols", []))
    df = md.load_candles(db, sig.symbol, sig.timeframe if sig.timeframe != "MAN" else "H1", limit=400)
    ind = add_indicators(df, cfg) if len(df) > 60 else None
    atr_now = float(ind["atr"].iloc[-1]) if ind is not None and ind["atr"].iloc[-1] == ind["atr"].iloc[-1] else None
    qq = quality.quote_quality(db, sig.symbol, crypto_syms, atr_now)
    breakdown, reasons = [], []

    def comp(name, pts, maxp, evidence):
        breakdown.append({"component": name, "points": int(pts), "max": maxp, "evidence": evidence})
        if pts < maxp * 0.5:
            reasons.append(f"weak {name.lower()}: {evidence}")

    tfc = {tf: report.tf_context(db, sig.symbol, tf, cfg) for tf in ("H4", "H1")}
    agree = [tf for tf, c in tfc.items() if c and c["trend"] == want]
    against = [tf for tf, c in tfc.items() if c and c["trend"] not in (want, "mixed", "unknown")]
    comp("H4/H1 trend alignment", 0 if against else (20 if len(agree) == 2 else 10 if agree else 4), 20,
         ", ".join(f"{tf} {c['trend'] if c else 'n/a'}" for tf, c in tfc.items()))
    s_pts, s_ev = 0, []
    if ind is not None:
        tail = ind.tail(300)
        ev = [e for e in smc.analyze(tail, cfg)["events"] if e["i"] >= len(tail) - 30]
        bull = side == "BUY"
        if any(e["kind"] == ("sweep_ssl" if bull else "sweep_bsl") for e in ev):
            s_pts += 10; s_ev.append("liquidity sweep in favour")  # noqa: E702
        if any(e["kind"] in ("bos", "choch") and e["dir"] == ("bull" if bull else "bear") for e in ev):
            s_pts += 10; s_ev.append("structure break in favour")  # noqa: E702
        if any(e["kind"] == "fvg" and e["dir"] == ("bull" if bull else "bear") for e in ev):
            s_pts += 5; s_ev.append("fair value gap in favour")  # noqa: E702
        if any(e["kind"] in ("bos", "choch") and e["dir"] == ("bear" if bull else "bull") for e in ev[-8:]):
            s_pts = max(0, s_pts - 10); s_ev.append("recent opposite structure break")  # noqa: E702
    comp("Confirmed structure/liquidity", min(25, s_pts), 25, ", ".join(s_ev) or "no confirmed SMC evidence in the last 30 candles")
    q_pts, q_ev = 0, []
    if ind is not None:
        r = ind.iloc[-1]
        rsi = float(r["rsi"]) if r["rsi"] == r["rsi"] else None
        if rsi is not None and ((side == "BUY" and 45 <= rsi <= 70) or (side == "SELL" and 30 <= rsi <= 55)):
            q_pts += 7; q_ev.append(f"RSI {rsi:.0f} in zone")  # noqa: E702
        if r["macd_hist"] == r["macd_hist"] and ((side == "BUY") == (r["macd_hist"] > 0)):
            q_pts += 7; q_ev.append("MACD momentum agrees")  # noqa: E702
        a = adx(df).iloc[-1]
        if a == a and a >= 20:
            q_pts += 6; q_ev.append(f"ADX {a:.0f} (trending)")  # noqa: E702
        elif a == a:
            q_ev.append(f"ADX {a:.0f} (weak trend)")
    comp("Independent quantitative confirmation", q_pts, 20, ", ".join(q_ev) or "indicators unavailable")
    eco = economics(d, ctx) if ctx is not None else {"available": False}
    nrr = eco.get("net_rr")
    comp("Entry quality & net reward/risk", 15 if nrr and nrr >= 2 else 10 if nrr and nrr >= 1.5 else 5 if nrr and nrr >= 1.2 else 0, 15,
         f"net R:R {nrr}" if nrr else "net R:R unavailable")
    v_pts, v_ev = 0, []
    if ind is not None:
        atr = ind["atr"].dropna()
        pct = float((atr < atr.iloc[-1]).mean() * 100) if len(atr) else None
        if pct is not None and 15 <= pct <= 85:
            v_pts += 5
        v_ev.append(f"volatility percentile {pct:.0f}" if pct is not None else "volatility n/a")
    if qq.get("quality") == "OK":
        v_pts += 5
    v_ev.append(f"quote {qq.get('quality')}")
    comp("Volatility & liquidity", v_pts, 10, ", ".join(v_ev))
    news = report.upcoming_news(db, sig.symbol, 2, ("High",))
    ses = report.sessions_now()
    e_pts = (0 if news else 5) + (5 if (sig.symbol in crypto_syms or not ses["liquidity"].startswith(("low", "closed"))) else 0)
    comp("Event & session conditions", e_pts, 10, (f"high-impact news soon: {news[0]['title']}" if news else "no high-impact news in 2 h") + f"; session {ses['liquidity']}")
    score = sum(b["points"] for b in breakdown)

    # ---- hard gates
    gates = []

    def gate(name, ok, detail=""):
        gates.append({"gate": name, "passed": bool(ok), "detail": detail})

    gate("market data trustworthy (fresh, open, normal spread)", qq.get("quality") == "OK", f"{qq.get('quality')}: {'; '.join(qq.get('issues', [])) or qq.get('session')}")
    if sig.timeframe in md.TF_SECONDS:
        lag = (utcnow() - (sig.candle_time + md.tf_delta(sig.timeframe))).total_seconds()
        gate("signal candle confirmed and recent", lag <= md.TF_SECONDS[sig.timeframe] * 3, f"closed {lag / 60:.0f} min ago")
    gate("valid stop and target", bool(sig.stop_loss and sig.take_profit), f"SL {sig.stop_loss} TP {sig.take_profit}")
    gate("net reward/risk ≥ 1.0 after costs", bool(nrr and nrr >= 1.0), f"net R:R {nrr}")
    controls = state.get(db, "controls")
    if controls.get("news_filter"):
        ns = state.get(db, "news_status")
        gate("economic calendar loaded", bool(ns.get("ok")), ns.get("error") or ns.get("last_fetch", ""))
    failed_risk = [c for c in (d.checks if d else []) if not c["passed"]]
    gate("risk engine (size, limits, exposure, kill switch)", not failed_risk, "; ".join(f"{c['check']} ({c.get('detail', '')})" for c in failed_risk[:4]))
    stats = comparable(db, sig.strategy, sig.symbol)
    stats_all = comparable(db, sig.strategy)
    rel = reliability(stats_all)
    t = acfg
    tier = "REJECT" if score < t["min_watch"] else "WATCH" if score < t["min_candidate"] else "CANDIDATE" if score < t["min_eligible"] else "ELIGIBLE"
    if any(not g["passed"] for g in gates):
        verdict = "NO_TRADE"
        reasons = [f"{g['gate']}: {g['detail']}" for g in gates if not g["passed"]] + reasons
    elif tier == "REJECT":
        verdict = "NO_TRADE"
        reasons.insert(0, f"score {score} below {t['min_watch']}")
    elif tier == "WATCH":
        verdict = "WATCH"
    elif rel == "PAPER_CANDIDATE" and tier == "ELIGIBLE":
        verdict = "RESEARCH_SUPPORTED"
    else:
        verdict = "PAPER_TEST"
    data = {k: qq.get(k) for k in ("provider", "session", "quality", "quote_age_s", "spread_pips", "spread_bps", "spread_atr_pct", "issues")}
    data["data_blocked"] = not gates[0]["passed"]
    a = db.get(SignalAssessment, sig.id) if (sig.id and save) else None  # transient when not saving (survives rollback)
    if a is None:
        a = SignalAssessment(signal_id=sig.id)
    a.at, a.score, a.breakdown, a.gates, a.verdict, a.tier, a.reliability = utcnow(), score, breakdown, gates, verdict, tier, rel
    a.reasons = reasons[:10]
    a.economics = {**eco, "comparable_symbol": stats, "comparable_strategy": stats_all,
                   "do_nothing": "Not trading keeps the full risk budget and has zero cost — a valid choice."}
    a.data, a.model_version = data, MODEL
    if save and sig.id:
        db.add(a)
    return a


# --------------------------------------------------------------- outcomes
def track_outcomes(db: Session, limit: int = 150) -> int:
    """Shadow-evaluate signals: did price hit the target or the stop first? (conservative: stop first on the same candle)."""
    sigs = db.scalars(select(Signal).outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id).where(
        Signal.action.in_(["BUY", "SELL"]), Signal.stop_loss.is_not(None), Signal.take_profit.is_not(None), Signal.entry_ref.is_not(None),
        Signal.timeframe.in_(list(md.TF_SECONDS)), (SignalOutcome.signal_id.is_(None)) | (SignalOutcome.status == "OPEN"),
        Signal.created_at >= utcnow() - timedelta(days=120)).order_by(Signal.id).limit(limit)).all()
    done = 0
    for s in sigs:
        o = db.get(SignalOutcome, s.id) or SignalOutcome(signal_id=s.id)
        start = s.candle_time + md.tf_delta(s.timeframe)
        df = md.load_candles(db, s.symbol, s.timeframe, start=start, end=utcnow())
        df = df[df.index >= start]
        risk = abs(s.entry_ref - s.stop_loss)
        if risk <= 0:
            o.status = "NO_DATA"
            db.add(o)
            continue
        a = db.get(SignalAssessment, s.id)
        cost_r = float((a.economics or {}).get("cost_r") or 0) if a else 0.0
        inst = db.get(Instrument, s.symbol)
        spread = float(df["spread_points"].iloc[0]) * inst.point if inst and len(df) and "spread_points" in df and df["spread_points"].iloc[0] else 0.0
        buy = s.action == "BUY"
        mfe = mae = 0.0
        status, exit_px, bars = "OPEN", None, 0
        for t, r in df.iterrows():
            bars += 1
            hi, lo = (r.high, r.low) if buy else (r.high + spread, r.low + spread)
            fav = (hi - s.entry_ref) if buy else (s.entry_ref - lo)
            adv = (s.entry_ref - lo) if buy else (hi - s.entry_ref)
            mfe, mae = max(mfe, fav / risk), max(mae, adv / risk)
            if (buy and lo <= s.stop_loss) or (not buy and hi >= s.stop_loss):
                status, exit_px = "STOP", s.stop_loss
            elif (buy and hi >= s.take_profit) or (not buy and lo <= s.take_profit):
                status, exit_px = "TARGET", s.take_profit
            if status != "OPEN":
                o.resolved_at = t.to_pydatetime()
                break
            if bars >= 200:
                status, exit_px = "EXPIRED", float(r.close)
                o.resolved_at = t.to_pydatetime()
                break
        o.status, o.bars, o.exit_price = status, bars, exit_px
        o.max_favorable_r, o.max_adverse_r = round(mfe, 3), round(mae, 3)
        o.checked_to = df.index[-1].to_pydatetime() if len(df) else None
        if exit_px is not None:
            g = ((exit_px - s.entry_ref) if buy else (s.entry_ref - exit_px)) / risk
            o.r_gross, o.r_net = round(g, 3), round(g - cost_r, 3)
            done += 1
        o.traded = db.scalar(select(Order.id).where(Order.signal_id == s.id, Order.status == "FILLED").limit(1)) is not None
        db.add(o)
    return done


def route(db: Session, sig: Signal, d, ctx) -> tuple[str, SignalAssessment]:
    """Score + gate a fresh strategy signal; record lifecycle and alerts. Returns (decision, assessment)
    where decision is BLOCKED_DATA | REJECTED | WATCH | QUALIFIED."""
    from .alerts import raise_alert, signal_text
    lifecycle(db, sig.id, "DETECTED", f"{sig.strategy} {sig.symbol} {sig.timeframe} {sig.action}")
    a = assess(db, sig, d=d, ctx=ctx)
    lifecycle(db, sig.id, "SCORED", f"{a.score}/100 {a.tier} · {a.verdict}")
    if a.data.get("data_blocked"):
        sig.status = "BLOCKED_DATA"
        lifecycle(db, sig.id, "BLOCKED_DATA", "; ".join(a.reasons[:2]))
        return "BLOCKED_DATA", a
    lifecycle(db, sig.id, "DATA_VALIDATED", f"{a.data.get('provider')} {a.data.get('quality')}")
    if a.verdict == "NO_TRADE":
        sig.status = "REJECTED"
        lifecycle(db, sig.id, "REJECTED", "; ".join(a.reasons[:3]))
        raise_alert(db, "SIGNAL_REJECTED", f"{sig.symbol} {sig.action} setup rejected ({a.score}/100)", "\n".join(a.reasons[:5]),
                    dedup=f"rej:{sig.id}", symbol=sig.symbol, signal_id=sig.id, link="#/signals")
        return "REJECTED", a
    if a.verdict == "WATCH":
        sig.status = "WATCH"
        lifecycle(db, sig.id, "WATCHLIST", f"score {a.score}")
        return "WATCH", a
    lifecycle(db, sig.id, "CANDIDATE", f"{a.verdict} · reliability {a.reliability}")
    lifecycle(db, sig.id, "RISK_ELIGIBLE", f"size {d.volume}")
    title, body = signal_text(sig, a, label="PAPER SIGNAL" if "LIVE" not in (sig.trading_mode or "") else "SIGNAL")
    raise_alert(db, "SIGNAL_QUALIFIED", title, body, dedup=f"qual:{sig.id}", symbol=sig.symbol, signal_id=sig.id, link="#/setups")
    return "QUALIFIED", a


def request_approval(db: Session, sig: Signal, d, minutes: int) -> None:
    from ..models import SignalApproval
    from .alerts import raise_alert, signal_text
    sig.status = "AWAITING"
    db.add(SignalApproval(signal_id=sig.id, expires_at=utcnow() + timedelta(minutes=minutes),
                          snapshot={k: v for k, v in d.to_dict().items() if k != "checks"}))
    lifecycle(db, sig.id, "AWAITING_APPROVAL", f"expires in {minutes} min")
    a = db.get(SignalAssessment, sig.id)
    title, body = signal_text(sig, a, label="APPROVAL NEEDED")
    raise_alert(db, "APPROVAL_REQUIRED", title, body, severity="warning", dedup=f"appr:{sig.id}", symbol=sig.symbol,
                signal_id=sig.id, link="#/setups")
