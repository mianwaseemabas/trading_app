"""Time Machine: how often did a setup like this reach its target before its stop?

Idea taken from day-trader-skill's "Time Machine", rebuilt on the app's own broker candles and strategies.

Two independent answers, both in R so any market compares fairly:

1. **Strategy replay** — every past time the SAME strategy gave the SAME direction on this market/timeframe, with that
   signal's own stop and target. One trade at a time (no overlapping trades), entry at the signal candle close,
   stop assumed first when stop and target are touched in the same candle (conservative). This is the strategy's
   track record on this market.
2. **Market simulation** — 10,000 random price paths built by re-sampling blocks of this market's recent candles,
   scaled to today's volatility (ATR). It knows nothing about the strategy: it is the "random entry" baseline for
   this stop and target distance. Edge = replay odds − simulated odds.

Neither is a prediction. Both describe the past on one data sample.
"""
from __future__ import annotations

import hashlib
import math
import time

import numpy as np
import pandas as pd

MAX_BARS = 150        # a trade that resolves neither way within this many candles counts as EXPIRED
MIN_REPLAY = 20       # fewer past setups than this: no conclusion
SIM_PATHS = 10_000
SIM_LOOKBACK = 1_000  # candles re-sampled for the simulation
BLOCK = 8             # block length for the block bootstrap (keeps some volatility clustering)

_cache: dict = {}


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1)]


def break_even_pct(reward_risk: float, cost_r: float = 0.0) -> float | None:
    """Win rate needed to break even: p·(RR − c) − (1 − p)·(1 + c) = 0  →  p = (1 + c) / (RR + 1)."""
    if not reward_risk or reward_risk <= 0:
        return None
    return round(min(100.0, (1 + max(0.0, cost_r)) / (reward_risk + 1) * 100), 1)


def resolve(high: np.ndarray, low: np.ndarray, close: np.ndarray, start: int, side: str, sl: float, tp: float,
            max_bars: int = MAX_BARS) -> tuple[str, int, float]:
    """Walk candles from `start` (first candle after entry). Returns (TARGET|STOP|EXPIRED|OPEN, bars, exit price)."""
    end = min(len(high), start + max_bars)
    buy = side == "BUY"
    for j in range(start, end):
        if (buy and low[j] <= sl) or (not buy and high[j] >= sl):
            return "STOP", j - start + 1, sl
        if (buy and high[j] >= tp) or (not buy and low[j] <= tp):
            return "TARGET", j - start + 1, tp
    if end - start >= max_bars:
        return "EXPIRED", max_bars, float(close[end - 1])
    return "OPEN", end - start, float("nan")


