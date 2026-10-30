"""Deterministic technical indicators computed with pandas.

All functions take a DataFrame with columns open/high/low/close indexed by
candle open time and return Series aligned with that index.
"""
from __future__ import annotations

import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    out = out.where(avg_loss != 0, 100.0)
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], int(cfg.get("ema_fast", 50)))
    out["ema_slow"] = ema(out["close"], int(cfg.get("ema_slow", 200)))
    out["rsi"] = rsi(out["close"], int(cfg.get("rsi_period", 14)))
    m = macd(out["close"], int(cfg.get("macd_fast", 12)), int(cfg.get("macd_slow", 26)), int(cfg.get("macd_signal", 9)))
    out = out.join(m)
    out["atr"] = atr(out, int(cfg.get("atr_period", 14)))
    return out


def required_history(cfg: dict) -> int:
    """Number of candles needed before indicators are considered stable."""
    return int(max(cfg.get("ema_slow", 200), cfg.get("macd_slow", 26) + cfg.get("macd_signal", 9))) * 2
