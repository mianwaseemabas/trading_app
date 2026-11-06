"""Buy-and-hold benchmark and strategy-vs-hold comparison (idea from Vibe-Trading / Freqtrade: always ask
"did it beat just holding?").

Holding = buy once at the first tested candle (at the ask, plus fees) and sell at the last candle (at the bid, minus fees),
using the whole starting balance without leverage. Forex holding would also earn or pay overnight swap, which is not
included. Strategies here risk a small % per trade, so raw return is not the only fair test: we also compare
return per unit of maximum drawdown.
"""
from __future__ import annotations

import pandas as pd


def max_drawdown_pct(values) -> float:
    peak, dd = None, 0.0
    for v in map(float, values):
        peak = v if peak is None or v > peak else peak
        if peak and peak > 0:
            dd = max(dd, (peak - v) / peak * 100)
    return round(float(dd), 2)


def buy_and_hold(df: pd.DataFrame, *, start: int = 0, initial: float = 1000.0, spread: float = 0.0, fee_pct: float = 0.0,
                 points: int = 300) -> dict:
    """df: candles (bid prices), start: first tested row. spread in price units, fee_pct per side."""
    d = df.iloc[start:]
    if len(d) < 2:
        return {"available": False}
    entry = float(d["open"].iloc[0]) + spread
    qty = initial * (1 - fee_pct / 100) / entry
    eq = qty * d["close"].astype(float)
    final = float(eq.iloc[-1]) * (1 - fee_pct / 100)
    step = max(1, len(eq) // points)
    curve = [{"time": t.isoformat(), "equity": round(float(v), 2)} for t, v in list(eq.items())[::step]]
    curve.append({"time": d.index[-1].isoformat(), "equity": round(final, 2)})
    ret = (final - initial) / initial * 100
    dd = max_drawdown_pct(list(eq.values) + [final])
    return {"available": True, "return_pct": round(float(ret), 2), "max_drawdown_pct": dd,
            "return_per_dd": round(float(ret / dd), 2) if dd > 0 else None, "entry": round(entry, 6),
            "exit": round(float(d["close"].iloc[-1]), 6), "start": d.index[0].isoformat(), "end": d.index[-1].isoformat(),
            "curve": curve}


def versus(strat: dict, hold: dict) -> dict:
    """strat: backtest metrics['all'] (return_pct, max_drawdown_pct). Returns verdict + numbers."""
    if not hold.get("available"):
        return {"code": "NO_BENCHMARK", "text": "no holding benchmark (not enough candles)"}
    r, dd = strat.get("return_pct") or 0.0, strat.get("max_drawdown_pct") or 0.0
    s_rdd = round(r / dd, 2) if dd > 0 else (None if r <= 0 else 99.0)
    h_r, h_rdd = hold["return_pct"], hold.get("return_per_dd")
    beats_ret = r > h_r
    beats_risk = r > 0 and s_rdd is not None and (h_rdd is None or h_rdd <= 0 or s_rdd > h_rdd)
    if not strat.get("trades"):
        code, text = "NO_TRADES", "no trades — holding wins by default"
    elif beats_ret and beats_risk:
        code, text = "BEATS_HOLD", f"{r:+.1f}% vs holding {h_r:+.1f}%, and better return per drawdown"
    elif beats_risk:
        code, text = "BEATS_HOLD_RISK_ADJUSTED", (f"less return than holding ({r:+.1f}% vs {h_r:+.1f}%) but more return per unit of drawdown "
                                                  f"({s_rdd} vs {h_rdd})")
    elif beats_ret:
        code, text = "MORE_RETURN_MORE_PAIN", f"{r:+.1f}% vs holding {h_r:+.1f}%, but a worse return per drawdown"
    else:
        code, text = "LOSES_TO_HOLD", f"{r:+.1f}% vs holding {h_r:+.1f}% — just holding did better"
    return {"code": code, "text": text, "beats_return": beats_ret, "beats_risk_adjusted": beats_risk,
            "strategy_return_per_dd": s_rdd, "hold_return_pct": h_r, "hold_max_dd_pct": hold["max_drawdown_pct"],
            "hold_return_per_dd": h_rdd}


def downsample(curve: list[dict], points: int = 300) -> list[dict]:
    if len(curve) <= points:
        return curve
    step = len(curve) / points
    out = [curve[int(i * step)] for i in range(points)]
    return out + [curve[-1]]
