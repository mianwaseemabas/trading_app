"""Smart Money Concepts (SMC) — measurable, testable definitions.

Every concept below is a *research definition* (a test parameter), not a market
law. The analyser walks the candles once, in time order, and only ever uses
candles up to the current one, so its output is free of look-ahead bias and
can be used in backtests exactly as it would have been seen live.

Definitions (n = swing_n, default 3):
* Swing high  — candle j whose high is higher than the n candles before it and
  not exceeded by the n candles after it. It is only *confirmed* (and usable)
  n candles later.  Swing low is the mirror image.
* BSL / SSL   — buy-side liquidity above unbroken confirmed swing highs;
  sell-side liquidity below unbroken confirmed swing lows.
* Sweep       — a candle trades beyond an unbroken swing level but CLOSES back
  on the original side (e.g. high > swing high, close < swing high = BSL sweep).
* Break (BOS / CHoCH) — a candle CLOSES beyond the most recent unbroken swing
  level. If it is in the direction of the current structure it is a BOS;
  against it, a CHoCH (change of character, also called MSS).
* Displacement — a candle whose body ≥ disp_atr × ATR and whose body is at least
  half of its range.
* Fair value gap (FVG) — three-candle imbalance: bullish when low[i] > high[i-2],
  bearish when high[i] < low[i-2], gap ≥ fvg_min_atr × ATR.
* Order block (OB) — the last opposite-colour candle (within 10 candles) before
  the candle that made a structure break; zone = that candle's high/low.
"""
from __future__ import annotations

import math

import pandas as pd

DEFAULTS = {"smc_swing_n": 3, "smc_displacement_atr": 1.2, "smc_fvg_min_atr": 0.1}


def _p(cfg: dict, k: str):
    return cfg.get(k, DEFAULTS[k])


