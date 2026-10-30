"""Strategy: trend_pullback v1 — a documented, testable hypothesis (NOT a proven edge).

Evaluated only on CLOSED candles.

LONG when all are true on the signal candle:
  1. EMA(fast) > EMA(slow)                       — uptrend context
  2. close > EMA(slow)                           — price on the trend side
  3. MACD histogram crosses above 0 on this bar  — momentum turning up
  4. rsi_long_min <= RSI <= rsi_long_max         — momentum, not overbought
  5. ATR / close <= max_atr_pct %                — volatility not abnormal
SHORT is the mirror image (EMA fast < slow, close < EMA slow, histogram crosses
below 0, rsi_short_min <= RSI <= rsi_short_max).

Stop-loss  = entry ∓ atr_stop_mult × ATR
Take-profit = entry ± reward_risk × stop distance
Entry reference = close of the signal candle; the actual order is placed at
market on the next tick and re-validated by the risk engine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

NAME = "trend_pullback"


@dataclass
class StrategyResult:
    action: str  # BUY | SELL | NONE
    entry_ref: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    features: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def _f(x) -> float | None:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def evaluate(df: pd.DataFrame, i: int, cfg: dict) -> StrategyResult:
    """Evaluate the strategy on row position i of an indicator DataFrame."""
    if i < 1 or i >= len(df):
        return StrategyResult("NONE", reasons=[{"check": "history", "passed": False, "value": "insufficient"}])
    row, prev = df.iloc[i], df.iloc[i - 1]
    close = _f(row["close"])
    ema_f, ema_s = _f(row["ema_fast"]), _f(row["ema_slow"])
    rsi_v, atr_v = _f(row["rsi"]), _f(row["atr"])
    hist, prev_hist = _f(row["macd_hist"]), _f(prev["macd_hist"])

    features = {
        "close": close,
        "ema_fast": ema_f,
        "ema_slow": ema_s,
        "rsi": rsi_v,
        "macd": _f(row["macd"]),
        "macd_signal": _f(row["macd_signal"]),
        "macd_hist": hist,
        "macd_hist_prev": prev_hist,
        "atr": atr_v,
    }
    if None in (close, ema_f, ema_s, rsi_v, atr_v, hist, prev_hist):
        return StrategyResult(
            "NONE", features=features, reasons=[{"check": "indicators_ready", "passed": False, "value": "warming up"}]
        )

    atr_pct = atr_v / close * 100
    features["atr_pct"] = atr_pct
    vol_ok = atr_pct <= float(cfg["max_atr_pct"])

    long_checks = [
        {"check": "trend_up (ema_fast > ema_slow)", "passed": ema_f > ema_s},
        {"check": "close > ema_slow", "passed": close > ema_s},
        {"check": "macd_hist crosses above 0", "passed": hist > 0 >= prev_hist},
        {
            "check": f"rsi in [{cfg['rsi_long_min']}, {cfg['rsi_long_max']}]",
            "passed": cfg["rsi_long_min"] <= rsi_v <= cfg["rsi_long_max"],
        },
        {"check": f"atr_pct <= {cfg['max_atr_pct']}", "passed": vol_ok},
    ]
    short_checks = [
        {"check": "trend_down (ema_fast < ema_slow)", "passed": ema_f < ema_s},
        {"check": "close < ema_slow", "passed": close < ema_s},
        {"check": "macd_hist crosses below 0", "passed": hist < 0 <= prev_hist},
        {
            "check": f"rsi in [{cfg['rsi_short_min']}, {cfg['rsi_short_max']}]",
            "passed": cfg["rsi_short_min"] <= rsi_v <= cfg["rsi_short_max"],
        },
        {"check": f"atr_pct <= {cfg['max_atr_pct']}", "passed": vol_ok},
    ]
    stop_dist = float(cfg["atr_stop_mult"]) * atr_v
    rr = float(cfg["reward_risk"])

    if all(c["passed"] for c in long_checks):
        return StrategyResult(
            "BUY", close, close - stop_dist, close + rr * stop_dist, features, [{"side": "BUY", **c} for c in long_checks]
        )
    if all(c["passed"] for c in short_checks):
        return StrategyResult(
            "SELL", close, close + stop_dist, close - rr * stop_dist, features, [{"side": "SELL", **c} for c in short_checks]
        )
    reasons = [{"side": "BUY", **c} for c in long_checks] + [{"side": "SELL", **c} for c in short_checks]
    return StrategyResult("NONE", features=features, reasons=reasons)
