"""Qivynex Trade — MT5 bridge (runs on the Windows machine/VPS next to the MetaTrader 5 terminal).

Responsibilities
  * push account state, symbol specifications, quotes and CLOSED candles to the backend
  * fetch signed order instructions, re-validate them locally, execute through MT5
  * verify fills and protective stop-loss at the broker, report results
  * push positions + closed deals so the backend can reconcile

Every API request is HMAC-signed (same secret as BRIDGE_SECRET on the server).
Independently of the server, the bridge FAILS CLOSED: it refuses orders that are
unsigned, expired, oversized, missing a stop-loss, for the wrong account type
(demo vs real), or when the spread is too wide.

Run:  python mt5_bridge.py         (configuration in bridge.env next to this file)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    print("The MetaTrader5 package is required: pip install MetaTrader5 (Windows only)")
    sys.exit(1)

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent


# ------------------------------------------------------------------ config
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(HERE / "bridge.env")

API_URL = os.environ.get("API_URL", "https://trade.qivynex.com").rstrip("/")
KEY_ID = os.environ.get("BRIDGE_KEY_ID", "mt5-bridge-1")
SECRET = os.environ.get("BRIDGE_SECRET", "")
MT5_PATH = os.environ.get("MT5_PATH") or None
MT5_LOGIN = int(os.environ.get("MT5_LOGIN", "0") or 0)
MT5_PASSWORD = os.environ.get("MT5_PASSWORD") or None
MT5_SERVER = os.environ.get("MT5_SERVER") or None
MAGIC = int(os.environ.get("MAGIC", "724001"))
MAX_VOLUME = float(os.environ.get("MAX_VOLUME", "0.10"))
MAX_SPREAD_POINTS = float(os.environ.get("MAX_SPREAD_POINTS", "30"))
ALLOW_REAL = os.environ.get("ALLOW_REAL_ACCOUNT", "false").lower() == "true"
LOOP_SECONDS = float(os.environ.get("LOOP_SECONDS", "5"))
BACKFILL_BARS = int(os.environ.get("BACKFILL_BARS", "5000"))
SERVER_UTC_OFFSET = os.environ.get("SERVER_UTC_OFFSET_HOURS", "auto")
# Broker symbol names, e.g. "EURUSD=EURUSD.m,GBPUSD=GBPUSD.m"
SYMBOL_MAP = dict(p.split("=", 1) for p in os.environ.get("SYMBOL_MAP", "").split(",") if "=" in p)
REVERSE_MAP = {v: k for k, v in SYMBOL_MAP.items()}

TF = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
}

log = logging.getLogger("bridge")
log.setLevel(logging.INFO)
_h = RotatingFileHandler(HERE / "bridge.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)
log.addHandler(logging.StreamHandler(sys.stdout))


def broker_symbol(sym: str) -> str:
    return SYMBOL_MAP.get(sym, sym)


def app_symbol(bsym: str) -> str:
    return REVERSE_MAP.get(bsym, bsym)


# --------------------------------------------------------------------- api
session = requests.Session()


def _headers(method: str, path: str, body: bytes) -> dict:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    msg = "\n".join([ts, nonce, method, path, hashlib.sha256(body).hexdigest()])
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-Bridge-Key-Id": KEY_ID, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
            "Content-Type": "application/json", "User-Agent": f"qivynex-mt5-bridge/{VERSION}"}


def api(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, default=str).encode() if payload is not None else b""
    r = session.request(method, API_URL + path, data=body or None, headers=_headers(method, path, body), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def verify_order_signature(o: dict) -> bool:
    sig = o.get("signature", "")
    payload = {k: v for k, v in o.items() if k != "signature"}
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.compare_digest(hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest(), sig)


# --------------------------------------------------------------------- mt5
def connect() -> None:
    kwargs = {}
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if MT5_LOGIN:
        kwargs.update(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    acc = mt5.account_info()
    log.info("Connected to MT5: login %s server %s trade_mode %s (0=demo, 2=real)", acc.login, acc.server, acc.trade_mode)
    if acc.trade_mode == 2 and not ALLOW_REAL:
        log.warning("REAL account detected and ALLOW_REAL_ACCOUNT=false: orders will be refused; data will still flow.")


_offset_cache: dict = {"value": None, "at": 0.0}


def server_offset_seconds(symbols: list[str]) -> int:
    """Broker server time minus UTC, rounded to 30 minutes. MT5 bar times are in server time."""
    if SERVER_UTC_OFFSET != "auto":
        return int(float(SERVER_UTC_OFFSET) * 3600)
    if _offset_cache["value"] is not None and time.time() - _offset_cache["at"] < 3600:
        return _offset_cache["value"]
    for s in symbols:
        tick = mt5.symbol_info_tick(broker_symbol(s))
        if tick and abs(time.time() - tick.time) < 15 * 3600:
            diff = tick.time - time.time()
            # Only trust a fresh tick (market open): offset is a multiple of 30 min, tick lag < 15 min.
            off = round(diff / 1800) * 1800
            if abs(diff - off) < 900:
                _offset_cache.update(value=int(off), at=time.time())
                return int(off)
    if _offset_cache["value"] is not None:
        return _offset_cache["value"]
    raise RuntimeError("Cannot determine broker server UTC offset (market closed?). Set SERVER_UTC_OFFSET_HOURS in bridge.env")


def account_payload() -> dict:
    a = mt5.account_info()
    return {"login": a.login, "server": a.server, "trade_mode": a.trade_mode, "currency": a.currency,
            "balance": a.balance, "equity": a.equity, "margin": a.margin, "margin_free": a.margin_free,
            "leverage": a.leverage}


def symbols_payload(symbols: list[str]) -> list[dict]:
    out = []
    for s in symbols:
        b = broker_symbol(s)
        mt5.symbol_select(b, True)
        i = mt5.symbol_info(b)
        if i is None:
            log.error("Symbol %s (%s) not found at broker", s, b)
            continue
        out.append({"symbol": s, "digits": i.digits, "point": i.point, "trade_contract_size": i.trade_contract_size,
                    "trade_tick_size": i.trade_tick_size, "trade_tick_value": i.trade_tick_value,
                    "volume_min": i.volume_min, "volume_max": i.volume_max, "volume_step": i.volume_step,
                    "currency_base": i.currency_base, "currency_profit": i.currency_profit,
                    "trade_allowed": i.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL})
    return out


def quotes_payload(symbols: list[str], offset: int) -> list[dict]:
    out = []
    for s in symbols:
        t = mt5.symbol_info_tick(broker_symbol(s))
        if t and t.bid > 0:
            out.append({"symbol": s, "bid": t.bid, "ask": t.ask, "time": t.time - offset})
    return out


last_sent: dict[tuple[str, str], int] = {}


def push_candles(symbols: list[str], timeframes: list[str], offset: int) -> None:
    for s in symbols:
        for tf in timeframes:
            key = (s, tf)
            count = BACKFILL_BARS if key not in last_sent else 10
            # start_pos=1 skips the currently forming bar: only CLOSED candles are sent.
            rates = mt5.copy_rates_from_pos(broker_symbol(s), TF[tf], 1, count)
            if rates is None or len(rates) == 0:
                log.warning("No rates for %s %s: %s", s, tf, mt5.last_error())
                continue
            newest = int(rates[-1]["time"])
            if last_sent.get(key) == newest:
                continue
            candles = [{"time": int(r["time"]) - offset, "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]), "tick_volume": int(r["tick_volume"]),
                        "spread": int(r["spread"])} for r in rates]
            for i in range(0, len(candles), 2000):
                res = api("POST", "/api/bridge/candles", {"symbol": s, "timeframe": tf, "candles": candles[i:i + 2000]})
                if res.get("rejected"):
                    log.warning("%s %s: %s candles rejected by server validation", s, tf, res["rejected"])
            last_sent[key] = newest


def positions_payload(offset: int) -> dict:
    raw = mt5.positions_get()
    if raw is None:
        # Never report "no positions" on an API error: the server would treat them as closed.
        raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
    pos = [p for p in raw if p.magic == MAGIC]
    out = [{"ticket": p.ticket, "symbol": app_symbol(p.symbol), "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": p.volume, "price_open": p.price_open, "price_current": p.price_current, "sl": p.sl, "tp": p.tp,
            "profit": p.profit, "swap": p.swap, "time": p.time - offset, "comment": p.comment} for p in pos]
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=30), now + timedelta(days=1))
    if deals is None:
        raise RuntimeError(f"history_deals_get failed: {mt5.last_error()}")
    by_pos: dict[int, dict] = {}
    for d in deals:
        if d.magic != MAGIC and d.entry != mt5.DEAL_ENTRY_OUT:
            continue
        g = by_pos.setdefault(d.position_id, {"profit": 0.0, "commission": 0.0, "swap": 0.0, "fee": 0.0, "out": None, "ours": False})
        g["ours"] |= d.magic == MAGIC
        g["profit"] += d.profit
        g["commission"] += d.commission
        g["swap"] += d.swap
        g["fee"] += getattr(d, "fee", 0.0)
        if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            g["out"] = d
    reasons = {mt5.DEAL_REASON_SL: "stop_loss", mt5.DEAL_REASON_TP: "take_profit", mt5.DEAL_REASON_SO: "stop_out",
               mt5.DEAL_REASON_CLIENT: "manual_terminal", mt5.DEAL_REASON_EXPERT: "closed_by_system"}
    closed = [{"position_ticket": pid, "price": g["out"].price, "profit": g["profit"], "commission": g["commission"] + g["fee"],
               "swap": g["swap"], "reason": reasons.get(g["out"].reason, "closed_at_broker"), "time": g["out"].time - offset}
              for pid, g in by_pos.items() if g["out"] is not None and g["ours"]]
    return {"positions": out, "closed": closed}


# --------------------------------------------------------------- execution
def filling_mode(info) -> int:
    fm = info.filling_mode
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def report(client_id: str, **kw) -> None:
    try:
        api("POST", f"/api/bridge/orders/{client_id}/result", kw)
    except Exception as e:
        log.error("Failed to report result for %s: %s", client_id, e)


def validate(o: dict) -> str | None:
    if not verify_order_signature(o):
        return "invalid order signature"
    if o.get("expires_at"):
        exp = datetime.fromisoformat(o["expires_at"])
        if exp < datetime.now(timezone.utc):
            return "order expired"
    acc = mt5.account_info()
    if o["mode"] == "MT5_DEMO" and acc.trade_mode != 0:
        return f"order is for a DEMO account but the terminal account trade_mode={acc.trade_mode}"
    if o["mode"] == "MT5_LIVE" and (acc.trade_mode != 2 or not ALLOW_REAL):
        return "live order refused (account is not real or ALLOW_REAL_ACCOUNT=false)"
    if o["volume"] <= 0 or o["volume"] > MAX_VOLUME:
        return f"volume {o['volume']} outside bridge limit {MAX_VOLUME}"
    if o["action"] == "OPEN":
        if not o.get("stop_loss"):
            return "missing stop-loss"
        b = broker_symbol(o["symbol"])
        if any(p.comment == f"qt:{o['client_id']}" for p in (mt5.positions_get(symbol=b) or [])):
            return "duplicate: position for this order already exists"
    return None


def execute(o: dict) -> None:
    cid = o["client_id"]
    err = validate(o)
    if err:
        log.warning("Refusing %s: %s", cid, err)
        report(cid, status="REJECTED", error=f"bridge: {err}")
        return
    b = broker_symbol(o["symbol"])
    info = mt5.symbol_info(b)
    tick = mt5.symbol_info_tick(b)
    if info is None or tick is None:
        report(cid, status="REJECTED", error="bridge: symbol/tick unavailable")
        return
    spread_pts = (tick.ask - tick.bid) / info.point
    if o["action"] == "OPEN" and spread_pts > MAX_SPREAD_POINTS:
        report(cid, status="REJECTED", error=f"bridge: spread {spread_pts:.0f} pts > {MAX_SPREAD_POINTS}")
        return
    buy = o["side"] == "BUY"
    price = tick.ask if buy else tick.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": b, "volume": float(o["volume"]),
        "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": price,
        "deviation": int(o.get("max_slippage_points") or 10), "magic": MAGIC,
        "comment": f"qt:{cid}"[:31], "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode(info),
    }
    if o["action"] == "OPEN":
        req["sl"] = round(float(o["stop_loss"]), info.digits)
        if o.get("take_profit"):
            req["tp"] = round(float(o["take_profit"]), info.digits)
    else:
        req["position"] = int(o["position_ticket"])
        pos = mt5.positions_get(ticket=req["position"])
        if not pos:
            report(cid, status="REJECTED", error="bridge: position not found (already closed?)")
            return
        req["volume"] = pos[0].volume
    check = mt5.order_check(req)
    if check is None or check.retcode not in (0, mt5.TRADE_RETCODE_DONE):
        report(cid, status="REJECTED", error=f"bridge: order_check failed {getattr(check, 'retcode', None)} {getattr(check, 'comment', mt5.last_error())}")
        return
    res = mt5.order_send(req)
    if res is None or res.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE_PARTIAL):
        report(cid, status="REJECTED", retcode=getattr(res, "retcode", None),
               error=f"bridge: order_send failed {getattr(res, 'comment', mt5.last_error())}")
        return
    log.info("%s %s %s %s @ %s ticket %s", cid, o["action"], o["side"], res.volume, res.price, res.order)

    if o["action"] == "CLOSE":
        report(cid, status="FILLED", ticket=res.order, price=res.price, volume=res.volume, retcode=res.retcode, sl_verified=True)
        return

    # Verify the position and its protective stop at the broker.
    time.sleep(0.5)
    position = None
    for _ in range(10):
        cands = [p for p in (mt5.positions_get(symbol=b) or []) if p.magic == MAGIC and p.comment == req["comment"]]
        if not cands and res.order:
            cands = list(mt5.positions_get(ticket=res.order) or [])
        if cands:
            position = cands[0]
            break
        time.sleep(0.5)
    if position is None:
        report(cid, status="FILLED", ticket=res.order, price=res.price, volume=res.volume, retcode=res.retcode,
               sl_verified=False, error="bridge: fill reported but position not found")
        return
    sl_ok = position.sl and abs(position.sl - req["sl"]) <= info.point * 2
    if not sl_ok:
        mod = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": position.ticket, "symbol": b,
                              "sl": req["sl"], "tp": req.get("tp", 0.0), "magic": MAGIC})
        position = (mt5.positions_get(ticket=position.ticket) or [position])[0]
        sl_ok = bool(position.sl) and getattr(mod, "retcode", 0) == mt5.TRADE_RETCODE_DONE
    if not sl_ok:
        # Fail closed: never leave an unprotected position open.
        log.error("Stop-loss could not be set for %s; closing position %s", cid, position.ticket)
        t2 = mt5.symbol_info_tick(b)
        mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": b, "volume": position.volume, "position": position.ticket,
                        "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, "price": t2.bid if buy else t2.ask,
                        "deviation": 50, "magic": MAGIC, "comment": "qt:sl-fail-close", "type_filling": filling_mode(info)})
    report(cid, status="FILLED", ticket=res.order, position_ticket=position.ticket, price=position.price_open,
           volume=position.volume, retcode=res.retcode, sl_verified=bool(sl_ok), sl=position.sl or None, tp=position.tp or None)


# -------------------------------------------------------------------- main
def main() -> None:
    if len(SECRET) < 32:
        print("Set BRIDGE_SECRET (32+ chars, same as the server .env) in bridge.env")
        sys.exit(1)
    connect()
    symbols, timeframes = ["EURUSD", "GBPUSD", "USDJPY"], ["M15", "H1"]
    last_specs = 0.0
    while True:
        started = time.time()
        try:
            if mt5.terminal_info() is None or mt5.account_info() is None:
                log.warning("MT5 terminal disconnected; reconnecting")
                mt5.shutdown()
                connect()
            term = mt5.terminal_info()
            hb = api("POST", "/api/bridge/heartbeat", {
                "account": account_payload(), "version": VERSION,
                "terminal": {"connected": term.connected, "trade_allowed": term.trade_allowed, "build": term.build,
                             "company": term.company},
            })
            symbols, timeframes = hb.get("symbols", symbols), hb.get("timeframes", timeframes)
            if time.time() - last_specs > 3600:
                api("POST", "/api/bridge/symbols", {"symbols": symbols_payload(symbols)})
                last_specs = time.time()
            offset = server_offset_seconds(symbols)
            api("POST", "/api/bridge/quotes", {"quotes": quotes_payload(symbols, offset)})
            push_candles(symbols, timeframes, offset)
            if term.trade_allowed:
                for o in api("GET", "/api/bridge/orders/pending").get("orders", []):
                    try:
                        execute(o)
                    except Exception as e:
                        log.exception("Execution error for %s", o.get("client_id"))
                        report(o["client_id"], status="REJECTED", error=f"bridge exception: {e}")
            api("POST", "/api/bridge/positions", positions_payload(offset))
        except Exception as e:
            log.error("Loop error: %s", e)
        time.sleep(max(0.5, LOOP_SECONDS - (time.time() - started)))


if __name__ == "__main__":
    main()
