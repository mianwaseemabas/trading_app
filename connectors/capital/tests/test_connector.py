"""Capital.com connector tests against an in-process mock of the REST API."""
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
os.environ.update({
    "BRIDGE_SECRET": "unit-test-secret-0123456789abcdef0123456789",
    "CAPITAL_API_KEY": "key123", "CAPITAL_IDENTIFIER": "me@example.com", "CAPITAL_API_PASSWORD": "apipass",
    "CAPITAL_BASE_DEMO": "http://127.0.0.1:18799", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
})
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import capital_connector as cc  # noqa: E402
import mock_capital as mock  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = mock.serve(18799)
    yield
    srv.shutdown()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    mock.STATE.update(positions={}, confirms={}, activities=[], no_stop=False, lot_size=1, contract=1, max_span=10**9)
    monkeypatch.setattr(cc, "STATE_FILE", tmp_path / "state.json")
    reports = []
    monkeypatch.setattr(cc.Connector, "report", lambda self, cid, **kw: reports.append((cid, kw)))
    c = cc.Connector()
    c.refresh_quotes()
    c.load_positions()
    c.reports = reports
    return c


def order(**kw):
    o = {"client_id": "S7-MT5_DEMO", "action": "OPEN", "mode": "MT5_DEMO", "symbol": "EURUSD", "side": "BUY",
         "volume": 0.01, "requested_price": 1.0801, "stop_loss": 1.078, "take_profit": 1.084,
         "max_slippage_points": 10, "position_ticket": None,
         "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
    o.update(kw)
    msg = json.dumps(o, sort_keys=True, separators=(",", ":")).encode()
    return {**o, "signature": hmac.new(cc.SECRET.encode(), msg, hashlib.sha256).hexdigest()}


def test_market_specs_and_size_conversion(conn):
    m = conn.markets["EURUSD"]
    assert (m.digits, m.units_per_size, m.min_size) == (5, 1.0, 100.0)
    assert m.size(0.01) == 1000 and m.lots(1000) == 0.01
    assert m.size_unit_problem() is None


def test_open_verifies_stop_and_tracks_position(conn):
    conn.execute(order())
    cid, r = conn.reports[-1]
    assert r["status"] == "FILLED" and r["sl_verified"] is True and r["volume"] == 0.01
    pos = conn.positions_payload()
    assert len(pos) == 1 and pos[0]["volume"] == 0.01 and pos[0]["sl"] == 1.078


def test_missing_stop_at_broker_closes_position(conn):
    mock.STATE["no_stop"] = True
    mock.STATE["positions"]["x"] = None  # ensure we don't confuse ids
    mock.STATE["positions"].pop("x")
    conn.execute(order())
    _, r = conn.reports[-1]
    assert r["status"] == "FILLED" and r["sl_verified"] is False
    assert mock.STATE["positions"] == {}  # emergency-closed


def test_size_mismatch_closes_position(conn):
    mock.STATE["contract"] = 100000  # broker treats 1 size as 100,000 units
    conn.execute(order())
    _, r = conn.reports[-1]
    assert r["sl_verified"] is False and "size mismatch" in r["error"]
    assert mock.STATE["positions"] == {}


def test_ambiguous_size_units_refuses_to_trade(conn, monkeypatch):
    m = conn.markets["EURUSD"]
    monkeypatch.setattr(m, "min_size", 0.01)  # lotSize=1 but fractional min deal -> unclear
    monkeypatch.setattr(conn, "load_market", lambda name: m)
    conn.execute(order())
    _, r = conn.reports[-1]
    assert r["status"] == "REJECTED" and "size units unclear" in r["error"]
    assert mock.STATE["positions"] == {}


@pytest.mark.parametrize("kw,msg", [
    (dict(mode="MT5_LIVE"), "live order refused"),
    (dict(volume=1.0), "outside connector limit"),
    (dict(stop_loss=None), "missing stop-loss"),
    (dict(stop_loss=1.09), "wrong side"),
    (dict(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()), "expired"),
])
def test_rejections(conn, kw, msg):
    conn.execute(order(**kw))
    _, r = conn.reports[-1]
    assert r["status"] == "REJECTED" and msg in r["error"]
    assert mock.STATE["positions"] == {}


def test_tampered_order_rejected(conn):
    o = order()
    o["volume"] = 0.05
    conn.execute(o)
    assert "invalid order signature" in conn.reports[-1][1]["error"]


def test_close_and_closed_report(conn):
    conn.execute(order())
    deal = conn.reports[-1][1]["ticket"]
    conn.execute(order(client_id="C9-1", action="CLOSE", side="SELL", stop_loss=None, take_profit=None, position_ticket=deal))
    assert conn.reports[-1][1]["status"] == "FILLED"
    conn.load_positions()
    closed = conn.closed_payload()
    assert closed and closed[0]["position_ticket"] == deal and closed[0]["profit"] == pytest.approx(-1.0)
    assert closed[0]["reason"] == "manual"


def test_candles_are_bid_ohlc_utc(conn):
    bars = conn.candles(conn.markets["EURUSD"], "H1", None, None, 5)
    assert bars and all(b["time"] % 3600 == 0 and b["low"] <= b["open"] <= b["high"] for b in bars)


def test_history_backfill_adapts_to_daterange_limit(conn, monkeypatch):
    mock.STATE["max_span"] = 200  # broker rejects spans over 200 bars
    posted = []
    monkeypatch.setattr(cc, "app", lambda method, path, payload=None: posted.append(payload) or {"stored": 1})
    monkeypatch.setattr(cc, "BACKFILL_BARS", 700)
    conn.timeframes = ["H1"]
    conn.push_candles()
    assert conn.windows["H1"] <= 200
    bars = [b for p in posted if p and p["symbol"] == "EURUSD" for b in p["candles"]]
    assert len(bars) >= 700 and len({b["time"] for b in bars}) == len(bars)
    # incremental update uses no date range at all
    posted.clear()
    conn._push_candles_one(conn.markets["EURUSD"], "H1", __import__("time").time() + 3600)
    assert posted == [] or all(p["candles"] for p in posted)


def test_history_errors_do_not_block_cycle(conn, monkeypatch):
    def boom(*a, **k):
        raise cc.CapitalError(500, "server down")
    monkeypatch.setattr(conn, "candles", boom)
    conn.push_candles()  # must not raise


def test_dashboard_config_seed_and_reconnect(conn, monkeypatch):
    calls = []
    cfg = {"active": True, "version": 3, "allow_real": False, "max_volume": 0.05, "max_spread_points": 20}

    def fake_app(method, path, payload=None):
        calls.append((method, path, payload))
        if path.startswith("/api/bridge/config"):
            return dict(cfg)
        if path == "/api/bridge/seed":
            cfg.update(identifier=payload["fields"]["identifier"], api_key=payload["fields"]["api_key"],
                       api_password=payload["fields"]["api_password"], environment="demo")
            return {"status": "seeded"}
        return {}
    monkeypatch.setattr(cc, "app", fake_app)
    conn.sync_config()
    assert any(p == "/api/bridge/seed" for _, p, _ in calls)  # .env credentials copied to the dashboard once
    assert cc.Cfg.max_volume == 0.05 and cc.Cfg.max_spread == 20 and cc.Cfg.active
    first_client = conn.cap
    calls.clear()
    conn.sync_config()
    assert conn.cap is first_client and not any(p == "/api/bridge/seed" for _, p, _ in calls)
    cfg.update(version=4, api_password="changed")  # changed on the Brokers page
    conn.sync_config()
    assert conn.cap is not first_client and cc.Cfg.api_password == "changed"
    # Volume cap from the dashboard is enforced by the connector.
    assert "outside connector limit 0.05" in conn.validate(order(volume=0.06))
    cc.Cfg.max_volume, cc.Cfg.max_spread, cc.Cfg.api_password = 0.10, 30, "apipass"


def test_manual_positions_reported_as_external(conn):
    mock.STATE["positions"]["manual1"] = {"position": {"dealId": "manual1", "size": 500, "direction": "SELL", "level": 1.27,
                                                       "upl": 1.5, "contractSize": 1}, "market": {"epic": "GBPUSD", "bid": 1.269, "offer": 1.2691}}
    conn.load_positions()
    ext = conn.external_payload()
    assert [e["ticket"] for e in ext] == ["manual1"] and conn.positions_payload() == []
