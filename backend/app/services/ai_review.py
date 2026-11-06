"""AI bull/bear review of strategy setups — the TradingAgents idea, rebuilt inside the app.

Debate style (default, 3 AI calls): a BULL analyst and a BEAR analyst each argue from the app's own data (setup,
score breakdown, Time Machine odds, H4/H1 context, calendar, headlines), then a RISK MANAGER reads both and gives a
rating — BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL — with a confidence, the strongest point on each side and what
would change its mind. Quick style does the same in one call (cheaper, faster).

Modes (setting `ai_review.mode`):
* OFF       — nothing runs.
* LOG_ONLY  — qualified setups are reviewed in the background and the result is only RECORDED. It never changes a
              trade. Use this first, for weeks, and compare the reviews with what actually happened (stats()).
* VETO      — before an AUTO trade is placed, the setup is reviewed. OPPOSE (confident enough) → no auto trade, the
              setup goes to manual approval. NEUTRAL → `neutral_action` (HALF = half size, VETO, or ALLOW).
              Any AI error, timeout, unreadable answer or exhausted daily budget → manual approval (fails closed).

The AI can only block or shrink a trade. It never places, enlarges or approves one; the risk engine still decides
size and checks every order. LLM answers vary between runs and models know history, so this cannot be backtested
honestly — only forward-tested (LOG_ONLY).
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

log = logging.getLogger("ai_review")

RATINGS = ("BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL")
MODES = ("OFF", "LOG_ONLY", "VETO")
STYLES = ("debate", "quick")
NEUTRAL_ACTIONS = ("HALF", "VETO", "ALLOW")

RULES = """Rules: use ONLY the APP DATA (numbers measured by the app). Quote the numbers you rely on. Do not invent prices, news or
events. SMC terms describe price patterns, not what institutions do. No profit promises. Plain English."""

BULL = """You are the BULL analyst on a small trading desk. Make the strongest HONEST case FOR taking this {side} trade on
{symbol} ({tf}). Max 120 words, 3-5 bullet points, strongest first. """ + RULES

BEAR = """You are the BEAR analyst on a small trading desk. Make the strongest HONEST case AGAINST taking this {side} trade on
{symbol} ({tf}) — what could make it lose, what the data does not support. Max 120 words, 3-5 bullet points, strongest first. """ + RULES

JUDGE = """You are the RISK MANAGER making the final call on a {side} setup on {symbol} ({tf}) for the life of this trade
(until its stop or target, roughly {horizon}). You have the APP DATA{debate}. Rate the expected direction of {symbol} over that
horizon as exactly one of: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL (BUY/OVERWEIGHT = up, UNDERWEIGHT/SELL = down, HOLD = no clear
edge). Prefer HOLD when the evidence is mixed or thin. Reply with ONLY one JSON object, no other text:
{{"rating": "...", "confidence": 0-100, "bull_point": "strongest point for, max 25 words",
"bear_point": "strongest point against, max 25 words", "change_mind": "what observable event would change your rating, max 25 words",
"summary": "one or two sentences"{extra}}}
""" + RULES

_bg_lock = threading.Lock()


# ------------------------------------------------------------------ pure helpers (unit-tested)
def stance_of(rating: str, side: str) -> str:
    r = (rating or "").upper()
    if r not in RATINGS:
        return "ERROR"
    if r == "HOLD":
        return "NEUTRAL"
    up = r in ("BUY", "OVERWEIGHT")
    return "AGREE" if up == (side == "BUY") else "OPPOSE"


def parse_verdict(text: str) -> dict:
    """Extract and validate the judge's JSON. Raises ValueError when it is unusable."""
    if not text:
        raise ValueError("empty AI answer")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the AI answer")
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(re.sub(r",\s*}", "}", raw))  # tolerate one trailing comma
    rating = str(data.get("rating", "")).strip().upper().replace(" ", "")
    if rating not in RATINGS:
        raise ValueError(f"rating '{data.get('rating')}' is not one of {', '.join(RATINGS)}")
    try:
        conf = int(round(float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0
    clip = lambda k, n=400: str(data.get(k) or "").strip()[:n]  # noqa: E731
    return {"rating": rating, "confidence": max(0, min(100, conf)), "bull_point": clip("bull_point"), "bear_point": clip("bear_point"),
            "change_mind": clip("change_mind"), "summary": clip("summary", 600), "bull_case": clip("bull_case", 1500),
            "bear_case": clip("bear_case", 1500)}


def decide(review: dict | None, side: str, cfg: dict) -> tuple[str, str]:
    """Policy for VETO mode: ALLOW | HALF | VETO. None / errors fail closed (VETO = manual approval)."""
    if not review or review.get("error"):
        return "VETO", f"AI review unavailable ({(review or {}).get('error') or 'no answer'}) — sent to manual approval"
    st = review.get("stance") or stance_of(review.get("rating", ""), side)
    conf = int(review.get("confidence") or 0)
    if st == "AGREE":
        return "ALLOW", f"AI agrees ({review['rating']}, {conf}%)"
    if st == "OPPOSE":
        if conf >= int(cfg.get("min_confidence", 60)):
            return "VETO", f"AI opposes ({review['rating']}, {conf}%) — sent to manual approval"
        act = cfg.get("neutral_action", "HALF")
        return (act if act in NEUTRAL_ACTIONS else "HALF"), f"AI opposes with low confidence ({review['rating']}, {conf}%) — treated as neutral"
    if st == "NEUTRAL":
        act = cfg.get("neutral_action", "HALF")
        return (act if act in NEUTRAL_ACTIONS else "HALF"), f"AI neutral (HOLD, {conf}%)"
    return "VETO", "AI answer unusable — sent to manual approval"


def shrink(volume: float, volume_min: float, volume_step: float) -> float | None:
    """Half the size, rounded DOWN to the broker step. None when that is below the broker minimum. Never increases."""
    if not volume or volume <= 0:
        return None
    step = volume_step or volume_min or 0.01
    v = math.floor((volume / 2) / step + 1e-9) * step
    v = round(v, 8)
    if v < (volume_min or step) - 1e-12 or v <= 0:
        return None
    return min(v, volume)


# ------------------------------------------------------------------ settings / budget
def cfg(db) -> dict:
    from .. import state
    return state.get(db, "ai_review")


def ai_ready(db) -> bool:
    from .. import state
    ai = state.get(db, "ai")
    return bool(ai.get("enabled") and ai.get("api_key_enc") and ai.get("provider") in ("openai", "anthropic"))


def used_today(db) -> int:
    """Reviews that called the AI since 00:00 UTC (counted from the table — no shared counter to lock)."""
    from sqlalchemy import func, select

    from ..models import AIReview, utcnow
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(select(func.count()).select_from(AIReview).where(AIReview.at >= start, AIReview.calls > 0)) or 0)


def budget_left(db) -> int:
    return max(0, int(cfg(db).get("max_per_day", 20)) - used_today(db))


# ------------------------------------------------------------------ data for the prompt
def context(db, sig) -> dict:
    from sqlalchemy import select

    from .. import state
    from ..models import NewsHeadline, SignalAssessment, utcnow
    from . import report
    from .instruments_meta import headline_assets
    strat = state.get(db, "strategy")
    a = db.get(SignalAssessment, sig.id)
    eco = (a.economics or {}) if a else {}
    risk = abs((sig.entry_ref or 0) - (sig.stop_loss or 0))
    rr = round(abs((sig.take_profit or 0) - (sig.entry_ref or 0)) / risk, 2) if risk else None
    tfs = {}
    for tf in ("D1", "H4", "H1"):
        try:
            c = report.tf_context(db, sig.symbol, tf, strat)
        except Exception:  # noqa: BLE001
            c = None
        if c:
            tfs[tf] = {k: c.get(k) for k in ("trend", "structure", "rsi", "atr_pct", "close")}
    try:
        assets = {x.upper() for x in headline_assets(sig.symbol)}
    except Exception:  # noqa: BLE001
        assets = {sig.symbol[:3], sig.symbol[3:6]}
    heads = []
    for h in db.scalars(select(NewsHeadline).where(NewsHeadline.published >= utcnow() - timedelta(hours=24),
                                                   NewsHeadline.impact.in_(["High", "Medium"]))
                        .order_by(NewsHeadline.published.desc()).limit(150)).all():
        if assets & {str(x).upper() for x in (h.assets or [])}:
            heads.append({"time": h.published.isoformat(), "impact": h.impact, "source": h.source, "title": h.title[:200]})
        if len(heads) >= 10:
            break
    return {
        "setup": {"symbol": sig.symbol, "timeframe": sig.timeframe, "side": sig.action, "strategy": sig.strategy,
                  "signal_candle": sig.candle_time.isoformat() if sig.candle_time else None, "entry": sig.entry_ref,
                  "stop": sig.stop_loss, "target": sig.take_profit, "reward_risk": rr},
        "strategy_reasons": [r for r in (sig.reasons or []) if isinstance(r, dict)][:12],
        "assessment": {"score": a.score, "verdict": a.verdict, "tier": a.tier, "reliability": a.reliability,
                       "breakdown": a.breakdown, "failed_gates": [g for g in (a.gates or []) if not g.get("passed")],
                       "net_rr": eco.get("net_rr"), "cost_r": eco.get("cost_r"), "break_even_win_rate_pct": eco.get("break_even_win_rate_pct"),
                       "comparable_live_signals": {k: (eco.get("comparable_strategy") or {}).get(k)
                                                   for k in ("n", "win_rate_pct", "expectancy_r", "expectancy_ci95")}} if a else None,
        "time_machine": eco.get("time_machine"),
        "timeframes": tfs,
        "calendar_next_24h": report.upcoming_news(db, sig.symbol, 24, ("High", "Medium"))[:8],
        "headlines_24h": heads,
        "session": report.sessions_now().get("liquidity"),
    }


def _horizon(db, sig) -> str:
    from ..models import SignalAssessment
    a = db.get(SignalAssessment, sig.id)
    tm = ((a.economics or {}).get("time_machine") if a else None) or {}
    return f"up to {tm.get('median_bars') or 'a few dozen'} {sig.timeframe} candles"


# ------------------------------------------------------------------ run one review
def run(db, sig, *, mode: str, style: str | None = None, timeout: float | None = None):
    """Review one setup now and store it (one row per signal; a new run replaces the old one). Returns the AIReview."""
    from .. import state
    from ..models import AIReview, utcnow
    from ..routers.ai import _call
    from ..secretbox import decrypt
    c = cfg(db)
    style = style if style in STYLES else c.get("style", "debate")
    timeout = float(timeout or c.get("timeout_s", 45))
    ai = state.get(db, "ai")
    rv = db.get(AIReview, sig.id) or AIReview(signal_id=sig.id)
    rv.at, rv.mode, rv.style, rv.provider, rv.model = utcnow(), mode, style, ai.get("provider", ""), ai.get("model", "")
    rv.error, rv.action, rv.calls = "", "NONE", 0
    t0 = time.time()
    try:
        if not ai_ready(db):
            raise RuntimeError("AI assistant is not set up (AI Assistant page)")
        if budget_left(db) <= 0:
            raise RuntimeError(f"daily AI review budget used up ({c.get('max_per_day')} per day)")
        key = decrypt(ai["api_key_enc"])
        data = json.dumps(context(db, sig), default=str)[:12000]
        fmt = {"side": sig.action, "symbol": sig.symbol, "tf": sig.timeframe, "horizon": _horizon(db, sig)}
        msg = lambda extra="": [{"role": "user", "content": f"APP DATA (JSON):\n{data}{extra}"}]  # noqa: E731

        def ask(system, messages, tokens=500):
            return _call(ai["provider"], ai["model"], key, system, messages, timeout=timeout, max_tokens=tokens)

        if style == "debate":
            with ThreadPoolExecutor(max_workers=2) as ex:  # bull and bear argue in parallel
                fb, fr = ex.submit(ask, BULL.format(**fmt), msg()), ex.submit(ask, BEAR.format(**fmt), msg())
                rv.calls = 2
                bull, bear = fb.result(), fr.result()
            rv.bull_case, rv.bear_case = bull[:3000], bear[:3000]
            text = ask(JUDGE.format(**fmt, debate=", the BULL CASE and the BEAR CASE", extra=""),
                       msg(f"\n\nBULL CASE:\n{bull[:2500]}\n\nBEAR CASE:\n{bear[:2500]}"), 450)
            rv.calls = 3
            v = parse_verdict(text)
        else:
            rv.calls = 1
            text = ask(JUDGE.format(**fmt, debate="",
                                    extra=', "bull_case": "the case for, max 80 words", "bear_case": "the case against, max 80 words"'),
                       msg(), 700)
            v = parse_verdict(text)
            rv.bull_case, rv.bear_case = v["bull_case"], v["bear_case"]
        rv.rating, rv.confidence = v["rating"], v["confidence"]
        rv.stance = stance_of(v["rating"], sig.action)
        rv.bull_point, rv.bear_point, rv.change_mind, rv.summary = v["bull_point"], v["bear_point"], v["change_mind"], v["summary"]
    except Exception as e:  # noqa: BLE001 — recorded; VETO mode treats it as a veto
        detail = getattr(e, "detail", None) or str(e)
        rv.rating, rv.stance, rv.confidence, rv.error = "ERROR", "ERROR", 0, str(detail)[:500]
        log.info("AI review of signal %s failed: %s", sig.id, detail)
    rv.seconds = round(time.time() - t0, 1)
    db.add(rv)
    return rv


def as_dict(rv) -> dict | None:
    if rv is None:
        return None
    return {k: getattr(rv, k) for k in ("signal_id", "at", "mode", "style", "provider", "model", "rating", "stance", "confidence",
                                         "bull_case", "bear_case", "bull_point", "bear_point", "change_mind", "summary", "action",
                                         "error", "calls", "seconds")}


# ------------------------------------------------------------------ engine hook (VETO mode)
def gate(db, sig) -> tuple[str, object | None, bool]:
    """Called right before an AUTO trade. Returns (ALLOW | HALF | VETO, review, waited_for_ai)."""
    c = cfg(db)
    if c.get("mode") != "VETO":
        return "ALLOW", None, False
    rv = run(db, sig, mode="VETO")
    review = None if rv.error else {"rating": rv.rating, "stance": rv.stance, "confidence": rv.confidence}
    if rv.error:
        review = {"error": rv.error}
    action, why = decide(review, sig.action, c)
    rv.action = action
    rv.summary = (rv.summary + "\n" if rv.summary else "") + f"Auto-trade decision: {why}"
    db.add(rv)
    return action, rv, True


# ------------------------------------------------------------------ background reviewer (LOG_ONLY, and info in VETO)
def pending_ids(db, limit: int = 3) -> list[int]:
    from sqlalchemy import select

    from ..models import AIReview, Signal, SignalAssessment, utcnow
    q = (select(Signal.id).join(SignalAssessment, SignalAssessment.signal_id == Signal.id)
         .outerjoin(AIReview, AIReview.signal_id == Signal.id)
         .where(AIReview.signal_id.is_(None), Signal.action.in_(["BUY", "SELL"]),
                SignalAssessment.verdict.in_(["PAPER_TEST", "RESEARCH_SUPPORTED"]),
                Signal.created_at >= utcnow() - timedelta(hours=6))
         .order_by(Signal.id.desc()).limit(limit))
    return list(db.scalars(q).all())


def _review_batch(ids: list[int]) -> None:
    from ..db import session_scope
    from ..models import Signal
    with _bg_lock:
        for sid in ids:
            try:
                with session_scope() as db:
                    sig = db.get(Signal, sid)
                    if sig is not None and budget_left(db) > 0:
                        run(db, sig, mode=cfg(db).get("mode", "LOG_ONLY"))
            except Exception:  # noqa: BLE001
                log.exception("background AI review of %s failed", sid)


def scheduled(db) -> None:
    """research_jobs hook: start a background thread so the trading engine never waits for the AI."""
    c = cfg(db)
    if c.get("mode") not in ("LOG_ONLY", "VETO") or not ai_ready(db) or budget_left(db) <= 0 or _bg_lock.locked():
        return
    ids = pending_ids(db, limit=min(3, budget_left(db)))
    if ids:
        threading.Thread(target=_review_batch, args=(ids,), daemon=True).start()


# ------------------------------------------------------------------ is it helping? (forward test)
def stats(db) -> dict:
    from sqlalchemy import func, select

    from ..models import AIReview, SignalOutcome
    rows = db.execute(select(AIReview.stance, SignalOutcome.r_net).join(SignalOutcome, SignalOutcome.signal_id == AIReview.signal_id)
                      .where(SignalOutcome.status.in_(["TARGET", "STOP", "EXPIRED"]), SignalOutcome.r_net.is_not(None))).all()
    groups = {}
    for st in ("AGREE", "NEUTRAL", "OPPOSE"):
        rs = [float(r) for s, r in rows if s == st]
        groups[st] = {"n": len(rs), "win_rate_pct": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1) if rs else None,
                      "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "total_r": round(sum(rs), 2)}
    total = db.scalar(select(func.count()).select_from(AIReview)) or 0
    ag, op = groups["AGREE"], groups["OPPOSE"]
    if ag["n"] < 30 or op["n"] < 15:
        verdict = (f"Not enough resolved reviews yet ({ag['n']} agree, {op['n']} oppose; need about 30 and 15). "
                   "Keep LOG_ONLY running before letting it veto real-money trades.")
        code = "TOO_EARLY"
    elif op["avg_r"] < ag["avg_r"] - 0.1 and op["avg_r"] < 0:
        code = "HELPFUL"
        verdict = (f"Setups the AI opposed averaged {op['avg_r']}R vs {ag['avg_r']}R when it agreed — vetoing them would have "
                   f"avoided {abs(op['total_r'])}R of losses.")
    else:
        code = "NOT_HELPFUL"
        verdict = (f"Opposed setups averaged {op['avg_r']}R vs {ag['avg_r']}R when it agreed — the AI is not separating good from bad "
                   "setups. Leave it in LOG_ONLY or OFF.")
    return {"reviews": total, "resolved": len(rows), "by_stance": groups, "code": code, "verdict": verdict,
            "note": "Outcomes are the app's shadow outcomes (target or stop first, costs in R), traded or not."}
