"""News breakout rules, AI news-direction filter, order routing labels."""
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.services import news_trading as nt


def frame(release_t, n=40, spike=0.0030, breakout=True):
    """M15 candles; a news spike at release_t, quiet range, then one close beyond the range on the last bar."""
    idx = pd.date_range(end=release_t + timedelta(minutes=15 * 6), periods=n, freq="15min", tz="UTC")
    close = np.full(n, 1.1000)
    df = pd.DataFrame({"open": close, "high": close + 0.0004, "low": close - 0.0004, "close": close}, index=idx)
    k = list(idx).index(pd.Timestamp(release_t))
    df.iloc[k, df.columns.get_loc("high")] = 1.1000 + spike / 2
    df.iloc[k, df.columns.get_loc("low")] = 1.1000 - spike / 2
    if breakout:
        df.iloc[-1, df.columns.get_loc("close")] = 1.1000 + spike / 2 + 0.0006
        df.iloc[-1, df.columns.get_loc("high")] = 1.1000 + spike / 2 + 0.0008
    df["atr"] = 0.0015
    return df


def test_news_breakout_rules():
    rel = datetime(2026, 10, 30, 12, 30, tzinfo=timezone.utc)
    c = {"news_events": [{"t": int(rel.timestamp()), "what": "USD CPI"}], "tf_seconds": 900, "wait_min": 15, "window_min": 120}
    a, e, sl, tp, feats, reasons = nt.evaluate(frame(rel), 39, c)
    assert a == "BUY" and sl < e < tp and abs((tp - e) / (e - sl) - 1.5) < 1e-6 and feats["news"] == "USD CPI"
    assert nt.evaluate(frame(rel, breakout=False), 39, c)[0] == "NONE"
    assert nt.evaluate(frame(rel), 39, {**c, "news_events": []})[0] == "NONE"
    assert nt.evaluate(frame(rel, spike=0.02), 39, c)[0] == "NONE"  # wild spike > 4 ATR -> skip
    late = {**c, "window_min": 30}
    assert nt.evaluate(frame(rel), 39, late)[0] == "NONE"  # outside the 2 h window


if os.getenv("APP_ENV") != "test":
    pytest.skip("integration part needs APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import NewsHeadline, User, utcnow  # noqa: E402
from app.secretbox import encrypt  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import intel  # noqa: E402

PW = "Test#Operator-Pass1"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    db.commit()
    db.close()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def c():
    cl = TestClient(app)
    r = cl.post("/api/auth/login", json={"username": "op", "password": PW})
    cl.headers["x-csrf-token"] = r.json()["user"]["csrf_token"]
    return cl


def test_routes_say_where_orders_go(db):
    tr = state.get(db, "trading")
    tr["mode"] = "MT5_DEMO"
    state.put(db, "trading", tr)
    br = state.get(db, "brokers")
    br["active"] = "capital"
    state.put(db, "brokers", br)
    cc = state.get(db, "crypto")
    cc.update(mode="TEST", symbols=["BTCUSDT"])
    state.put(db, "crypto", cc)
    db.commit()
    assert intel.route_for(db, "EURUSD")["label"].startswith("Capital.com DEMO")
    r = intel.route_for(db, "BTCUSDT")
    assert r["short"] == "BINANCE TEST" and r["mode"] == "CRYPTO_TEST" and not r["real"]
    assert intel.route_for(db, "2222.SR")["kind"] == "watch"


def test_ai_tags_and_filter(c, db):
    assert c.put("/api/news-auto", json={"ai_filter": True}).status_code == 400  # AI off
    a = state.get(db, "ai")
    a.update(enabled=True, api_key_enc=encrypt("sk-ant-x"), provider="anthropic", model="claude-sonnet-5-5")
    state.put(db, "ai", a)
    db.add(NewsHeadline(url="https://n.test/fed", source="Fed", category="central bank", title="Fed signals faster rate hikes",
                        summary="", published=utcnow() - timedelta(minutes=30), impact="High", tags=["rate hike"], assets=["USD"]))
    db.commit()
    r = c.put("/api/news-auto", json={"ai_filter": True, "breakout": True}).json()
    assert r["ai_filter"] and r["breakout"]
    import json
    n = nt.classify(db, call=lambda system, prompt: json.dumps({"items": [{"id": json.loads(prompt)[0]["id"], "effects": {"USD": "bullish"},
                                                                          "confidence": 0.9, "why": "hawkish Fed"}]}))
    db.commit()
    assert n == 1
    assert nt.bias(db, "EURUSD", "BUY")["opposing"] is True      # buying EUR vs USD while USD is bullish
    assert nt.bias(db, "EURUSD", "SELL")["aligned"] is True
    assert nt.bias(db, "USDJPY", "BUY")["aligned"] is True
    assert nt.bias(db, "GBPJPY", "BUY") is None                  # no tag for these currencies
    assert c.get("/api/news-auto").json()["tags"][0]["why"] == "hawkish Fed"
    ev = nt.event_times(db, "EURUSD")
    assert ev and "Fed signals" in ev[0]["what"]
