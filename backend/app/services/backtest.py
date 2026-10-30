"""Bar-by-bar backtester that reuses the live strategy and risk engine.

Assumptions (all conservative, all reported with the results):
* Signals are evaluated on closed candles; entry is at the NEXT candle's open.
* Candle prices are treated as BID prices (MT5 convention); ask = bid + spread.
* If stop-loss and take-profit are both touched inside one candle, the
  stop-loss is assumed to have been hit first.
* Stop exits incur the configured slippage; commission is charged per lot
  round-turn; swap is charged per lot per night held (negative = cost).
* Daily/weekly loss limits block new trades until the next day/week (live
  trading instead locks until a manual reset).
Results describe the past on one data sample. They do not establish that the
strategy will be profitable.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from .indicators import add_indicators, required_history
from .risk_engine import AccountState, InstrumentSpec, OpenPos, RiskContext
from .risk_engine import evaluate as risk_evaluate
from .strategy import evaluate as strat_evaluate


def _vpu(spec: InstrumentSpec, account_ccy: str, price: float, fallback_rate: float) -> float:
    if spec.currency_profit == account_ccy:
        return spec.contract_size
    if spec.currency_base == account_ccy:
        return spec.contract_size / price
    return spec.contract_size * fallback_rate


def metrics(trades: list[dict], initial: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net_pnl": 0.0, "return_pct": 0.0}
    pnl = [t["net_pnl"] for t in trades]
    wins = [p for p in pnl if p > 0]
    losses_ = [p for p in pnl if p <= 0]
    gp, gl = sum(wins), -sum(losses_)
    equity, peak, max_dd, max_dd_pct = initial, initial, 0.0, 0.0
    streak = longest = 0
    for p in pnl:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd, max_dd_pct = dd, dd / peak * 100 if peak else 0
        streak = streak + 1 if p <= 0 else 0
        longest = max(longest, streak)
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses_),
        "win_rate_pct": round(len(wins) / n * 100, 2),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "profit_factor": round(gp / gl, 3) if gl > 0 else (None if gp == 0 else 999.0),
        "net_pnl": round(sum(pnl), 2),
        "return_pct": round(sum(pnl) / initial * 100, 2),
        "expectancy": round(sum(pnl) / n, 3),
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "longest_losing_streak": longest,
        "total_costs": round(sum(t["commission"] + t["spread_cost"] - t["swap"] for t in trades), 2),
        "long_trades": sum(1 for t in trades if t["side"] == "BUY"),
        "short_trades": sum(1 for t in trades if t["side"] == "SELL"),
    }


def run_backtest(df: pd.DataFrame, strategy_cfg: dict, risk_cfg: dict, spec: InstrumentSpec, params: dict) -> dict:
    initial = float(params.get("initial_balance", 500))
    account_ccy = params.get("account_currency", "USD")
    spread_pts_default = float(params.get("spread_points", 12))
    slip_pts = float(params.get("slippage_points", 3))
    commission = float(params.get("commission_per_lot", risk_cfg.get("commission_per_lot", 7)))
    swap_per_lot_night = float(params.get("swap_per_lot_per_night", -1.0))
    fallback_rate = float(params.get("conversion_rate", 1.0))
    leverage = int(params.get("leverage", 100))
    oos_fraction = float(params.get("oos_fraction", 0.3))
    n_folds = int(params.get("folds", 4))

    ind = add_indicators(df, strategy_cfg)
    warmup = required_history(strategy_cfg) // 2
    rows = ind.rename_axis("time").reset_index()
    times = rows["time"]
    balance = initial
    pos = None
    trades: list[dict] = []
    equity_curve = [{"time": times.iloc[min(warmup, len(rows) - 1)].isoformat(), "equity": initial}] if len(rows) else []
    rejections: Counter = Counter()
    signals = 0
    day_key = week_key = None
    day_anchor = week_anchor = initial
    pt = spec.point

    def spread_at(i: int) -> float:
        s = float(rows["spread_points"].iloc[i]) if "spread_points" in rows and rows["spread_points"].iloc[i] > 0 else 0
        return (s or spread_pts_default) * pt

    def close_pos(i: int, exit_bid: float, reason: str):
        nonlocal balance, pos
        p = pos
        spr = spread_at(i)
        exit_px = exit_bid if p["side"] == "BUY" else exit_bid + spr
        vpu = _vpu(spec, account_ccy, exit_px, fallback_rate)
        gross = (exit_px - p["entry"] if p["side"] == "BUY" else p["entry"] - exit_px) * p["volume"] * vpu
        nights = max(0, (times.iloc[i].normalize() - p["time"].normalize()).days)
        swap = swap_per_lot_night * p["volume"] * nights
        comm = commission * p["volume"]
        net = gross + swap - comm
        balance += net
        risk_amt = abs(p["entry"] - p["sl"]) * p["volume"] * vpu
        trades.append({
            "symbol": spec.symbol, "side": p["side"], "entry_time": p["time"].isoformat(),
            "exit_time": times.iloc[i].isoformat(), "entry": round(p["entry"], 6), "exit": round(exit_px, 6),
            "sl": round(p["sl"], 6), "tp": round(p["tp"], 6) if p["tp"] else None, "volume": p["volume"],
            "gross_pnl": round(gross, 2), "commission": round(comm, 2), "swap": round(swap, 2),
            "spread_cost": round(p["spread"] * p["volume"] * vpu, 2), "net_pnl": round(net, 2),
            "r_multiple": round(net / risk_amt, 3) if risk_amt else None, "exit_reason": reason,
            "balance": round(balance, 2),
        })
        equity_curve.append({"time": times.iloc[i].isoformat(), "equity": round(balance, 2)})
        pos = None

    for i in range(warmup, len(rows) - 1):
        t = times.iloc[i]
        dk, wk = t.date(), (t.isocalendar()[0], t.isocalendar()[1])
        if dk != day_key:
            day_key, day_anchor = dk, balance
        if wk != week_key:
            week_key, week_anchor = wk, balance

        # Manage open position on this candle (entered at an earlier open).
        if pos is not None and t >= pos["time"]:
            hi, lo = float(rows["high"].iloc[i]), float(rows["low"].iloc[i])
            spr = spread_at(i)
            if pos["side"] == "BUY":
                hit_sl, hit_tp = lo <= pos["sl"], pos["tp"] is not None and hi >= pos["tp"]
                if hit_sl:
                    close_pos(i, pos["sl"] - slip_pts * pt, "stop_loss")
                elif hit_tp:
                    close_pos(i, pos["tp"], "take_profit")
            else:
                hit_sl, hit_tp = hi + spr >= pos["sl"], pos["tp"] is not None and lo + spr <= pos["tp"]
                if hit_sl:
                    close_pos(i, pos["sl"] + slip_pts * pt - spr, "stop_loss")
                elif hit_tp:
                    close_pos(i, pos["tp"] - spr, "take_profit")

        if pos is not None:
            continue
        res = strat_evaluate(ind, i, strategy_cfg)
        if res.action == "NONE":
            continue
        signals += 1
        nxt = i + 1
        bid = float(rows["open"].iloc[nxt])
        spr = spread_at(nxt)
        ask = bid + spr
        unreal = 0.0
        ctx = RiskContext(
            side=res.action, symbol=spec.symbol, bid=bid, ask=ask, quote_age_s=0,
            stop_loss=res.stop_loss, take_profit=res.take_profit, spec=spec,
            account=AccountState(balance + unreal, balance, balance, account_ccy, leverage),
            cfg={**risk_cfg, "max_spread_points": max(risk_cfg["max_spread_points"], spr / pt)},
            open_positions=[], mode="PAPER",
            day_loss=day_anchor - balance, week_loss=week_anchor - balance,
            day_anchor_equity=day_anchor, week_anchor_equity=week_anchor,
            profit_to_account_rate=_vpu(spec, account_ccy, bid, fallback_rate) / spec.contract_size,
            base_to_account_rate=1.0 if spec.currency_base == account_ccy else (bid if spec.currency_profit == account_ccy else fallback_rate),
        )
        d = risk_evaluate(ctx)
        if not d.approved:
            for c in d.checks:
                if not c["passed"]:
                    rejections[c["check"]] += 1
            continue
        entry = ask if res.action == "BUY" else bid
        entry += slip_pts * pt if res.action == "BUY" else -slip_pts * pt
        pos = {"side": res.action, "entry": entry, "sl": res.stop_loss, "tp": res.take_profit,
               "volume": d.volume, "time": times.iloc[nxt], "spread": spr}

    if pos is not None:
        last = len(rows) - 1
        close_pos(last, float(rows["close"].iloc[last]), "end_of_data")

    # In-sample / out-of-sample split and segment (walk-forward style) stability.
    folds = []
    if len(rows) > warmup + 10:
        start_t, end_t = times.iloc[warmup], times.iloc[-1]
        split_t = start_t + (end_t - start_t) * (1 - oos_fraction)
        ins = [t for t in trades if pd.Timestamp(t["entry_time"]) < split_t]
        oos = [t for t in trades if pd.Timestamp(t["entry_time"]) >= split_t]
        seg = (end_t - start_t) / max(1, n_folds)
        for k in range(n_folds):
            a, b = start_t + seg * k, start_t + seg * (k + 1)
            ft = [t for t in trades if a <= pd.Timestamp(t["entry_time"]) < b or (k == n_folds - 1 and pd.Timestamp(t["entry_time"]) == b)]
            folds.append({"fold": k + 1, "start": a.isoformat(), "end": b.isoformat(), **metrics(ft, initial)})
    else:
        ins, oos, split_t = trades, [], None

    m_all = metrics(trades, initial)
    m_in, m_oos = metrics(ins, initial), metrics(oos, initial)
    warnings = []
    if m_all["trades"] < 30:
        warnings.append("Fewer than 30 trades: results are not statistically meaningful.")
    if m_all["trades"] == 0:
        warnings.append("No trades were taken on this sample.")
    elif (m_all.get("profit_factor") or 0) < 1.1:
        warnings.append("Profit factor after costs is below 1.1.")
    if m_oos["trades"] and (m_oos.get("profit_factor") or 0) < 1.0:
        warnings.append("Out-of-sample segment lost money.")
    neg_folds = sum(1 for f in folds if f["trades"] and f["net_pnl"] < 0)
    if folds and neg_folds >= max(1, len(folds) // 2):
        warnings.append(f"{neg_folds} of {len(folds)} segments were negative: unstable.")
    return {
        "metrics": {
            "all": m_all,
            "in_sample": m_in,
            "out_of_sample": m_oos,
            "split_time": split_t.isoformat() if split_t is not None else None,
            "signals": signals,
            "risk_rejections": dict(rejections),
            "final_balance": round(balance, 2),
            "candles": len(rows),
            "period_start": times.iloc[0].isoformat() if len(rows) else None,
            "period_end": times.iloc[-1].isoformat() if len(rows) else None,
            "robust": not warnings,
            "warnings": warnings,
            "assumptions": {
                "entry": "next candle open", "prices": "candles treated as bid",
                "spread_points_default": spread_pts_default, "slippage_points": slip_pts,
                "commission_per_lot": commission, "swap_per_lot_per_night": swap_per_lot_night,
                "same_bar_sl_tp": "stop-loss assumed first",
            },
        },
        "trades": trades,
        "equity_curve": equity_curve,
        "folds": folds,
    }
