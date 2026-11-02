"""Additional, well-known strategy families (crypto and forex), written as testable rules.

None of these has a reliably "high win rate AND profit". Each trades one off for the other:
* Trend / breakout systems usually win 30–45% of trades but the winners are larger than the losers.
* Mean-reversion systems often win 55–70% of trades but each win is small; a few big losses can erase them.
Which one works on which market and period is decided by the Strategy lab (backtest with costs
+ untouched final-test period) and by shadow tracking of live signals — never by the name.

All rules use closed candles only and information available at that candle (no look-ahead).
"""
from __future__ import annotations

import math

import pandas as pd

from .indicators import ema, rsi


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    pdi = 100 * plus.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi)).replace([math.inf, -math.inf], float("nan"))
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


INFO = {
    "donchian_breakout": {
        "label": "Donchian breakout (trend following)",
        "family": "trend", "typical_win_rate": "30–45%", "payoff": "large winners (target 2.5R)",
        "rules": "BUY when the close breaks above the highest high of the previous 20 candles, price is above EMA 200 and "
                 "ADX ≥ 20. Stop 2×ATR below entry, target 2.5×stop. SELL is the mirror image (not used on spot crypto).",
        "best_when": "strong trending markets (crypto bull runs, trending FX pairs)", "weak_when": "sideways chop: many small false breakouts"},
    "rsi2_meanrev": {
        "label": "RSI-2 pullback in an uptrend (mean reversion)",
        "family": "mean reversion", "typical_win_rate": "55–70%", "payoff": "small winners (target ≈1.25R)",
        "rules": "BUY when price is above EMA 200 (uptrend) and the 2-period RSI drops below 10 (short-term oversold). "
                 "Stop 1.2×ATR, target 1.5×ATR. SELL mirror when below EMA 200 and RSI-2 above 90.",
        "best_when": "steady trends with shallow pullbacks", "weak_when": "crashes: buying dips can keep losing"},
    "bb_squeeze": {
        "label": "Bollinger squeeze breakout (volatility expansion)",
        "family": "breakout", "typical_win_rate": "35–50%", "payoff": "target 2R",
        "rules": "After Bollinger band width (20, 2σ) was in its lowest 20% of the last 120 candles within the last 5 candles, "
                 "BUY when the close breaks above the upper band and EMA 50 is rising. Stop 1.5×ATR, target 2×stop.",
        "best_when": "quiet periods that end in a strong move", "weak_when": "fake-outs right after news"},
    "ema_cross_adx": {
        "label": "EMA 9/21 cross with ADX filter (momentum)",
        "family": "momentum", "typical_win_rate": "35–45%", "payoff": "target 2R",
        "rules": "BUY when EMA 9 crosses above EMA 21, MACD line > 0 and ADX ≥ 20. Stop 1.5×ATR, target 2×stop. SELL mirror.",
        "best_when": "clear momentum phases", "weak_when": "ranges: frequent whipsaws"},
    "london_breakout": {
        "label": "London open breakout of the Asian range (session breakout)",
        "family": "breakout", "typical_win_rate": "40–50%", "payoff": "target 1.5R",
        "rules": "Asian range = high/low of 00:00–07:00 UTC. Between 07:00 and 10:00 UTC, BUY on the first close above the range high "
                 "(SELL below the low). Skipped when the range is unusually wide or narrow (0.3–2× the ATR scaled to 7 hours). Stop at the middle of the "
                 "range (at least 1×ATR), target 1.5×stop. Use on M15/H1 forex pairs (EUR, GBP, CHF crosses).",
        "best_when": "normal London sessions after a quiet Asian session", "weak_when": "holiday sessions, big news at the open"},
    "supertrend_ema": {
        "label": "Supertrend flip with EMA 200 filter (trend following)",
        "family": "trend", "typical_win_rate": "35–45%", "payoff": "target 2R",
        "rules": "Supertrend (ATR 10, multiplier 3). BUY when it flips to up-trend while the close is above EMA 200 (SELL mirror). "
                 "Stop at the Supertrend line (between 1 and 3×ATR), target 2×stop.",
        "best_when": "sustained trends (gold, JPY crosses, crypto)", "weak_when": "tight ranges: flips back and forth"},
    "ichimoku_tk": {
        "label": "Ichimoku Tenkan/Kijun cross above/below the cloud",
        "family": "trend", "typical_win_rate": "35–50%", "payoff": "target 2R",
        "rules": "Ichimoku 9/26/52. BUY when Tenkan crosses above Kijun, the close is above the cloud (Senkou A and B, shifted 26), "
                 "and the close is above the close of 26 candles ago (Chikou confirmation). SELL mirror. Stop 1.5×ATR or beyond the "
                 "Kijun (whichever is further, max 3×ATR), target 2×stop.",
        "best_when": "trending FX majors and JPY crosses on H1/H4", "weak_when": "price inside the cloud (no trade by rule)"},
}


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """Classic Supertrend. Returns (line, direction) with direction +1 up / -1 down. Uses only past and current candles."""
    hl2 = (df["high"] + df["low"]) / 2
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    up_b, lo_b = (hl2 + mult * atr).to_numpy(), (hl2 - mult * atr).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    line, dirn = [float("nan")] * n, [0] * n
    fu, fl, d = float("nan"), float("nan"), 1
    for i in range(n):
        if math.isnan(up_b[i]):
            continue
        if math.isnan(fu):
            fu, fl = up_b[i], lo_b[i]
        else:
            fu = up_b[i] if (up_b[i] < fu or close[i - 1] > fu) else fu
            fl = lo_b[i] if (lo_b[i] > fl or close[i - 1] < fl) else fl
            if d == 1 and close[i] < fl:
                d = -1
            elif d == -1 and close[i] > fu:
                d = 1
        line[i], dirn[i] = (fl if d == 1 else fu), d
    return pd.Series(line, index=df.index), pd.Series(dirn, index=df.index)


