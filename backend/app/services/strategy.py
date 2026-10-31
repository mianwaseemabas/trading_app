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

STRATEGIES = {
    "trend_pullback": "EMA trend + MACD momentum pullback (baseline)",
    "smc_sweep_bos": "SMC: liquidity sweep → BOS/CHoCH with displacement → FVG/order-block retest",
    "donchian_breakout": "Donchian 20-candle breakout with EMA 200 + ADX filter (trend following)",
    "rsi2_meanrev": "RSI-2 pullback in an uptrend (mean reversion, higher win rate, smaller wins)",
    "bb_squeeze": "Bollinger squeeze → breakout (volatility expansion)",
    "ema_cross_adx": "EMA 9/21 cross with MACD + ADX filter (momentum)",
}


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


EXTRA = {"donchian_breakout", "rsi2_meanrev", "bb_squeeze", "ema_cross_adx"}


def prepare(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add everything the configured strategy needs (indicators, SMC columns)."""
    from .indicators import add_indicators
    ind = add_indicators(df, cfg)
    if cfg.get("name") == "smc_sweep_bos":
        from .smc import signals
        ind = signals(ind, cfg)
    elif cfg.get("name") in EXTRA:
        from . import strategies_extra
        ind = strategies_extra.prepare(ind, cfg["name"])
    return ind


def evaluate(df: pd.DataFrame, i: int, cfg: dict) -> StrategyResult:
    """Evaluate the configured strategy on row i of a DataFrame returned by prepare()."""
    if cfg.get("name") == "smc_sweep_bos":
        return _smc_evaluate(df, i, cfg)
    if cfg.get("name") in EXTRA:
        from . import strategies_extra
        if "adx" not in df:
            df = strategies_extra.prepare(df, cfg["name"])
        a, e, sl, tp, feats, reasons = strategies_extra.evaluate(df, i, cfg["name"])
        return StrategyResult(a, e, sl, tp, feats, reasons)
    return _trend_evaluate(df, i, cfg)


def _smc_evaluate(df: pd.DataFrame, i: int, cfg: dict) -> StrategyResult:
    if "smc_signal" not in df:
        from .smc import signals
        df = signals(df, cfg)
    row = df.iloc[i]
    close, atr_v, ema_s = _f(row["close"]), _f(row.get("atr")), _f(row.get("ema_slow"))
    features = {"close": close, "atr": atr_v, "ema_slow": ema_s, "rsi": _f(row.get("rsi"))}
    info = row.get("smc_info")
    if not isinstance(info, dict):
        return StrategyResult("NONE", features=features,
                              reasons=[{"check": "SMC sequence (sweep → break → retest) completed on this candle", "passed": False}])
    features["smc"] = {"zone": info["zone"], "sweep_time": info["sweep_time"], "break_time": info["break_time"]}
    side = "BUY" if info["dir"] == "bull" else "SELL"
    reasons = [{"side": side, **c} for c in info["checks"]]
    sigv = int(row["smc_signal"])
    if sigv == 0:
        return StrategyResult("NONE", features=features, reasons=reasons)
    return StrategyResult(side, close, _f(row["smc_sl"]), _f(row["smc_tp"]), features, reasons)


def _trend_evaluate(df: pd.DataFrame, i: int, cfg: dict) -> StrategyResult:
    """trend_pullback v1 on row position i of an indicator DataFrame."""
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
