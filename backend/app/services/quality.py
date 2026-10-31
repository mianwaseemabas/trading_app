"""Data trust: quote diagnostics, market-session state, feed health and data incidents.

Every number shown to the user (spread, age, session) is computed here from the stored
quote and instrument metadata, so the dashboard, signal scoring and alerts agree.
A quote is never "repaired": missing or stale prices simply make a market ineligible.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..models import AccountSnapshot, DataIncident, Instrument, Quote, utcnow
from . import market_data as md

FX = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "SGD", "HKD", "MXN", "ZAR", "TRY", "CNH", "PLN"}
# Typical upper bounds for normal-hours spreads (pips). Wider = suspicious / costly.
FX_MAX_PIPS = {"EURUSD": 2.0, "GBPUSD": 3.0, "USDJPY": 2.5, "AUDUSD": 2.5, "USDCAD": 3.0, "USDCHF": 3.0, "EURGBP": 3.0}


def is_fx(symbol: str) -> bool:
    return len(symbol) == 6 and symbol[:3] in FX and symbol[3:] in FX


def pip_size(symbol: str, inst: Instrument | None) -> float | None:
    if is_fx(symbol):
        return 0.01 if symbol.endswith("JPY") else 0.0001
    return None


def session_state(symbol: str, crypto: bool, inst: Instrument | None, now: datetime | None = None) -> str:
    """OPEN | CLOSED_WEEKEND | ROLLOVER | CLOSED (broker says not tradeable)."""
    if crypto:
        return "OPEN"
    now = now or utcnow()
    wd, h = now.weekday(), now.hour
    if (wd == 4 and h >= 21) or wd == 5 or (wd == 6 and h < 22):
        return "CLOSED_WEEKEND"
    if inst is not None and not inst.trade_allowed:
        return "CLOSED"
    if h == 21 or (h == 22 and now.minute < 5):
        return "ROLLOVER"
    return "OPEN"


def quote_quality(db: Session, symbol: str, crypto_syms: set | None = None, atr_h1: float | None = None) -> dict:
    crypto_syms = crypto_syms if crypto_syms is not None else set(state.get(db, "crypto").get("symbols", []))
    crypto = symbol in crypto_syms
    q = db.get(Quote, symbol)
    inst = db.get(Instrument, symbol)
    risk = state.get(db, "risk")
    sess = session_state(symbol, crypto, inst)
    out = {"symbol": symbol, "asset": "crypto" if crypto else ("forex" if is_fx(symbol) else "other"),
           "provider": (q.source if q else None), "session": sess, "issues": []}
    if q is None or not q.bid or not q.ask:
        out.update(quality="NO_DATA", issues=["no bid/ask received"])
        return out
    now = utcnow()
    age = (now - q.collected_at).total_seconds()
    spread = q.ask - q.bid
    mid = (q.ask + q.bid) / 2
    pip = pip_size(symbol, inst)
    out.update(bid=q.bid, ask=q.ask, spread=round(spread, 10), spread_bps=round(spread / mid * 10000, 2) if mid else None,
               spread_pips=round(spread / pip, 2) if pip else None, pip_size=pip, tick_size=inst.tick_size if inst else None,
               contract_size=inst.contract_size if inst else None, min_qty=inst.volume_min if inst else None,
               qty_step=inst.volume_step if inst else None, quote_currency=inst.currency_profit if inst else None,
               timestamp_provider=q.time.isoformat() if q.time else None, timestamp_received=q.collected_at.isoformat(),
               quote_age_s=round(age, 1))
    if atr_h1:
        out["spread_atr_pct"] = round(spread / atr_h1 * 100, 1)
    stale_s = 60 if crypto else float(state.get(db, "data").get("stale_quote_seconds", 120))
    if spread < 0:
        out["issues"].append("ask below bid (crossed quote)")
    if age > stale_s and sess == "OPEN":
        out["issues"].append(f"quote is {age:.0f}s old (limit {stale_s:.0f}s)")
    max_pips = FX_MAX_PIPS.get(symbol, 4.0)
    wide = False
    if pip:
        wide = out["spread_pips"] > max_pips
        if wide:
            out["issues"].append(f"spread {out['spread_pips']} pips > normal max {max_pips}")
    else:
        lim_bps = 10 if crypto else float(risk.get("max_spread_pct_other", 0.2)) * 100
        wide = (out["spread_bps"] or 0) > lim_bps
        if wide:
            out["issues"].append(f"spread {out['spread_bps']} bps > limit {lim_bps:.0f}")
    if atr_h1 and spread > 0.25 * atr_h1:
        out["issues"].append(f"spread is {out['spread_atr_pct']}% of an average hourly move")
        wide = True
    if sess != "OPEN":
        out["quality"] = "CLOSED" if sess.startswith("CLOSED") else "ROLLOVER"
        if sess == "CLOSED_WEEKEND" and wide:
            out["explanation"] = "Market closed for the weekend: brokers show wide indicative spreads. Not tradeable — this is normal."
        elif sess == "ROLLOVER":
            out["explanation"] = "Daily rollover (around 21:00 UTC): spreads widen for a few minutes. Avoid new entries."
    elif any("old" in i or "crossed" in i for i in out["issues"]):
        out["quality"] = "STALE"
    elif wide:
        out["quality"] = "WIDE_SPREAD"
    else:
        out["quality"] = "OK"
    out["tradeable"] = out["quality"] == "OK"
    return out


def _atr(db: Session, symbol: str) -> float | None:
    from .indicators import add_indicators
    df = md.load_candles(db, symbol, "H1", limit=120)
    if len(df) < 30:
        return None
    v = add_indicators(df, state.get(db, "strategy"))["atr"].iloc[-1]
    return None if v != v else float(v)


def data_health(db: Session) -> dict:
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    crypto_syms = set(cc.get("symbols", []))
    symbols = list(dict.fromkeys(strat["symbols"] + (cc["symbols"] if cc.get("mode") != "OFF" or crypto_syms else [])))
    quotes = [quote_quality(db, s, crypto_syms, _atr(db, s)) for s in symbols]
    br = state.get(db, "brokers")
    cs = state.get(db, "connector_status").get(br.get("active"), {}) if br.get("active") not in (None, "none") else {}
    bridge = state.get(db, "bridge")
    last_hb = bridge.get("last_seen")
    hb_age = (utcnow() - datetime.fromisoformat(last_hb)).total_seconds() if last_hb else None
    news = state.get(db, "news_status")
    news_age = (utcnow() - datetime.fromisoformat(news["last_fetch"])).total_seconds() / 3600 if news.get("last_fetch") else None
    candles = []
    for s in symbols:
        for tf in (strat.get("trade_timeframes") or ["H1"]) if s not in crypto_syms else (cc.get("signal_timeframes") or ["H1"]):
            df = md.load_candles(db, s, tf, limit=1)
            if len(df):
                last = df.index[-1].to_pydatetime()
                lag = (utcnow() - (last + md.tf_delta(tf))).total_seconds()
                candles.append({"symbol": s, "tf": tf, "last": last.isoformat(), "lag_min": round(lag / 60, 1),
                                "ok": lag <= md.TF_SECONDS[tf] * 1.5})
            else:
                candles.append({"symbol": s, "tf": tf, "last": None, "lag_min": None, "ok": False})
    feeds = [
        {"name": f"Broker feed ({br.get('active') or 'none'})", "ok": hb_age is not None and hb_age < 90,
         "detail": f"last heartbeat {hb_age:.0f}s ago" if hb_age is not None else "no heartbeat yet", "status": cs.get("status")},
        {"name": f"Crypto prices ({cc['exchange']})", "ok": bool(state.get(db, "crypto_status").get("market_ok")),
         "detail": state.get(db, "crypto_status").get("market_error") or (
             f"updated {(utcnow() - datetime.fromisoformat(state.get(db, 'crypto_status')['market_at'])).total_seconds():.0f}s ago"
             if state.get(db, "crypto_status").get("market_at") else "not loaded"),
         "status": cc.get("mode")},
        {"name": "Economic calendar", "ok": bool(news.get("ok")) and news_age is not None and news_age < 26,
         "detail": f"updated {news_age:.1f} h ago" if news_age is not None else (news.get("error") or "never loaded")},
    ]
    if state.get(db, "data").get("source") == "simulator":
        feeds.insert(0, {"name": "Data source", "ok": False, "detail": "SIMULATED prices — for testing only"})
    open_inc = db.scalars(select(DataIncident).where(DataIncident.ended_at.is_(None)).order_by(DataIncident.started_at.desc())).all()
    tradeable = [q["symbol"] for q in quotes if q.get("tradeable")]
    return {"at": utcnow().isoformat(), "quotes": quotes, "feeds": feeds, "candles": candles,
            "incidents": [{"key": i.key, "kind": i.kind, "symbol": i.symbol, "since": i.started_at.isoformat(), "detail": i.detail} for i in open_inc],
            "tradeable": tradeable, "ok": bool(tradeable) and all(f["ok"] for f in feeds if "Crypto" not in f["name"] or cc.get("mode") != "OFF")}


_last_check = {"t": 0.0}


def track_incidents(db: Session) -> None:
    """Open one incident per (kind, symbol) when an OPEN market has stale/crossed/wide data; close on recovery."""
    if time.time() - _last_check["t"] < 30:
        return
    _last_check["t"] = time.time()
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    crypto_syms = set(cc.get("symbols", []))
    syms = strat["symbols"] + (cc["symbols"] if cc.get("mode") != "OFF" else [])
    active: dict[str, tuple] = {}
    for s in syms:
        q = quote_quality(db, s, crypto_syms)
        if q.get("session") != "OPEN":
            continue
        if q["quality"] in ("STALE", "NO_DATA"):
            active[f"DATA_STALE:{s}"] = ("DATA_STALE", s, "; ".join(q["issues"]))
        elif q["quality"] == "WIDE_SPREAD":
            active[f"SPREAD_TOO_WIDE:{s}"] = ("SPREAD_TOO_WIDE", s, "; ".join(q["issues"]))
    tr = state.get(db, "trading")
    if tr["mode"] in ("MT5_DEMO", "MT5_LIVE"):
        last = state.get(db, "bridge").get("last_seen")
        age = (utcnow() - datetime.fromisoformat(last)).total_seconds() if last else None
        if age is None or age > 120:
            active["BROKER_DISCONNECTED:"] = ("BROKER_DISCONNECTED", "", f"no heartbeat from {state.get(db, 'brokers').get('active')} for "
                                              f"{'ever' if age is None else f'{age:.0f}s'}")
    from .alerts import raise_alert
    existing = {i.key: i for i in db.scalars(select(DataIncident).where(DataIncident.ended_at.is_(None))).all()}
    for key, (kind, sym, detail) in active.items():
        if key not in existing:
            inc = DataIncident(key=key, kind=kind, symbol=sym, detail=detail[:500])
            db.add(inc)
            db.flush()
            title = {"DATA_STALE": f"{sym}: prices stale", "SPREAD_TOO_WIDE": f"{sym}: spread too wide",
                     "BROKER_DISCONNECTED": "Broker connection lost"}[kind]
            raise_alert(db, kind, title, detail, severity="critical" if kind == "BROKER_DISCONNECTED" else "warning",
                        dedup=f"inc:{key}:{inc.id}", symbol=sym)
    for key, inc in existing.items():
        if key not in active:
            inc.ended_at = utcnow()


# ---------------------------------------------------------------- accounts
def accounts(db: Session) -> list[dict]:
    """Every account environment separately — never summed together."""
    from . import trading
    from .crypto import desk
    tr = state.get(db, "trading")
    cc = state.get(db, "crypto")
    out = []
    acct, meta = trading.account_state(db, "PAPER")
    out.append({"env": "APP_PAPER", "label": "App practice (paper)", "source": "Qivynex simulator at broker prices", "currency": acct.currency,
                "balance": acct.balance, "equity": acct.equity, "updated_at": utcnow().isoformat(), "active": tr["mode"] == "PAPER",
                "real_money": False})
    snap = db.scalar(select(AccountSnapshot).where(AccountSnapshot.source == "mt5").order_by(AccountSnapshot.at.desc()).limit(1))
    if snap:
        env = "BROKER_LIVE" if snap.trade_mode == "real" else "BROKER_DEMO"
        out.append({"env": env, "label": "Broker LIVE (real money)" if env == "BROKER_LIVE" else "Broker demo",
                    "source": f"{state.get(db, 'brokers').get('active')} · account {str(snap.login)[-4:].rjust(len(str(snap.login)), '•') if snap.login else '—'}",
                    "currency": snap.currency, "balance": snap.balance, "equity": snap.equity, "updated_at": snap.at.isoformat(),
                    "age_s": round((utcnow() - snap.at).total_seconds()), "active": tr["mode"] in trading.MT5_MODES,
                    "real_money": env == "BROKER_LIVE"})
    a2, _ = desk.account(db, "CRYPTO_PAPER")
    out.append({"env": "CRYPTO_PAPER", "label": "Crypto practice (paper)", "source": f"virtual USDT at {cc['exchange']} prices",
                "currency": "USDT", "balance": a2.balance, "equity": a2.equity, "updated_at": utcnow().isoformat(),
                "active": cc["mode"] == "PAPER", "real_money": False})
    st = state.get(db, "crypto_status")
    if st.get("balance_mode") in ("CRYPTO_TEST", "CRYPTO_LIVE"):
        a3, _ = desk.account(db, st["balance_mode"])
        if a3:
            env = "CRYPTO_TESTNET" if st["balance_mode"] == "CRYPTO_TEST" else "CRYPTO_LIVE"
            out.append({"env": env, "label": "Crypto testnet/demo" if env == "CRYPTO_TESTNET" else "Crypto LIVE (real money)",
                        "source": cc["exchange"], "currency": "USDT", "balance": a3.balance, "equity": a3.equity,
                        "updated_at": st.get("balance_at"), "active": desk.pos_mode(db) == st["balance_mode"],
                        "real_money": env == "CRYPTO_LIVE"})
    for a in out:
        a["balance"], a["equity"] = round(a["balance"], 2), round(a["equity"], 2)
    return out
