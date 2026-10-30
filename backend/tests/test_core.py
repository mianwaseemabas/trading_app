"""Pure-logic tests: indicators, strategy, risk engine, backtester, security."""
import math

import numpy as np
import pandas as pd
import pytest

from app import security
from app.services import indicators, strategy
from app.services.backtest import run_backtest
from app.services.risk_engine import AccountState, InstrumentSpec, OpenPos, RiskContext, evaluate, floor_to_step
from app.state import DEFAULTS

EURUSD = InstrumentSpec("EURUSD", 0.00001, 0.00001, None, 100000, 0.01, 100, 0.01, "EUR", "USD")
USDJPY = InstrumentSpec("USDJPY", 0.001, 0.001, None, 100000, 0.01, 100, 0.01, "USD", "JPY")


def ctx(**kw):
    base = dict(
        side="BUY", symbol="EURUSD", bid=1.08000, ask=1.08010, quote_age_s=1,
        stop_loss=1.07800, take_profit=1.08400, spec=EURUSD,
        account=AccountState(500, 500, 500, "USD", 100), cfg=dict(DEFAULTS["risk"]),
        mode="PAPER", day_anchor_equity=500, week_anchor_equity=500, profit_to_account_rate=1.0,
    )
    base.update(kw)
    return RiskContext(**base)


# ---------------------------------------------------------------- risk engine
def test_risk_sizing_respects_budget():
    d = evaluate(ctx())
    assert d.approved, [c for c in d.checks if not c["passed"]]
    assert d.risk_budget == 2.50
    assert d.risk_amount <= 2.50
    # 21 pip stop + 1 pip slippage allowance = $220/lot, + $7 commission -> 2.50/227 -> 0.01 lot
    assert d.loss_per_lot == pytest.approx(227.0)
    assert d.volume == 0.01


def test_min_lot_too_risky_rejected():
    # 300 pip stop: even 0.01 lot risks > $2.50
    d = evaluate(ctx(stop_loss=1.05000))
    assert not d.approved
    assert any("fits risk budget" in c["check"] and not c["passed"] for c in d.checks)


@pytest.mark.parametrize("kw,check", [
    (dict(stop_loss=None), "stop-loss present"),
    (dict(stop_loss=1.09000), "stop-loss present"),
    (dict(kill_switch=True), "kill switch"),
    (dict(locked=True), "loss-limit lock"),
    (dict(mode="SIGNALS_ONLY"), "trading mode"),
    (dict(quote_age_s=999), "quote fresh"),
    (dict(data_ok=False), "market data fresh"),
    (dict(reconciliation_ok=False), "reconciliation"),
    (dict(ask=1.08100), "spread within limit"),
    (dict(open_positions=[OpenPos("GBPUSD", "BUY")]), "concurrent positions"),
    (dict(day_loss=10.0), "daily loss"),
    (dict(week_loss=20.0), "weekly loss"),
    (dict(duplicate=True), "duplicate"),
])
def test_rejections(kw, check):
    d = evaluate(ctx(**kw))
    assert not d.approved
    assert any(check in c["check"] and not c["passed"] for c in d.checks)


def test_jpy_conversion():
    d = evaluate(ctx(symbol="USDJPY", spec=USDJPY, bid=150.000, ask=150.012, stop_loss=149.700,
                     take_profit=150.600, profit_to_account_rate=1 / 150.006, base_to_account_rate=1.0))
    assert d.approved, [c for c in d.checks if not c["passed"]]
    assert d.risk_amount <= 2.5


def test_risk_does_not_grow_with_equity():
    d = evaluate(ctx(account=AccountState(5000, 5000, 5000, "USD", 100)))
    assert d.risk_budget == 2.50  # capped at reference balance 500


def test_floor_to_step():
    assert floor_to_step(0.0899, 0.01) == 0.08
    assert floor_to_step(1.0, 0.01) == 1.0


# ----------------------------------------------------------------- indicators
def _series(n=600, seed=1):
    rng = np.random.default_rng(seed)
    close = 1.1 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    idx = pd.date_range("2025-01-06", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": close, "close": close}, index=idx)
    df["open"] = df["close"].shift(1).fillna(close[0])
    df["high"] = df[["open", "close"]].max(axis=1) * 1.0005
    df["low"] = df[["open", "close"]].min(axis=1) * 0.9995
    df["tick_volume"] = 100
    df["spread_points"] = 10
    return df


def test_rsi_bounds_and_ema():
    df = _series()
    r = indicators.rsi(df["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    e = indicators.ema(pd.Series([1.0] * 100), 10).dropna()
    assert math.isclose(e.iloc[-1], 1.0)


def test_strategy_deterministic():
    cfg = DEFAULTS["strategy"]
    ind = indicators.add_indicators(_series(), cfg)
    a = [strategy.evaluate(ind, i, cfg).action for i in range(450, 599)]
    b = [strategy.evaluate(ind, i, cfg).action for i in range(450, 599)]
    assert a == b


def test_strategy_signal_has_valid_stops():
    cfg = DEFAULTS["strategy"]
    for seed in range(5):
        ind = indicators.add_indicators(_series(1500, seed), cfg)
        for i in range(450, len(ind)):
            res = strategy.evaluate(ind, i, cfg)
            if res.action == "BUY":
                assert res.stop_loss < res.entry_ref < res.take_profit
            elif res.action == "SELL":
                assert res.take_profit < res.entry_ref < res.stop_loss


# ------------------------------------------------------------------ backtest
def test_backtest_runs_and_accounts_costs():
    df = _series(3000, 7)
    res = run_backtest(df, DEFAULTS["strategy"], DEFAULTS["risk"], EURUSD, {"initial_balance": 500})
    m = res["metrics"]
    assert m["candles"] == 3000
    for t in res["trades"]:
        assert t["commission"] >= 0
        assert math.isclose(t["net_pnl"], t["gross_pnl"] - t["commission"] + t["swap"], abs_tol=0.02)
    assert m["final_balance"] == pytest.approx(500 + sum(t["net_pnl"] for t in res["trades"]), abs=0.05)


# ------------------------------------------------------------------ security
def test_password_hash_roundtrip():
    h = security.hash_password("Correct#Horse9battery")
    assert security.verify_password("Correct#Horse9battery", h)
    assert not security.verify_password("wrong", h)


def test_password_policy():
    assert security.password_problems("short")
    assert not security.password_problems("Long#Enough-Pass1")
    assert security.password_problems("Admin#Pass-12345", "admin")


def test_totp_rfc6238_vector():
    # RFC 6238 SHA1 test vector, secret "12345678901234567890", T=59 -> 94287082 (8 digits) -> last 6: 287082
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert security.totp_now(secret, at=59) == "287082"