def analyze(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    """Return detected events (in detection order) and the current market state.

    df needs open/high/low/close and an 'atr' column (see indicators.add_indicators).
    Each event has `i` (index of the candle on which it became known) and `time`.
    """
    cfg = cfg or {}
    n = int(_p(cfg, "smc_swing_n"))
    disp_k = float(_p(cfg, "smc_displacement_atr"))
    fvg_k = float(_p(cfg, "smc_fvg_min_atr"))
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr = df["atr"].to_numpy(dtype=float) if "atr" in df else [float("nan")] * len(df)
    times = list(df.index)
    events: list[dict] = []
    highs: list[dict] = []  # confirmed swing highs {i, price, broken, swept}
    lows: list[dict] = []
    fvgs: list[dict] = []
    trend = 0  # 1 bullish structure, -1 bearish, 0 unknown

    def ev(i, kind, direction, **kw):
        e = {"i": i, "time": times[i], "kind": kind, "dir": direction, **kw}
        events.append(e)
        return e

    for i in range(len(df)):
        a = atr[i] if i < len(atr) and not math.isnan(atr[i]) else None
        # 1) confirm swings whose pivot is n candles back
        j = i - n
        if j >= n:
            if h[j] > max(h[j - n:j]) and h[j] >= max(h[j + 1:i + 1]):
                highs.append({"i": j, "price": h[j], "broken": False, "swept": False})
                ev(i, "swing_high", "bear", price=h[j], pivot_time=times[j])
            if l[j] < min(l[j - n:j]) and l[j] <= min(l[j + 1:i + 1]):
                lows.append({"i": j, "price": l[j], "broken": False, "swept": False})
                ev(i, "swing_low", "bull", price=l[j], pivot_time=times[j])
        if len(highs) > 60:
            del highs[:-60]
        if len(lows) > 60:
            del lows[:-60]
        # 2) displacement
        body, rng = abs(c[i] - o[i]), h[i] - l[i]
        if a and body >= disp_k * a and rng > 0 and body / rng >= 0.5:
            ev(i, "displacement", "bull" if c[i] > o[i] else "bear", size_atr=round(body / a, 2))
        # 3) fair value gaps
        if i >= 2 and a:
            if l[i] - h[i - 2] >= fvg_k * a:
                fvgs.append(ev(i, "fvg", "bull", zone_low=h[i - 2], zone_high=l[i], filled=False))
            elif l[i - 2] - h[i] >= fvg_k * a:
                fvgs.append(ev(i, "fvg", "bear", zone_low=h[i], zone_high=l[i - 2], filled=False))
        for f in fvgs:
            if f["i"] < i and ((f["dir"] == "bull" and l[i] <= f["zone_low"]) or (f["dir"] == "bear" and h[i] >= f["zone_high"])):
                f["filled"] = True
        if len(fvgs) > 40 or any(f["filled"] for f in fvgs):
            fvgs[:] = [f for f in fvgs if not f["filled"]][-40:]
        # 4) structure breaks (close beyond the most recent unbroken swing)
        sh = next((s for s in reversed(highs) if not s["broken"]), None)
        sl = next((s for s in reversed(lows) if not s["broken"]), None)
        if sh and c[i] > sh["price"]:
            for s in highs:
                if s["price"] < c[i]:
                    s["broken"] = True
            kind = "choch" if trend == -1 else "bos"
            trend = 1
            ob = next((k for k in range(i - 1, max(-1, i - 11), -1) if c[k] < o[k]), None)
            ev(i, kind, "bull", level=sh["price"], swing_time=times[sh["i"]],
               ob_low=l[ob] if ob is not None else None, ob_high=h[ob] if ob is not None else None)
        elif sl and c[i] < sl["price"]:
            for s in lows:
                if s["price"] > c[i]:
                    s["broken"] = True
            kind = "choch" if trend == 1 else "bos"
            trend = -1
            ob = next((k for k in range(i - 1, max(-1, i - 11), -1) if c[k] > o[k]), None)
            ev(i, kind, "bear", level=sl["price"], swing_time=times[sl["i"]],
               ob_low=l[ob] if ob is not None else None, ob_high=h[ob] if ob is not None else None)
        else:
            # 5) liquidity sweeps (wick beyond, close back inside)
            for s in reversed(highs[-5:]):
                if not s["broken"] and not s["swept"] and s["i"] < i and h[i] > s["price"] > c[i]:
                    s["swept"] = True
                    ev(i, "sweep_bsl", "bear", level=s["price"], extreme=h[i], swing_time=times[s["i"]])
                    break
            for s in reversed(lows[-5:]):
                if not s["broken"] and not s["swept"] and s["i"] < i and l[i] < s["price"] < c[i]:
                    s["swept"] = True
                    ev(i, "sweep_ssl", "bull", level=s["price"], extreme=l[i], swing_time=times[s["i"]])
                    break

    state = {
        "trend": {1: "bullish", -1: "bearish", 0: "undefined"}[trend],
        "bsl": sorted({round(s["price"], 5) for s in highs if not s["broken"]})[:3],   # nearest above first
        "ssl": sorted({round(s["price"], 5) for s in lows if not s["broken"]})[-3:][::-1],  # nearest below first
        "open_fvgs": [{k: f[k] for k in ("time", "dir", "zone_low", "zone_high")} for f in fvgs][-6:],
    }
    return {"events": events, "state": state}


def signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """SMC sweep → structure break → retest strategy, computed bar by bar.

    LONG sequence (SHORT is the mirror):
      1. A sell-side liquidity sweep (sweep_ssl) happens.
      2. Within `smc_bos_window` candles a bullish BOS/CHoCH closes above a swing
         high, with at least one bullish displacement candle since the sweep.
      3. The entry zone is the newest bullish FVG formed since the sweep, or
         else the order block of the break.
      4. Within `smc_retest_window` candles price returns into the zone
         (low ≤ zone top) and closes above the zone bottom → BUY at the close.
      Stop = lowest low since the sweep − 0.1 ATR; target = reward_risk × stop.
      Rejected if the stop is < 0.3 ATR or > smc_max_stop_atr ATR, or (with the
      trend filter on) if the close is below EMA(slow).
    Adds columns smc_signal (1/-1/0), smc_sl, smc_tp, smc_info.
    """
    res = analyze(df, cfg)
    by_i: dict[int, list] = {}
    for e in res["events"]:
        by_i.setdefault(e["i"], []).append(e)
    h, l, c = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    atr = df["atr"].to_numpy(dtype=float)
    ema_s = df["ema_slow"].to_numpy(dtype=float) if "ema_slow" in df else None
    bos_w, ret_w = int(cfg.get("smc_bos_window", 20)), int(cfg.get("smc_retest_window", 15))
    max_stop, rr = float(cfg.get("smc_max_stop_atr", 3.0)), float(cfg.get("smc_reward_risk", cfg.get("reward_risk", 2.0)))
    trend_filter = bool(cfg.get("smc_trend_filter", True))
    sig = [0] * len(df)
    sl_col: list = [None] * len(df)
    tp_col: list = [None] * len(df)
    info: list = [None] * len(df)
    armed = {"bull": None, "bear": None}
    setup = {"bull": None, "bear": None}
    last_fvg = {"bull": None, "bear": None}
    last_disp = {"bull": -10**9, "bear": -10**9}
    for i in range(len(df)):
        for e in by_i.get(i, []):
            d = e["dir"]
            if e["kind"] == "sweep_ssl":
                armed["bull"] = {"i": i, "level": e["level"], "time": e["time"]}
            elif e["kind"] == "sweep_bsl":
                armed["bear"] = {"i": i, "level": e["level"], "time": e["time"]}
            elif e["kind"] == "displacement":
                last_disp[d] = i
            elif e["kind"] == "fvg":
                last_fvg[d] = e
            elif e["kind"] in ("bos", "choch"):
                a = armed[d]
                if a and i - a["i"] <= bos_w and last_disp[d] >= a["i"]:
                    f = last_fvg[d] if last_fvg[d] and last_fvg[d]["i"] >= a["i"] else None
                    zone = (f["zone_low"], f["zone_high"], "FVG") if f else (e.get("ob_low"), e.get("ob_high"), "order block")
                    if zone[0] is not None:
                        ext = min(l[a["i"]:i + 1]) if d == "bull" else max(h[a["i"]:i + 1])
                        setup[d] = {"i": i, "zone": zone, "extreme": ext, "sweep": a, "break": e}
                    armed[d] = None
                other = "bear" if d == "bull" else "bull"
                setup[other] = None  # an opposite break cancels the other side's idea
        a_v = atr[i]
        if math.isnan(a_v):
            continue
        for d, s in setup.items():
            if not s or i <= s["i"]:
                continue
            if i - s["i"] > ret_w or (d == "bull" and c[i] < s["extreme"]) or (d == "bear" and c[i] > s["extreme"]):
                setup[d] = None
                continue
            zl, zh, zname = s["zone"]
            touched = (l[i] <= zh and c[i] > zl) if d == "bull" else (h[i] >= zl and c[i] < zh)
            if not touched:
                continue
            setup[d] = None
            entry = c[i]
            stop = (s["extreme"] - 0.1 * a_v) if d == "bull" else (s["extreme"] + 0.1 * a_v)
            dist = abs(entry - stop)
            checks = [
                {"check": f"liquidity sweep of {'SSL' if d == 'bull' else 'BSL'} at {s['sweep']['level']:.5f}", "passed": True},
                {"check": f"{s['break']['kind'].upper()} {'above' if d == 'bull' else 'below'} {s['break']['level']:.5f} with displacement", "passed": True},
                {"check": f"retest of {zname} {zl:.5f}–{zh:.5f}", "passed": True},
                {"check": f"stop distance {dist / a_v:.2f} ATR within 0.3–{max_stop}", "passed": 0.3 * a_v <= dist <= max_stop * a_v},
            ]
            if trend_filter and ema_s is not None and not math.isnan(ema_s[i]):
                checks.append({"check": "trend filter: close vs EMA slow", "passed": c[i] > ema_s[i] if d == "bull" else c[i] < ema_s[i]})
            info[i] = {"dir": d, "checks": checks, "zone": [zl, zh, zname], "sweep_time": str(s["sweep"]["time"]),
                       "break_time": str(s["break"]["time"])}
            if all(x["passed"] for x in checks):
                sig[i] = 1 if d == "bull" else -1
                sl_col[i] = stop
                tp_col[i] = entry + rr * dist if d == "bull" else entry - rr * dist
    out = df.copy()
    out["smc_signal"], out["smc_sl"], out["smc_tp"], out["smc_info"] = sig, sl_col, tp_col, info
    return out
