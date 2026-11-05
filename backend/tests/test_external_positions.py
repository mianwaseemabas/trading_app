"""Positions opened outside the app must not count towards its daily/weekly loss limits."""
import pytest

from app import state
from app.services import trading
from app.services.risk_engine import AccountState


def ext(ticket, pnl):
    return {"ticket": ticket, "symbol": "AMC", "side": "SELL", "pnl": pnl}


def test_track_external_moves_closed_pnl_into_total():
    r = trading.track_external({}, "capital", [ext("a", -100), ext("b", -50)])
    assert r["closed_pnl_total"] == 0
    r = trading.track_external(r, "capital", [ext("a", -120)])  # b closed at -50
    assert r["closed_pnl_total"] == pytest.approx(-50)
    assert r["closed_tickets"] == {"b": -50}


def test_track_external_missed_snapshot_is_not_double_counted():
    r = trading.track_external({}, "capital", [ext("a", -100)])
    r = trading.track_external(r, "capital", [])  # snapshot without it
    assert r["closed_pnl_total"] == pytest.approx(-100)
    r = trading.track_external(r, "capital", [ext("a", -110)])  # it was still open
    assert r["closed_pnl_total"] == pytest.approx(0)
    assert r["closed_tickets"] == {}


def test_track_external_resets_on_new_connector():
    r = trading.track_external({}, "capital", [ext("a", -100)])
    r = trading.track_external(r, "capital", [])
    r = trading.track_external(r, "ctrader", [])
    assert r["closed_pnl_total"] == 0


def test_limits_account_removes_outside_positions(monkeypatch):
    rec = {"positions": [ext("a", -2736.75), ext("b", -919.02), ext("c", -362.91)], "closed_pnl_total": -10}
    monkeypatch.setattr(state, "get", lambda db, key: rec)
    acct = AccountState(12880.35, 16899.03, 10589.54, "USD", 100)
    adj = trading.limits_account(None, "MT5_DEMO", acct)
    assert adj.equity == pytest.approx(16899.03 + 10)
    assert adj.balance == pytest.approx(16899.03 + 10)
    assert adj.free_margin == acct.free_margin
    # Paper / crypto modes are untouched.
    assert trading.limits_account(None, "PAPER", acct) is acct


def test_limits_account_ignores_missing_pnl(monkeypatch):
    monkeypatch.setattr(state, "get", lambda db, key: {"positions": [ext("a", None)]})
    acct = AccountState(1000, 1000, 1000)
    assert trading.limits_account(None, "MT5_DEMO", acct) is acct