def replay(ind: pd.DataFrame, cfg: dict, side: str, *, until: int | None = None, max_bars: int = MAX_BARS,
           cost_r: float = 0.0, evaluate=None) -> dict:
    """Strategy replay on a DataFrame returned by strategy.prepare(). Only signals before row `until` are used and
    each one must have resolved before `until`, so a live signal is never judged with its own future."""
    if evaluate is None:
        from .strategy import evaluate
    n = len(ind) if until is None else min(until, len(ind))
    hi, lo, cl = (ind[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
    out = {"TARGET": 0, "STOP": 0, "EXPIRED": 0}
    rs, bars_used, examples, planned = [], [], [], []
    i = 260 if n > 600 else max(50, n // 5)  # skip indicator warm-up
    while i < n - 1:
        r = evaluate(ind, i, cfg)
        if r.action != side or not r.stop_loss or not r.take_profit or not r.entry_ref:
            i += 1
            continue
        status, bars, exit_px = resolve(hi[:n], lo[:n], cl[:n], i + 1, side, float(r.stop_loss), float(r.take_profit), max_bars)
        if status == "OPEN":
            break  # not resolved yet (would need the future)
        risk = abs(r.entry_ref - r.stop_loss)
        rr = ((exit_px - r.entry_ref) if side == "BUY" else (r.entry_ref - exit_px)) / risk if risk else 0.0
        out[status] += 1
        rs.append(rr - cost_r)
        planned.append(abs(r.take_profit - r.entry_ref) / risk if risk else 0.0)
        bars_used.append(bars)
        if len(examples) < 400:
            examples.append({"time": ind.index[i].isoformat(), "result": status, "r": round(rr - cost_r, 2), "bars": bars})
        i += bars + 1  # one trade at a time
    total = sum(out.values())
    avg = sum(rs) / len(rs) if rs else None
    sd = (sum((x - avg) ** 2 for x in rs) / (len(rs) - 1)) ** 0.5 if len(rs) > 1 else None
    # break-even for the PAST trades' own reward/risk (strategies with moving targets differ from the current signal)
    be_past = round(sum((1 + cost_r) / (p + 1) for p in planned if p > 0) / len(planned) * 100, 1) if planned else None
    res = {"n": total, "target_first": out["TARGET"], "stop_first": out["STOP"], "expired": out["EXPIRED"],
           "p_target_pct": round(out["TARGET"] / total * 100, 1) if total else None, "p_target_ci95": wilson(out["TARGET"], total),
           "avg_r_net": round(avg, 3) if avg is not None else None,
           "avg_r_ci95": [round(avg - 1.96 * sd / len(rs) ** 0.5, 3), round(avg + 1.96 * sd / len(rs) ** 0.5, 3)] if sd else None,
           "avg_planned_rr": round(sum(planned) / len(planned), 2) if planned else None, "break_even_pct": be_past,
           "median_bars": int(np.median(bars_used)) if bars_used else None,
           "recent": examples[-12:][::-1], "period": [ind.index[0].isoformat(), ind.index[n - 1].isoformat()] if n else None}
    if total < MIN_REPLAY:
        res["note"] = f"only {total} past setups — at least {MIN_REPLAY} are needed before the odds mean anything"
    return res


def simulate(df: pd.DataFrame, side: str, entry: float, sl: float, tp: float, *, atr_now: float | None = None,
             paths: int = SIM_PATHS, max_bars: int = MAX_BARS, lookback: int = SIM_LOOKBACK, seed: int = 7) -> dict:
    """Block-bootstrap Monte Carlo of candle moves, in ATR units, scaled to today's ATR."""
    d = df.tail(lookback + 15)
    if len(d) < 120:
        return {"paths": 0, "note": "not enough candles to simulate"}
    o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    prev = np.r_[o[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
    a_prev = np.r_[np.nan, atr[:-1]]
    ok = np.isfinite(a_prev) & (a_prev > 0)
    up, dn, mv = ((h - prev) / a_prev)[ok], ((l - prev) / a_prev)[ok], ((c - prev) / a_prev)[ok]
    m = len(mv)
    if m < 60:
        return {"paths": 0, "note": "not enough candles to simulate"}
    unit = atr_now if atr_now and atr_now > 0 else float(atr[-1])
    if not unit or not math.isfinite(unit):
        return {"paths": 0, "note": "volatility (ATR) unavailable"}
    sl_d, tp_d = abs(entry - sl) / unit, abs(tp - entry) / unit
    if sl_d <= 0 or tp_d <= 0:
        return {"paths": 0, "note": "invalid stop or target"}
    rng = np.random.default_rng(seed)
    nblocks = math.ceil(max_bars / BLOCK)
    starts = rng.integers(0, max(1, m - BLOCK), size=(paths, nblocks))
    idx = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]).reshape(paths, -1)[:, :max_bars]
    UP, DN, MV = up[idx].astype(np.float32), dn[idx].astype(np.float32), mv[idx].astype(np.float32)
    base = np.concatenate([np.zeros((paths, 1), np.float32), np.cumsum(MV, axis=1)[:, :-1]], axis=1)
    hi_p, lo_p = base + UP, base + DN
    if side == "BUY":
        sl_hit, tp_hit = lo_p <= -sl_d, hi_p >= tp_d
    else:
        sl_hit, tp_hit = hi_p >= sl_d, lo_p <= -tp_d
    big = max_bars + 1
    first_sl = np.where(sl_hit.any(axis=1), sl_hit.argmax(axis=1), big)
    first_tp = np.where(tp_hit.any(axis=1), tp_hit.argmax(axis=1), big)
    target = (first_tp < first_sl)          # same candle -> stop first (conservative)
    stop = (first_sl <= first_tp) & (first_sl < big)
    expired = ~(target | stop)
    k = int(target.sum())
    res_bars = np.minimum(first_sl, first_tp)[~expired] + 1
    return {"paths": paths, "max_bars": max_bars, "target_first": k, "stop_first": int(stop.sum()), "expired": int(expired.sum()),
            "p_target_pct": round(k / paths * 100, 1), "p_stop_pct": round(int(stop.sum()) / paths * 100, 1),
            "p_expired_pct": round(int(expired.sum()) / paths * 100, 1),
            "median_bars": int(np.median(res_bars)) if len(res_bars) else None,
            "stop_atr": round(sl_d, 2), "target_atr": round(tp_d, 2), "sampled_candles": m}


def verdict(rep: dict | None, sim: dict | None, be: float | None, rr: float | None = None) -> dict:
    """Plain-words reading. BELOW_BREAK_EVEN only when the whole 95% range of the replay is below break-even."""
    if be is None:
        return {"code": "UNKNOWN", "text": "Break-even win rate unavailable (stop or target missing)."}
    if not rep or rep.get("n", 0) < MIN_REPLAY:
        s = f" Random-entry simulation: {sim['p_target_pct']}% target first." if sim and sim.get("paths") else ""
        return {"code": "TOO_FEW", "text": f"Too few past setups to judge (need {MIN_REPLAY}). Break-even is {be}%.{s}"}
    be = rep.get("break_even_pct") or be
    lo, hi = rep["p_target_ci95"]
    same_shape = bool(rr and rep.get("avg_planned_rr") and abs(rep["avg_planned_rr"] - rr) / rr <= 0.15)
    # "vs random" is only fair when past trades had about the same stop/target shape as this one
    edge = round(rep["p_target_pct"] - sim["p_target_pct"], 1) if sim and sim.get("paths") and same_shape else None
    edge_txt = f" That is {edge:+} points vs random entries." if edge is not None else ""
    if lo > be:
        return {"code": "ABOVE_BREAK_EVEN", "edge_pts": edge,
                "text": f"Past setups hit the target first {rep['p_target_pct']}% of the time (95% range {lo}–{hi}%), above the {be}% needed.{edge_txt}"}
    if hi < be:
        return {"code": "BELOW_BREAK_EVEN", "edge_pts": edge,
                "text": f"Past setups hit the target first only {rep['p_target_pct']}% (95% range {lo}–{hi}%), below the {be}% needed to break even.{edge_txt}"}
    return {"code": "UNCLEAR", "edge_pts": edge,
            "text": f"Past setups: {rep['p_target_pct']}% target first (95% range {lo}–{hi}%) — break-even {be}% is inside that range, so no clear edge.{edge_txt}"}


def analyze(df: pd.DataFrame, cfg: dict | None, side: str, entry: float, sl: float, tp: float, *, cost_r: float = 0.0,
            seed: int = 7, paths: int = SIM_PATHS, until: int | None = None) -> dict:
    """Pure entry point (no database): df = broker candles (oldest first), cfg = strategy config or None for manual."""
    t0 = time.time()
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk if risk else None
    be = break_even_pct(rr, cost_r) if rr else None
    rep = None
    atr_now = None
    if cfg and cfg.get("name") and cfg["name"] not in ("manual", "news_breakout"):
        from .strategy import prepare
        ind = prepare(df, cfg)
        rep = replay(ind, cfg, side, until=until, cost_r=cost_r)
        if "atr" in ind and until is None:
            v = ind["atr"].iloc[-1]
            atr_now = float(v) if v == v else None
    sim = simulate(df if until is None else df.iloc[:until], side, entry, sl, tp, atr_now=atr_now, paths=paths, seed=seed)
    return {"side": side, "entry": entry, "stop": sl, "target": tp, "reward_risk": round(rr, 2) if rr else None,
            "cost_r": round(cost_r, 3), "break_even_pct": be, "strategy": (cfg or {}).get("name") or "manual",
            "replay": rep, "simulation": sim, "verdict": verdict(rep, sim, be, rr), "max_bars": MAX_BARS,
            "seconds": round(time.time() - t0, 2),
            "honesty": "Describes the past on this market only. Not a prediction; markets change. Stop assumed first when both are hit in one candle."}


# --------------------------------------------------------------------------- database wrappers
def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def for_signal(db, sig, *, cost_r: float | None = None, use_cache: bool = True) -> dict | None:
    """Time Machine for a stored strategy signal (cached per signal for 6 hours)."""
    from .. import state
    from . import market_data as md
    if sig.action not in ("BUY", "SELL") or not (sig.entry_ref and sig.stop_loss and sig.take_profit) or sig.timeframe not in md.TF_SECONDS:
        return None
    key = ("sig", sig.id)
    hit = _cache.get(key)
    if use_cache and hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    tm = state.get(db, "timemachine")
    df = md.load_candles(db, sig.symbol, sig.timeframe, end=sig.candle_time, limit=int(tm.get("max_candles", 6000)))
    if len(df) < 300:
        return {"verdict": {"code": "TOO_FEW", "text": f"only {len(df)} candles stored for {sig.symbol} {sig.timeframe}"}}
    if cost_r is None:
        from ..models import SignalAssessment
        a = db.get(SignalAssessment, sig.id)
        cost_r = float(((a.economics or {}).get("cost_r") if a else 0) or 0)
    cfg = {**state.get(db, "strategy"), "name": sig.strategy}
    out = analyze(df, cfg, sig.action, float(sig.entry_ref), float(sig.stop_loss), float(sig.take_profit), cost_r=cost_r,
                  seed=_seed(sig.id, sig.symbol), paths=int(tm.get("paths", SIM_PATHS)))
    out["symbol"], out["timeframe"], out["signal_id"] = sig.symbol, sig.timeframe, sig.id
    _cache[key] = (time.time(), out)
    if len(_cache) > 500:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
            _cache.pop(k, None)
    return out


def for_manual(db, symbol: str, tf: str, side: str, entry: float, sl: float, tp: float, strategy: str | None = None) -> dict:
    from .. import state
    from . import market_data as md
    tm = state.get(db, "timemachine")
    df = md.load_candles(db, symbol, tf, limit=int(tm.get("max_candles", 6000)))
    if len(df) < 300:
        return {"verdict": {"code": "TOO_FEW", "text": f"only {len(df)} candles stored for {symbol} {tf}"}}
    cfg = {**state.get(db, "strategy"), "name": strategy} if strategy else None
    out = analyze(df, cfg, side, entry, sl, tp, seed=_seed(symbol, tf, side, entry, sl, tp), paths=int(tm.get("paths", SIM_PATHS)))
    out["symbol"], out["timeframe"] = symbol, tf
    return out
