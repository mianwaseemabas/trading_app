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
}


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
    stop = stop_mult * atr
    if long and all(ok for _, ok in long):
        return "BUY", c, c - stop, c + rr * stop, feats, [{"side": "BUY", "check": n, "passed": True} for n, _ in long]
    if short and all(ok for _, ok in short):
        return "SELL", c, c + stop, c - rr * stop, feats, [{"side": "SELL", "check": n, "passed": True} for n, _ in short]
    reasons = [{"side": "BUY", "check": n, "passed": bool(ok)} for n, ok in long] + [{"side": "SELL", "check": n, "passed": bool(ok)} for n, ok in short]
    return (*none, reasons)
