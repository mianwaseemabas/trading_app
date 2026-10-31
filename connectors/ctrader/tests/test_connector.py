"""Unit tests for the cTrader connector (no network)."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("BRIDGE_SECRET", "unit-test-secret-0123456789abcdef0123456789")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ctrader_connector as cc  # noqa: E402


def sign(o: dict) -> dict:
    msg = json.dumps(o, sort_keys=True, separators=(",", ":")).encode()
    return {**o, "signature": hmac.new(cc.SECRET.encode(), msg, hashlib.sha256).hexdigest()}


def order(**kw):
    o = {"client_id": "S1-MT5_DEMO", "action": "OPEN", "mode": "MT5_DEMO", "symbol": "EURUSD", "side": "BUY",
         "volume": 0.01, "requested_price": 1.0801, "stop_loss": 1.078, "take_profit": 1.084,
         "max_slippage_points": 10, "position_ticket": None,
         "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
    o.update(kw)
    return sign(o)


def connector(live=False):
    c = cc.Connector.__new__(cc.Connector)
    c.st = cc.State(account_id=1, is_live=live)
    c.st.symbols["EURUSD"] = cc.Sym("EURUSD", 1, 5, 10_000_000, 100_000, 10_000_000_000, 100_000, "EUR", "USD", True)
    c._positions = []
    return c


def test_volume_conversion():
    s = cc.Sym("EURUSD", 1, 5, 10_000_000, 100_000, 10_000_000_000, 100_000, "EUR", "USD", True)
    assert s.cents(0.01) == 100_000          # 0.01 lot = 1,000 units = 100,000 cents
    assert s.cents(0.017) == 100_000         # rounded DOWN to the volume step
    assert s.lots(250_000) == 0.025
    assert s.point == 0.00001


def test_validate_accepts_good_order():
    assert connector().validate(order()) is None


def test_validate_rejections():
    c = connector()
    bad = order()
    bad["volume"] = 0.02  # tampered after signing
    assert "signature" in c.validate(bad)
    assert "expired" in c.validate(order(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    assert "missing stop-loss" in c.validate(order(stop_loss=None))
    assert "outside connector limit" in c.validate(order(volume=5.0))
    assert "live order refused" in c.validate(order(mode="MT5_LIVE"))
    assert "LIVE" in connector(live=True).validate(order())
    c._positions = [{"tradeData": {"comment": "S1-MT5_DEMO"}}]
    assert "duplicate" in c.validate(order())


def test_trendbar_decoding():
    c = connector()

    class FakeClient:
        async def send(self, pt, payload, timeout=20):
            assert pt == cc.PT["GET_TRENDBARS_REQ"] and payload["period"] == 9
            return {"trendbar": [{"volume": "120", "low": "108000", "deltaOpen": "20", "deltaClose": "35",
                                  "deltaHigh": "60", "utcTimestampInMinutes": 29_000_000}]}

    c.client = FakeClient()
    bars = asyncio.run(c.trendbars(c.st.symbols["EURUSD"], "H1", 0, 1))
    assert bars == [{"time": 29_000_000 * 60, "open": 1.0802, "high": 1.0806, "low": 1.08, "close": 1.08035, "tick_volume": 120}]


def test_int64_strings_and_enum_names():
    assert cc.i("123") == 123 and cc.i(None, 5) == 5
    assert cc.enum_is("ORDER_FILLED", "ORDER_FILLED", 3) and cc.enum_is(3, "ORDER_FILLED", 3)
    assert cc.enum_is("3", "ORDER_FILLED", 3) and not cc.enum_is("ORDER_ACCEPTED", "ORDER_FILLED", 3)


def test_validate_modify():
    c = connector()
    c._positions = [{"tradeData": {"comment": "S1-MT5_DEMO"}}]
    assert c.validate(order(client_id="M5-1", action="MODIFY", position_ticket=42, volume=50.0)) is None
    assert "stop-loss" in c.validate(order(client_id="M5-2", action="MODIFY", position_ticket=42, stop_loss=None))
