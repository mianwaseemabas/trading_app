"""Strategy library, strategy lab, auto-trade policy, market headlines and Telegram helper."""
import os
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.services import headlines as hl
from app.services import strategies_extra as sx
from app.services import strategy as st


def synth(n=1500, seed=3):
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0, 0.0012, n)) + 0.02 * np.sin(np.arange(n) / 90)
    opn = np.r_[close[0], close[:-1]]
    hi = np.maximum(opn, close) + rng.uniform(0, 0.0008, n)
    lo = np.minimum(opn, close) - rng.uniform(0, 0.0008, n)
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": opn, "high": hi, "low": lo, "close": close, "tick_volume": 100}, index=idx)


@pytest.mark.parametrize("name", sorted(sx.INFO))
def test_extra_strategies_valid_levels_and_no_lookahead(name):
    cfg = {"name": name}
    df = st.prepare(synth(), cfg)
    hits = 0
    for i in range(250, len(df)):
        action, entry, sl, tp, feats, reasons = sx.evaluate(df, i, name)
        if action == "BUY":
            assert sl < entry < tp
        elif action == "SELL":
            assert tp < entry < sl
        else:
            continue
        hits += 1
        # same decision when the future is removed -> no look-ahead
        cut = st.prepare(synth().iloc[: i + 1], cfg)
        assert sx.evaluate(cut, i, name)[0] == action
        if hits >= 3:
            break
    info = sx.INFO[name]
    assert info["typical_win_rate"] and info["rules"] and name in st.STRATEGIES


