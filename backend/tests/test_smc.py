"""SMC definitions: swings, sweeps, breaks, FVGs, and the no-look-ahead guarantee."""
import numpy as np
import pandas as pd

from app.services import smc, strategy
from app.services.backtest import run_backtest
from app.state import DEFAULTS
from tests.test_core import EURUSD, _series

CFG = {**DEFAULTS["strategy"], "name": "smc_sweep_bos"}


def frame(rows):
    idx = pd.date_range("2026-01-05", periods=len(rows), freq="h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["atr"] = 0.0010
    return df


def kinds(res, kind):
    return [e for e in res["events"] if e["kind"] == kind]


def test_swing_confirmed_only_after_n_candles():
    # up, up, up, PEAK, down, down, down
    rows = [(1.000 + k * 0.001, 1.0015 + k * 0.001, 0.9995 + k * 0.001, 1.001 + k * 0.001) for k in range(4)]
    rows += [(1.003 - k * 0.001, 1.0035 - k * 0.001, 1.0015 - k * 0.001, 1.002 - k * 0.001) for k in range(1, 5)]
    df = frame(rows)
    res = smc.analyze(df, {"smc_swing_n": 3})
    sh = kinds(res, "swing_high")
    assert len(sh) == 1 and sh[0]["pivot_time"] == df.index[3] and sh[0]["i"] == 6  # known 3 candles later
    assert smc.analyze(df.iloc[:6], {"smc_swing_n": 3})["events"] == [] or not kinds(smc.analyze(df.iloc[:6], {"smc_swing_n": 3}), "swing_high")


def test_sweep_vs_break():
    base = [(1.0, 1.001, 0.999, 1.0)] * 3 + [(1.0, 1.005, 0.999, 1.0)] + [(1.0, 1.001, 0.999, 1.0)] * 3
    sweep = frame(base + [(1.0, 1.006, 0.999, 1.004)])  # wick above 1.005, close back below
    r = smc.analyze(sweep, {"smc_swing_n": 3})
    assert kinds(r, "sweep_bsl") and not kinds(r, "bos")
    brk = frame(base + [(1.0, 1.007, 0.999, 1.006)])  # close above the swing high
    r = smc.analyze(brk, {"smc_swing_n": 3})
    assert kinds(r, "bos") and kinds(r, "bos")[0]["dir"] == "bull" and not kinds(r, "sweep_bsl")


def test_fvg_detection():
    df = frame([(1.000, 1.001, 0.999, 1.0005), (1.0005, 1.004, 1.000, 1.0038), (1.0038, 1.006, 1.0025, 1.0055)])
    f = kinds(smc.analyze(df), "fvg")
    assert f and f[0]["dir"] == "bull" and f[0]["zone_low"] == 1.001 and f[0]["zone_high"] == 1.0025


def test_smc_strategy_has_no_lookahead():
    df = strategy.prepare(_series(2500, 3), CFG)
    full = df["smc_signal"].tolist()
    for cut in (900, 1400, 2100):
        part = strategy.prepare(_series(2500, 3).iloc[:cut], CFG)["smc_signal"].tolist()
        assert part == full[:cut]


def test_smc_strategy_stops_and_backtest():
    found = 0
    for seed in range(4):
        df = strategy.prepare(_series(3000, seed), CFG)
        for i in range(len(df)):
            r = strategy.evaluate(df, i, CFG)
            if r.action == "BUY":
                found += 1
                assert r.stop_loss < r.entry_ref < r.take_profit
            elif r.action == "SELL":
                found += 1
                assert r.take_profit < r.entry_ref < r.stop_loss
    assert found > 0
    res = run_backtest(_series(3000, 1), CFG, DEFAULTS["risk"], EURUSD, {"initial_balance": 500})
    assert res["metrics"]["signals"] >= 0 and "segments" in res["metrics"]