def _asian_range(df: pd.DataFrame) -> pd.DataFrame:
    """Per candle: the 00:00–07:00 UTC high/low of the same UTC day, known only from 07:00 on (no look-ahead)."""
    idx = df.index.tz_convert("UTC") if df.index.tz is not None else df.index
    day = idx.normalize()
    asian = idx.hour < 7
    hi = df["high"].where(asian).groupby(day).transform("max")
    lo = df["low"].where(asian).groupby(day).transform("min")
    known = ~asian
    return pd.DataFrame({"asia_hi": hi.where(known), "asia_lo": lo.where(known), "hour": idx.hour, "day": day}, index=df.index)


def prepare(ind: pd.DataFrame, name: str) -> pd.DataFrame:
    out = ind.copy()
    out["adx"] = adx(out)
    if name == "donchian_breakout":
        out["dc_high"] = out["high"].rolling(20).max().shift(1)
        out["dc_low"] = out["low"].rolling(20).min().shift(1)
        out["ema200"] = ema(out["close"], 200)
    elif name == "rsi2_meanrev":
        out["rsi2"] = rsi(out["close"], 2)
        out["ema200"] = ema(out["close"], 200)
    elif name == "bb_squeeze":
        mid = out["close"].rolling(20).mean()
        sd = out["close"].rolling(20).std()
        out["bb_up"], out["bb_lo"] = mid + 2 * sd, mid - 2 * sd
        bw = (4 * sd) / mid
        out["bw_rank"] = bw.rolling(120).apply(lambda w: (w < w[-1]).mean(), raw=True)
        out["squeeze_recent"] = (out["bw_rank"] <= 0.2).rolling(5).max().shift(1)
        out["ema50"] = ema(out["close"], 50)
    elif name == "ema_cross_adx":
        out["ema9"], out["ema21"] = ema(out["close"], 9), ema(out["close"], 21)
    elif name == "london_breakout":
        ar = _asian_range(out)
        out["asia_hi"], out["asia_lo"], out["hour_utc"] = ar["asia_hi"], ar["asia_lo"], ar["hour"]
        step = out.index.to_series().diff().median()
        out["asia_bars"] = max(1.0, 7 * 3600 / step.total_seconds()) if pd.notna(step) and step.total_seconds() > 0 else 7.0
        # did an earlier candle of today's London window already close outside the range? (first breakout only)
        inside_win = (ar["hour"] >= 7) & (ar["hour"] < 10)
        brk_up = (out["close"] > out["asia_hi"]) & inside_win
        brk_dn = (out["close"] < out["asia_lo"]) & inside_win
        out["prior_up"] = brk_up.groupby(ar["day"]).transform(lambda s: s.shift(1, fill_value=False).cummax())
        out["prior_dn"] = brk_dn.groupby(ar["day"]).transform(lambda s: s.shift(1, fill_value=False).cummax())
    elif name == "supertrend_ema":
        out["st_line"], out["st_dir"] = supertrend(out)
        out["ema200"] = ema(out["close"], 200)
    elif name == "ichimoku_tk":
        h, low = out["high"], out["low"]
        out["tenkan"] = (h.rolling(9).max() + low.rolling(9).min()) / 2
        out["kijun"] = (h.rolling(26).max() + low.rolling(26).min()) / 2
        # cloud plotted 26 ahead -> the cloud *at* this candle was computed 26 candles ago (past data only)
        out["span_a"] = ((out["tenkan"] + out["kijun"]) / 2).shift(26)
        out["span_b"] = ((h.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
        out["close_26"] = out["close"].shift(26)
    return out


def _f(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def evaluate(df: pd.DataFrame, i: int, name: str):
    """Return (action, entry, sl, tp, features, reasons)."""
    r, p = df.iloc[i], df.iloc[i - 1] if i > 0 else None
    c, atr, adx_v = _f(r["close"]), _f(r.get("atr")), _f(r.get("adx"))
    feats = {"close": c, "atr": atr, "adx": adx_v, "rsi": _f(r.get("rsi"))}
    none = ("NONE", None, None, None, feats)
    if c is None or atr is None or p is None:
        return (*none, [{"check": "indicators ready", "passed": False}])
    long, short, rr, stop_mult = [], [], 2.0, 1.5
    if name == "donchian_breakout":
        hi, lo, e200 = _f(r["dc_high"]), _f(r["dc_low"]), _f(r["ema200"])
        if None in (hi, lo, e200):
            return (*none, [{"check": "indicators ready", "passed": False}])
        long = [("close breaks 20-candle high", c > hi), ("above EMA 200", c > e200), ("ADX ≥ 20", (adx_v or 0) >= 20)]
        short = [("close breaks 20-candle low", c < lo), ("below EMA 200", c < e200), ("ADX ≥ 20", (adx_v or 0) >= 20)]
        rr, stop_mult = 2.5, 2.0
    elif name == "rsi2_meanrev":
        r2, e200 = _f(r["rsi2"]), _f(r["ema200"])
        if None in (r2, e200):
            return (*none, [{"check": "indicators ready", "passed": False}])
        long = [("uptrend (above EMA 200)", c > e200), ("RSI-2 below 10", r2 < 10)]
        short = [("downtrend (below EMA 200)", c < e200), ("RSI-2 above 90", r2 > 90)]
        rr, stop_mult = 1.25, 1.2
        feats["rsi2"] = r2
    elif name == "bb_squeeze":
        up, lo, sq, e50, e50p = _f(r["bb_up"]), _f(r["bb_lo"]), _f(r["squeeze_recent"]), _f(r["ema50"]), _f(p["ema50"])
        if None in (up, lo, sq, e50, e50p):
            return (*none, [{"check": "indicators ready", "passed": False}])
        long = [("recent squeeze (low band width)", sq >= 1), ("close above upper band", c > up), ("EMA 50 rising", e50 > e50p)]
        short = [("recent squeeze (low band width)", sq >= 1), ("close below lower band", c < lo), ("EMA 50 falling", e50 < e50p)]
    elif name == "ema_cross_adx":
        e9, e21, pe9, pe21, macd = _f(r["ema9"]), _f(r["ema21"]), _f(p["ema9"]), _f(p["ema21"]), _f(r.get("macd"))
        if None in (e9, e21, pe9, pe21, macd):
            return (*none, [{"check": "indicators ready", "passed": False}])
        long = [("EMA 9 crosses above EMA 21", e9 > e21 and pe9 <= pe21), ("MACD above 0", macd > 0), ("ADX ≥ 20", (adx_v or 0) >= 20)]
        short = [("EMA 9 crosses below EMA 21", e9 < e21 and pe9 >= pe21), ("MACD below 0", macd < 0), ("ADX ≥ 20", (adx_v or 0) >= 20)]
    elif name == "london_breakout":
        hi, lo, hr = _f(r["asia_hi"]), _f(r["asia_lo"]), _f(r["hour_utc"])
        if None in (hi, lo, hr):
            return (*none, [{"check": "Asian range known (after 07:00 UTC)", "passed": False}])
        rng = hi - lo
        window = 7 <= hr < 10
        k = math.sqrt(_f(r.get("asia_bars")) or 7.0)  # a 7-hour range spans ~sqrt(candles) × the per-candle ATR
        size_ok = 0.3 * k * atr <= rng <= 2.0 * k * atr
        feats.update(asia_hi=hi, asia_lo=lo, asia_range_atr=round(rng / atr, 2) if atr else None)
        long = [("London window 07–10 UTC", window), ("Asian range normal size", size_ok), ("close above Asian high", c > hi),
                ("first breakout today", not bool(r["prior_up"]))]
        short = [("London window 07–10 UTC", window), ("Asian range normal size", size_ok), ("close below Asian low", c < lo),
                 ("first breakout today", not bool(r["prior_dn"]))]
        rr = 1.5
        mid = (hi + lo) / 2
        stop_mult = max(1.0, abs(c - mid) / atr)
    elif name == "supertrend_ema":
        ln, d, pd_, e200 = _f(r["st_line"]), _f(r["st_dir"]), _f(p["st_dir"]), _f(r["ema200"])
        if None in (ln, d, pd_, e200) or d == 0 or pd_ == 0:
            return (*none, [{"check": "indicators ready", "passed": False}])
        feats.update(supertrend=ln, ema200=e200)
        long = [("Supertrend flips up", d == 1 and pd_ == -1), ("above EMA 200", c > e200)]
        short = [("Supertrend flips down", d == -1 and pd_ == 1), ("below EMA 200", c < e200)]
        stop_mult = min(3.0, max(1.0, abs(c - ln) / atr))
    elif name == "ichimoku_tk":
        tk, kj, ptk, pkj = _f(r["tenkan"]), _f(r["kijun"]), _f(p["tenkan"]), _f(p["kijun"])
        sa, sb, c26 = _f(r["span_a"]), _f(r["span_b"]), _f(r["close_26"])
        if None in (tk, kj, ptk, pkj, sa, sb, c26):
            return (*none, [{"check": "indicators ready", "passed": False}])
        top, bot = max(sa, sb), min(sa, sb)
        feats.update(tenkan=tk, kijun=kj, cloud_top=top, cloud_bottom=bot)
        long = [("Tenkan crosses above Kijun", tk > kj and ptk <= pkj), ("close above the cloud", c > top), ("Chikou: close above close 26 ago", c > c26)]
        short = [("Tenkan crosses below Kijun", tk < kj and ptk >= pkj), ("close below the cloud", c < bot), ("Chikou: close below close 26 ago", c < c26)]
        stop_mult = min(3.0, max(1.5, (abs(c - kj) / atr) * 1.1))
    stop = stop_mult * atr
    if long and all(ok for _, ok in long):
        return "BUY", c, c - stop, c + rr * stop, feats, [{"side": "BUY", "check": n, "passed": True} for n, _ in long]
    if short and all(ok for _, ok in short):
        return "SELL", c, c + stop, c - rr * stop, feats, [{"side": "SELL", "check": n, "passed": True} for n, _ in short]
    reasons = [{"side": "BUY", "check": n, "passed": bool(ok)} for n, ok in long] + [{"side": "SELL", "check": n, "passed": bool(ok)} for n, ok in short]
    return (*none, reasons)