def test_headline_tagging_and_rss_parse():
    imp, tags, assets = hl.tag("Fed holds interest rate steady as inflation cools", "Powell said...")
    assert imp == "High" and "USD" in assets
    imp2, _, assets2 = hl.tag("Bitcoin ETF inflows reach record")
    assert imp2 == "Medium" and {"BTC", "CRYPTO"} <= set(assets2)
    assert hl.tag("Company opens new office")[0] == "Low"
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><title>ECB raises rates by 25bp</title><link>https://x.test/a</link><pubDate>Fri, 30 Oct 2026 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;Lagarde says euro inflation&lt;/p&gt;</description></item>
      <item><title></title><link>https://x.test/b</link></item></channel></rss>"""
    rows = hl.parse(xml, {"name": "T", "category": "central bank"})
    assert len(rows) == 1 and rows[0]["impact"] == "High" and "EUR" in rows[0]["assets"] and "<p>" not in rows[0]["summary"]
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Solana hack drains wallet</title>
      <link href="https://x.test/c"/><updated>2026-10-30T09:00:00Z</updated></entry></feed>"""
    r2 = hl.parse(atom, {"name": "A"})
    assert r2[0]["url"] == "https://x.test/c" and r2[0]["impact"] == "High"
    with pytest.raises(ValueError):
        hl.parse(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><r/>', {"name": "E"})


# ------------------------------------------------------------------ integration
if os.getenv("APP_ENV") != "test":
    pytest.skip("integration part needs APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Alert, Candle, NewsHeadline, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import alerts, automation, lab, livegate  # noqa: E402
from app.services import market_data as md  # noqa: E402

PW = "Test#Operator-Pass1"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    db.commit()
    md.run_simulator(db, ["EURUSD"], ["H1"])
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


def test_auto_ok_rules(db):
    good = SimpleNamespace(verdict="PAPER_TEST", score=80)
    assert not automation.auto_ok(db, "PAPER", good)  # off by default
    ctl = state.get(db, "controls")
    ctl["auto_execute"] = {"PAPER": True, "CRYPTO_LIVE": True}
    state.put(db, "controls", ctl)
    db.commit()
    assert automation.auto_ok(db, "PAPER", good)
    assert not automation.auto_ok(db, "PAPER", SimpleNamespace(verdict="WATCH", score=90))
    assert not automation.auto_ok(db, "PAPER", SimpleNamespace(verdict="PAPER_TEST", score=60))
    assert not livegate.is_approved(db) and not automation.auto_ok(db, "CRYPTO_LIVE", good)  # live needs the gate
    # with an approved gate, real money still needs RESEARCH_SUPPORTED — PAPER_TEST is not enough
    from datetime import timedelta
    g = state.get(db, "live_gate")
    state.put(db, "live_gate", {**g, "approved": True, "expires_at": (utcnow() + timedelta(days=1)).isoformat()})
    db.commit()
    assert livegate.is_approved(db)
    assert not automation.auto_ok(db, "CRYPTO_LIVE", good)
    assert automation.auto_ok(db, "CRYPTO_LIVE", SimpleNamespace(verdict="RESEARCH_SUPPORTED", score=80))
    state.put(db, "live_gate", g)
    db.commit()
    ctl["auto_execute"] = {}
    state.put(db, "controls", ctl)
    db.commit()


def test_automation_endpoints(c, db):
    r = c.get("/api/automation").json()
    assert {m["mode"] for m in r["modes"]} >= {"PAPER", "CRYPTO_LIVE"} and not r["live_gate_approved"]
    r = c.put("/api/automation", json={"mode": "CRYPTO_TEST", "enabled": True, "min_score": 75, "min_verdict": "PAPER_TEST"})
    assert r.status_code == 200, r.text
    assert next(m for m in r.json()["modes"] if m["mode"] == "CRYPTO_TEST")["enabled"]
    prefs = state.get(db, "alert_prefs")
    assert "AUTO_TRADE" in prefs["channels"]["telegram"] and "AUTO_TRADE" in prefs["channels"]["email"]
    bad = c.put("/api/automation", json={"mode": "CRYPTO_LIVE", "enabled": True, "password": PW})
    assert bad.status_code in (400, 403, 409) and "gate" in bad.text.lower()
    c.put("/api/automation", json={"mode": "CRYPTO_TEST", "enabled": False})


def test_auto_trade_alert_text(db):
    from app.models import Signal
    last = utcnow().replace(minute=0, second=0, microsecond=0)
    s = Signal(symbol="EURUSD", timeframe="H1", candle_time=last, strategy="rsi2_meanrev", strategy_version="x1", action="BUY",
               entry_ref=1.1, stop_loss=1.09, take_profit=1.12, features={}, reasons=[], status="ORDERED", trading_mode="PAPER")
    db.add(s)
    db.commit()
    automation.announce(db, s, 0.1, 1.1, "PAPER")
    db.commit()
    a = db.scalars(select(Alert).where(Alert.type == "AUTO_TRADE")).one()
    assert "AUTO TRADE PLACED" in a.title and "H1" in a.title and "valid until" in a.body and "Mode: PAPER" in a.body


def test_headlines_refresh_alert_and_api(c, db):
    now = utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    xml = f"""<rss><channel><item><title>Fed cuts interest rate in emergency move</title><link>https://n.test/1</link>
      <pubDate>{now}</pubDate></item><item><title>Local bakery wins award</title><link>https://n.test/2</link>
      <pubDate>{now}</pubDate></item></channel></rss>""".encode()
    state.put(db, "news_feeds", [{"name": "Test", "url": "https://n.test/rss", "category": "central bank"}])
    db.commit()
    st_ = hl.refresh(db, force=True, fetcher=lambda u: xml)
    db.commit()
    assert st_["feeds"]["Test"]["ok"] and db.scalar(select(NewsHeadline).where(NewsHeadline.url == "https://n.test/1")).impact == "High"
    assert db.scalars(select(Alert).where(Alert.type == "MARKET_NEWS")).all()
    hl.refresh(db, force=True, fetcher=lambda u: xml)  # no duplicate alert
    db.commit()
    assert len(db.scalars(select(Alert).where(Alert.type == "MARKET_NEWS")).all()) == 1
    r = c.get("/api/news/headlines?mine=true").json()
    assert [x["url"] for x in r["items"]] == ["https://n.test/1"] and "USD" in r["watched"]
    bad = hl.refresh(db, force=True, fetcher=lambda u: (_ for _ in ()).throw(OSError("offline")))
    assert not bad["feeds"]["Test"]["ok"]
    state.put(db, "news_feeds", [])
    db.commit()


def test_telegram_find_chat(c, monkeypatch):
    def fake(token, method, payload=None):
        assert token == "123:ABC"
        if method == "getMe":
            return {"result": {"username": "my_bot"}}
        return {"result": [{"message": {"chat": {"id": 555, "type": "private", "first_name": "Waseem"}}}]}
    monkeypatch.setattr(alerts, "telegram_api", fake)
    r = c.post("/api/alerts/telegram/find-chat", json={"telegram_token": "123:ABC"}).json()
    assert r["bot"] == "my_bot" and r["chats"][0]["chat_id"] == "555"
    assert c.post("/api/alerts/telegram/find-chat", json={"telegram_token": "nocolon"}).status_code == 400


def test_lab_one_and_api(c, db):
    df = synth(900)
    for t, r in df.iterrows():
        db.merge(Candle(symbol="EURUSD", timeframe="H4", time=t.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close,
                        tick_volume=1, spread_points=0, source="test", collected_at=utcnow()))
    db.commit()
    res = lab._one(db, "EURUSD", "H4", "ema_cross_adx", False)
    assert "skipped" not in res and res["verdict"] and "final_test" in res, res
    assert lab._one(db, "EURUSD", "M15", "rsi2_meanrev", False)["skipped"]
    g = c.get("/api/lab").json()
    assert len(g["catalog"]) == len(st.STRATEGIES) and "honesty" in g
    assert c.post("/api/lab/apply", json={"target": "crypto", "strategy": "bb_squeeze"}).status_code == 200
    assert state.get(db, "crypto")["strategy"] == "bb_squeeze"
    assert c.post("/api/lab/apply", json={"target": "crypto", "strategy": "nope"}).status_code in (400, 422)
