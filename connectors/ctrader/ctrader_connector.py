"""Qivynex Trade — cTrader Open API connector.

Runs on the Linux VPS (Docker service "ctrader") — no Windows machine needed.
It speaks cTrader Open API (JSON over WebSocket) on one side and the app's
HMAC-signed bridge API on the other, exactly like the MT5 bridge:

  * pushes account state, symbol specs, live quotes and CLOSED candles
  * fetches signed order instructions, re-validates them, executes them
  * verifies the protective stop-loss at the broker (closes the position if it can't)
  * pushes positions + closing deals for reconciliation

Protocol facts (from spotware/openapi-proto-messages):
  prices in spot events / trendbars are integers in 1/100000 of a price unit,
  volumes are in cents of a unit (1 lot EURUSD = lotSize = 10_000_000),
  money values are integers scaled by 10**moneyDigits,
  trendbar times are UTC minutes, market orders need relativeStopLoss.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import logging
import math
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
import websockets

VERSION = "1.0.0"
log = logging.getLogger("ctrader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

# ------------------------------------------------------------------ config
ENV = os.environ
API_URL = ENV.get("API_URL", "http://backend:8000").rstrip("/")
KEY_ID = ENV.get("BRIDGE_KEY_ID", "mt5-bridge-1")
SECRET = ENV.get("BRIDGE_SECRET", "")
CLIENT_ID = ENV.get("CTRADER_CLIENT_ID", "")
CLIENT_SECRET = ENV.get("CTRADER_CLIENT_SECRET", "")
ACCESS_TOKEN = ENV.get("CTRADER_ACCESS_TOKEN", "")
REFRESH_TOKEN = ENV.get("CTRADER_REFRESH_TOKEN", "")
ACCOUNT_LOGIN = ENV.get("CTRADER_ACCOUNT_LOGIN", "")  # the account number shown in cTrader
HOST_DEMO = ENV.get("CTRADER_HOST_DEMO", "wss://demo.ctraderapi.com:5036")
HOST_LIVE = ENV.get("CTRADER_HOST_LIVE", "wss://live.ctraderapi.com:5036")
STATE_FILE = Path(ENV.get("CTRADER_STATE_FILE", "/data/ctrader_state.json"))
MAX_VOLUME = float(ENV.get("MAX_VOLUME", "0.10"))  # lots
MAX_SPREAD_POINTS = float(ENV.get("MAX_SPREAD_POINTS", "30"))
ALLOW_REAL = ENV.get("ALLOW_REAL_ACCOUNT", "false").lower() == "true"
LOOP_SECONDS = float(ENV.get("LOOP_SECONDS", "5"))
BACKFILL_BARS = int(ENV.get("BACKFILL_BARS", "5000"))
LABEL = ENV.get("ORDER_LABEL", "qt")
# Broker symbol names if they differ, e.g. "EURUSD=EURUSD.r"
SYMBOL_MAP = dict(p.split("=", 1) for p in ENV.get("SYMBOL_MAP", "").split(",") if "=" in p)

# Payload types (OpenApiModelMessages.proto)
PT = dict(
    ERROR_RES=50, HEARTBEAT=51,
    APP_AUTH_REQ=2100, APP_AUTH_RES=2101, ACCOUNT_AUTH_REQ=2102, ACCOUNT_AUTH_RES=2103,
    NEW_ORDER_REQ=2106, AMEND_SLTP_REQ=2110, CLOSE_POSITION_REQ=2111,
    ASSET_LIST_REQ=2112, ASSET_LIST_RES=2113, SYMBOLS_LIST_REQ=2114, SYMBOLS_LIST_RES=2115,
    SYMBOL_BY_ID_REQ=2116, SYMBOL_BY_ID_RES=2117, TRADER_REQ=2121, TRADER_RES=2122,
    RECONCILE_REQ=2124, RECONCILE_RES=2125, EXECUTION_EVENT=2126,
    SUBSCRIBE_SPOTS_REQ=2127, SUBSCRIBE_SPOTS_RES=2128, SPOT_EVENT=2131, ORDER_ERROR_EVENT=2132,
    DEAL_LIST_REQ=2133, DEAL_LIST_RES=2134, GET_TRENDBARS_REQ=2137, GET_TRENDBARS_RES=2138,
    OA_ERROR_RES=2142, TOKEN_INVALIDATED_EVENT=2147, CLIENT_DISCONNECT_EVENT=2148,
    ACCOUNTS_BY_TOKEN_REQ=2149, ACCOUNTS_BY_TOKEN_RES=2150, ACCOUNT_DISCONNECT_EVENT=2164,
    REFRESH_TOKEN_REQ=2173, REFRESH_TOKEN_RES=2174, UNREALIZED_PNL_REQ=2187, UNREALIZED_PNL_RES=2188,
)
PERIODS = {"M1": 1, "M5": 5, "M15": 7, "M30": 8, "H1": 9, "H4": 10, "D1": 12}
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
# Window per historical request (kept small to respect server limits).
TF_WINDOW_DAYS = {"M1": 1, "M5": 5, "M15": 14, "M30": 28, "H1": 56, "H4": 200, "D1": 1000}
BUY, SELL = 1, 2
MARKET = 1
SIDE_NAMES = {1: "BUY", 2: "SELL", "BUY": "BUY", "SELL": "SELL"}


class CTraderError(Exception):
    def __init__(self, code: str, description: str = ""):
        super().__init__(f"{code}: {description}")
        self.code = code


def i(v, default=0) -> int:
    """JSON int64 values may arrive as strings."""
    if v is None:
        return default
    return int(v)


def enum_is(v, name: str, num: int) -> bool:
    return v == name or v == num or (isinstance(v, str) and v.isdigit() and int(v) == num)


# --------------------------------------------------------------- app API
_http = requests.Session()


def _headers(method: str, path: str, body: bytes) -> dict:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    msg = "\n".join([ts, nonce, method, path, hashlib.sha256(body).hexdigest()])
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-Bridge-Key-Id": KEY_ID, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
            "Content-Type": "application/json", "User-Agent": f"qivynex-ctrader-connector/{VERSION}"}


def _app_call(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, default=str).encode() if payload is not None else b""
    r = _http.request(method, API_URL + path, data=body or None, headers=_headers(method, path, body), timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


async def app(method: str, path: str, payload: dict | None = None) -> dict:
    return await asyncio.to_thread(_app_call, method, path, payload)


def verify_order_signature(o: dict) -> bool:
    payload = {k: v for k, v in o.items() if k != "signature"}
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.compare_digest(hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest(), o.get("signature", ""))


# ---------------------------------------------------------------- tokens
def load_tokens() -> dict:
    try:
        st = json.loads(STATE_FILE.read_text())
        if st.get("access_token") and st.get("refresh_token"):
            return st
    except (OSError, ValueError):
        pass
    return {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "refreshed_at": 0}


def save_tokens(st: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        os.chmod(tmp, 0o600)
        tmp.replace(STATE_FILE)
    except OSError as e:
        log.error("Could not persist refreshed tokens to %s: %s", STATE_FILE, e)


# ------------------------------------------------------------ connection
class Client:
    """Minimal cTrader Open API JSON/WebSocket client with request/response matching."""

    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.listeners: list = []
        self.closed = asyncio.Event()

    async def connect(self):
        self.ws = await websockets.connect(self.url, ping_interval=None, max_size=32 * 1024 * 1024, open_timeout=20)
        self.closed.clear()
        asyncio.create_task(self._reader())
        asyncio.create_task(self._heartbeat())

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def _heartbeat(self):
        while not self.closed.is_set():
            await asyncio.sleep(10)
            try:
                await self.ws.send(json.dumps({"payloadType": PT["HEARTBEAT"], "payload": {}}))
            except Exception:
                break

    async def _reader(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                pt = msg.get("payloadType")
                cid = msg.get("clientMsgId")
                payload = msg.get("payload") or {}
                if cid and cid in self.pending and pt not in (PT["EXECUTION_EVENT"], PT["ORDER_ERROR_EVENT"]):
                    fut = self.pending.pop(cid)
                    if pt in (PT["OA_ERROR_RES"], PT["ERROR_RES"]):
                        fut.set_exception(CTraderError(payload.get("errorCode", "ERROR"), payload.get("description", "")))
                    elif not fut.done():
                        fut.set_result(payload)
                    continue
                for cb in list(self.listeners):
                    try:
                        cb(pt, payload, cid)
                    except Exception:
                        log.exception("listener failed")
        except websockets.ConnectionClosed as e:
            log.warning("cTrader connection closed: %s", e)
        finally:
            self.closed.set()
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("cTrader connection closed"))
            self.pending.clear()

    async def send(self, pt: int, payload: dict, timeout: float = 20) -> dict:
        cid = f"qt{next(self.ids)}"
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        await self.ws.send(json.dumps({"clientMsgId": cid, "payloadType": pt, "payload": payload}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(cid, None)

    async def send_nowait(self, pt: int, payload: dict) -> str:
        cid = f"qt{next(self.ids)}"
        await self.ws.send(json.dumps({"clientMsgId": cid, "payloadType": pt, "payload": payload}))
        return cid


# ------------------------------------------------------------- connector
@dataclass
class Sym:
    app_name: str
    symbol_id: int
    digits: int
    lot_size: int  # cents of units per lot
    min_volume: int
    max_volume: int
    step_volume: int
    base: str
    quote: str
    enabled: bool

    @property
    def point(self) -> float:
        return 10 ** -self.digits

    def lots(self, cents: int) -> float:
        return round(cents / self.lot_size, 4)

    def cents(self, lots: float) -> int:
        raw = int(round(lots * self.lot_size))
        return (raw // self.step_volume) * self.step_volume


@dataclass
class State:
    account_id: int = 0
    is_live: bool = False
    login: int = 0
    broker: str = ""
    money_digits: int = 2
    currency: str = "USD"
    leverage: float = 0
    symbols: dict = field(default_factory=dict)  # app name -> Sym
    by_id: dict = field(default_factory=dict)  # symbol id -> Sym
    quotes: dict = field(default_factory=dict)  # symbol id -> (bid, ask, ts)
    last_bar: dict = field(default_factory=dict)  # (sym, tf) -> last sent bar time


class Connector:
    def __init__(self):
        self.tokens = load_tokens()
        self.st = State()
        self.client: Client | None = None
        self.exec_events: asyncio.Queue | None = None
        self.symbols_wanted = ["EURUSD", "GBPUSD", "USDJPY"]
        self.timeframes = ["M15", "H1"]
        self.last_specs = 0.0

    # ---------------------------------------------------------- session
    async def start(self):
        if not (CLIENT_ID and CLIENT_SECRET and self.tokens["access_token"]):
            raise SystemExit("Set CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET and CTRADER_ACCESS_TOKEN/CTRADER_REFRESH_TOKEN")
        if len(SECRET) < 32:
            raise SystemExit("BRIDGE_SECRET must match the server .env (32+ chars)")
        # Discover the account (and whether it is demo or live) using the demo host first.
        accounts = await self._list_accounts()
        acc = self._pick_account(accounts)
        self.st.account_id = i(acc["ctidTraderAccountId"])
        self.st.is_live = bool(acc.get("isLive"))
        self.st.login = i(acc.get("traderLogin"))
        self.st.broker = acc.get("brokerTitleShort", "")
        log.info("Using cTrader account %s (%s) at %s", self.st.login, "LIVE" if self.st.is_live else "demo", self.st.broker)
        if self.st.is_live and not ALLOW_REAL:
            log.warning("LIVE account selected and ALLOW_REAL_ACCOUNT=false: orders will be refused; data will still flow.")
        await self._open(HOST_LIVE if self.st.is_live else HOST_DEMO)

    async def _list_accounts(self) -> list[dict]:
        c = Client(HOST_DEMO)
        await c.connect()
        try:
            await c.send(PT["APP_AUTH_REQ"], {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET})
            try:
                res = await c.send(PT["ACCOUNTS_BY_TOKEN_REQ"], {"accessToken": self.tokens["access_token"]})
            except CTraderError as e:
                if "TOKEN" not in e.code.upper():
                    raise
                await self._refresh(c)
                res = await c.send(PT["ACCOUNTS_BY_TOKEN_REQ"], {"accessToken": self.tokens["access_token"]})
            return res.get("ctidTraderAccount", [])
        finally:
            await c.close()

    def _pick_account(self, accounts: list[dict]) -> dict:
        if not accounts:
            raise SystemExit("The access token has no trading accounts. Grant access to your account in the Playground.")
        for a in accounts:
            log.info("  account login %s  live=%s  broker=%s", a.get("traderLogin"), a.get("isLive", False), a.get("brokerTitleShort"))
        if ACCOUNT_LOGIN:
            for a in accounts:
                if str(a.get("traderLogin")) == ACCOUNT_LOGIN.strip():
                    return a
            raise SystemExit(f"Account {ACCOUNT_LOGIN} not found for this token")
        demos = [a for a in accounts if not a.get("isLive")]
        if len(demos) == 1:
            return demos[0]
        raise SystemExit("Several accounts available: set CTRADER_ACCOUNT_LOGIN to the account number to use")

    async def _refresh(self, c: Client):
        if not self.tokens.get("refresh_token"):
            raise SystemExit("Access token expired and no refresh token configured")
        res = await c.send(PT["REFRESH_TOKEN_REQ"], {"refreshToken": self.tokens["refresh_token"]})
        self.tokens = {"access_token": res["accessToken"], "refresh_token": res["refreshToken"], "refreshed_at": time.time()}
        save_tokens(self.tokens)
        log.info("Access token refreshed and saved")

    async def _open(self, host: str):
        self.client = Client(host)
        self.exec_events = asyncio.Queue()
        self.client.listeners.append(self._on_event)
        await self.client.connect()
        c = self.client
        await c.send(PT["APP_AUTH_REQ"], {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET})
        # Refresh proactively every 20 days (tokens last ~30 days).
        if time.time() - float(self.tokens.get("refreshed_at") or 0) > 20 * 86400 and self.tokens.get("refresh_token"):
            await self._refresh(c)
        await c.send(PT["ACCOUNT_AUTH_REQ"], {"ctidTraderAccountId": self.st.account_id, "accessToken": self.tokens["access_token"]})
        await self._load_trader()
        await self._load_symbols()

    def _on_event(self, pt, payload, cid):
        if pt == PT["SPOT_EVENT"]:
            sid = i(payload.get("symbolId"))
            old = self.st.quotes.get(sid, (0, 0, 0))
            bid = i(payload.get("bid")) / 1e5 if payload.get("bid") is not None else old[0]
            ask = i(payload.get("ask")) / 1e5 if payload.get("ask") is not None else old[1]
            ts = i(payload.get("timestamp")) / 1000 if payload.get("timestamp") else time.time()
            self.st.quotes[sid] = (bid, ask, ts)
        elif pt in (PT["EXECUTION_EVENT"], PT["ORDER_ERROR_EVENT"], PT["OA_ERROR_RES"], PT["ERROR_RES"]):
            self.exec_events.put_nowait((pt, payload, cid))
        elif pt in (PT["TOKEN_INVALIDATED_EVENT"], PT["CLIENT_DISCONNECT_EVENT"], PT["ACCOUNT_DISCONNECT_EVENT"]):
            log.warning("cTrader session event %s: %s; reconnecting", pt, payload)
            asyncio.create_task(self.client.close())

    async def _load_trader(self) -> dict:
        res = await self.client.send(PT["TRADER_REQ"], {"ctidTraderAccountId": self.st.account_id})
        t = res["trader"]
        self.st.money_digits = i(t.get("moneyDigits"), 2)
        self.st.leverage = i(t.get("leverageInCents"), 0) / 100
        if not getattr(self, "_asset_names", None):
            assets = await self.client.send(PT["ASSET_LIST_REQ"], {"ctidTraderAccountId": self.st.account_id})
            names = {i(a["assetId"]): a["name"] for a in assets.get("asset", [])}
            self.st.currency = names.get(i(t.get("depositAssetId")), "USD")
            self._asset_names = names
        self.st.broker = t.get("brokerName", self.st.broker)
        return t

    async def _load_symbols(self):
        c, acc = self.client, self.st.account_id
        lst = await c.send(PT["SYMBOLS_LIST_REQ"], {"ctidTraderAccountId": acc})
        by_name = {s.get("symbolName"): s for s in lst.get("symbol", [])}
        wanted = {}
        for name in self.symbols_wanted:
            broker_name = SYMBOL_MAP.get(name, name)
            light = by_name.get(broker_name)
            if light is None:
                log.error("Symbol %s (%s) not found at broker", name, broker_name)
                continue
            wanted[i(light["symbolId"])] = (name, light)
        if not wanted:
            return
        full = await c.send(PT["SYMBOL_BY_ID_REQ"], {"ctidTraderAccountId": acc, "symbolId": list(wanted)})
        names = getattr(self, "_asset_names", {})
        for s in full.get("symbol", []):
            sid = i(s["symbolId"])
            app_name, light = wanted[sid]
            sym = Sym(app_name, sid, i(s["digits"]), i(s.get("lotSize"), 10_000_000), i(s.get("minVolume"), 100_000),
                      i(s.get("maxVolume"), 10_000_000_000), i(s.get("stepVolume"), 100_000),
                      names.get(i(light.get("baseAssetId")), app_name[:3]), names.get(i(light.get("quoteAssetId")), app_name[3:]),
                      enum_is(s.get("tradingMode", "ENABLED"), "ENABLED", 0) and light.get("enabled", True))
            self.st.symbols[app_name] = sym
            self.st.by_id[sid] = sym
        await c.send(PT["SUBSCRIBE_SPOTS_REQ"], {"ctidTraderAccountId": acc, "symbolId": list(self.st.by_id)})

    # ---------------------------------------------------------- account
    async def account_payload(self) -> dict:
        c, acc = self.client, self.st.account_id
        t = await self._load_trader()
        scale = 10 ** self.st.money_digits
        balance = i(t["balance"]) / scale
        rec = await c.send(PT["RECONCILE_REQ"], {"ctidTraderAccountId": acc})
        positions = rec.get("position", [])
        margin = sum(i(p.get("usedMargin")) / 10 ** i(p.get("moneyDigits"), self.st.money_digits) for p in positions)
        unreal = 0.0
        self._pnl = {}
        if positions:
            pnl = await c.send(PT["UNREALIZED_PNL_REQ"], {"ctidTraderAccountId": acc})
            md = 10 ** i(pnl.get("moneyDigits"), self.st.money_digits)
            for p in pnl.get("positionUnrealizedPnL", []):
                self._pnl[i(p["positionId"])] = i(p["netUnrealizedPnL"]) / md
            unreal = sum(self._pnl.values())
        self._positions = positions
        equity = balance + unreal
        return {"login": self.st.login, "server": f"cTrader {self.st.broker}", "trade_mode": 2 if self.st.is_live else 0,
                "currency": self.st.currency, "balance": round(balance, 2), "equity": round(equity, 2),
                "margin": round(margin, 2), "margin_free": round(equity - margin, 2), "leverage": int(self.st.leverage)}

    def symbols_payload(self) -> list[dict]:
        out = []
        for s in self.st.symbols.values():
            out.append({"symbol": s.app_name, "digits": s.digits, "point": s.point,
                        "trade_contract_size": s.lot_size / 100, "trade_tick_size": s.point,
                        "volume_min": s.lots(s.min_volume), "volume_max": s.lots(s.max_volume),
                        "volume_step": s.lots(s.step_volume), "currency_base": s.base, "currency_profit": s.quote,
                        "trade_allowed": s.enabled})
        return out

    def quotes_payload(self) -> list[dict]:
        out = []
        for sid, (bid, ask, ts) in self.st.quotes.items():
            s = self.st.by_id.get(sid)
            if s and bid > 0 and ask >= bid:
                out.append({"symbol": s.app_name, "bid": round(bid, s.digits), "ask": round(ask, s.digits), "time": int(ts)})
        return out

    # ---------------------------------------------------------- candles
    async def trendbars(self, s: Sym, tf: str, frm_ms: int, to_ms: int) -> list[dict]:
        res = await self.client.send(PT["GET_TRENDBARS_REQ"], {
            "ctidTraderAccountId": self.st.account_id, "symbolId": s.symbol_id, "period": PERIODS[tf],
            "fromTimestamp": frm_ms, "toTimestamp": to_ms}, timeout=60)
        out = []
        for b in res.get("trendbar", []):
            low = i(b.get("low"))
            out.append({"time": i(b["utcTimestampInMinutes"]) * 60,
                        "open": round((low + i(b.get("deltaOpen"))) / 1e5, s.digits),
                        "high": round((low + i(b.get("deltaHigh"))) / 1e5, s.digits),
                        "low": round(low / 1e5, s.digits),
                        "close": round((low + i(b.get("deltaClose"))) / 1e5, s.digits),
                        "tick_volume": i(b.get("volume"))})
        return out

    async def push_candles(self):
        now_ms = int(time.time() * 1000)
        for s in self.st.symbols.values():
            for tf in self.timeframes:
                key = (s.app_name, tf)
                step = TF_SECONDS[tf] * 1000
                bars: list[dict] = []
                if key not in self.st.last_bar:
                    window = TF_WINDOW_DAYS[tf] * 86400 * 1000
                    to = now_ms
                    while len(bars) < BACKFILL_BARS:
                        chunk = await self.trendbars(s, tf, to - window, to)
                        await asyncio.sleep(0.25)  # historical request rate limit
                        if not chunk:
                            break
                        bars = chunk + bars
                        to = chunk[0]["time"] * 1000 - 1
                else:
                    bars = await self.trendbars(s, tf, now_ms - 12 * step, now_ms)
                # Only closed bars (the server also discards the forming bar).
                closed = sorted({b["time"]: b for b in bars if (b["time"] * 1000 + step) <= now_ms}.values(), key=lambda b: b["time"])
                if not closed or self.st.last_bar.get(key) == closed[-1]["time"]:
                    continue
                for k in range(0, len(closed), 2000):
                    res = await app("POST", "/api/bridge/candles", {"symbol": s.app_name, "timeframe": tf, "candles": closed[k:k + 2000]})
                    if res.get("rejected"):
                        log.warning("%s %s: %s candles rejected by server validation", s.app_name, tf, res["rejected"])
                self.st.last_bar[key] = closed[-1]["time"]

    # --------------------------------------------------------- positions
    def positions_payload(self) -> list[dict]:
        out = []
        for p in getattr(self, "_positions", []):
            td = p["tradeData"]
            if td.get("label") != LABEL:
                continue
            s = self.st.by_id.get(i(td["symbolId"]))
            if s is None:
                continue
            pid = i(p["positionId"])
            out.append({"ticket": pid, "symbol": s.app_name, "side": SIDE_NAMES.get(td.get("tradeSide"), "BUY"),
                        "volume": s.lots(i(td["volume"])), "price_open": float(p.get("price", 0)),
                        "sl": float(p["stopLoss"]) if p.get("stopLoss") else 0, "tp": float(p["takeProfit"]) if p.get("takeProfit") else 0,
                        "profit": self._pnl.get(pid, 0.0), "swap": 0,
                        "time": i(td.get("openTimestamp")) / 1000 if td.get("openTimestamp") else time.time(),
                        "comment": td.get("comment", "")})
        return out

    async def closed_deals(self) -> list[dict]:
        now = int(time.time() * 1000)
        res = await self.client.send(PT["DEAL_LIST_REQ"], {"ctidTraderAccountId": self.st.account_id,
                                                           "fromTimestamp": now - 7 * 86400 * 1000, "toTimestamp": now, "maxRows": 1000})
        deals = res.get("deal", [])
        comm: dict[int, float] = {}
        for d in deals:
            md = 10 ** i(d.get("moneyDigits"), self.st.money_digits)
            comm[i(d["positionId"])] = comm.get(i(d["positionId"]), 0.0) + abs(i(d.get("commission")) / md)
        out = []
        for d in deals:
            cpd = d.get("closePositionDetail")
            if not cpd:
                continue
            md = 10 ** i(cpd.get("moneyDigits"), i(d.get("moneyDigits"), self.st.money_digits))
            pid = i(d["positionId"])
            out.append({"position_ticket": pid, "price": float(d.get("executionPrice", 0)),
                        "profit": i(cpd.get("grossProfit")) / md, "commission": -comm.get(pid, 0.0),
                        "swap": i(cpd.get("swap")) / md, "reason": "closed_at_broker",
                        "time": i(d.get("executionTimestamp")) / 1000})
        return out

    # --------------------------------------------------------- execution
    def validate(self, o: dict) -> str | None:
        if not verify_order_signature(o):
            return "invalid order signature"
        if o.get("expires_at") and datetime.fromisoformat(o["expires_at"]) < datetime.now(timezone.utc):
            return "order expired"
        if o["mode"] == "MT5_DEMO" and self.st.is_live:
            return "order is for a DEMO account but the connected cTrader account is LIVE"
        if o["mode"] == "MT5_LIVE" and (not self.st.is_live or not ALLOW_REAL):
            return "live order refused (account is demo or ALLOW_REAL_ACCOUNT=false)"
        if o["volume"] <= 0 or o["volume"] > MAX_VOLUME:
            return f"volume {o['volume']} outside connector limit {MAX_VOLUME}"
        if o["symbol"] not in self.st.symbols:
            return f"symbol {o['symbol']} not available"
        if o["action"] == "OPEN":
            if not o.get("stop_loss"):
                return "missing stop-loss"
            for p in getattr(self, "_positions", []):
                if p["tradeData"].get("comment") == o["client_id"]:
                    return "duplicate: position for this order already exists"
        return None

    async def report(self, cid: str, **kw):
        try:
            await app("POST", f"/api/bridge/orders/{cid}/result", kw)
        except Exception as e:
            log.error("Failed to report result for %s: %s", cid, e)

    async def wait_fill(self, client_order_id: str, req_cid: str, timeout: float = 20) -> tuple[str, dict]:
        """Wait for ORDER_FILLED / rejection of our order. Returns (status, event payload)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pt, p, cid = await asyncio.wait_for(self.exec_events.get(), deadline - time.time())
            except asyncio.TimeoutError:
                break
            order = p.get("order") or {}
            mine = cid == req_cid or bool(client_order_id and order.get("clientOrderId") == client_order_id)
            if not mine:
                continue
            if pt in (PT["ORDER_ERROR_EVENT"], PT["OA_ERROR_RES"], PT["ERROR_RES"]):
                return "REJECTED", p
            et = p.get("executionType")
            if enum_is(et, "ORDER_FILLED", 3) or enum_is(et, "ORDER_PARTIAL_FILL", 11):
                return "FILLED", p
            if any(enum_is(et, n, v) for n, v in (("ORDER_REJECTED", 7), ("ORDER_CANCELLED", 5), ("ORDER_EXPIRED", 6))):
                return "REJECTED", p
        return "TIMEOUT", {}

    async def execute(self, o: dict):
        cid = o["client_id"]
        err = self.validate(o)
        if err:
            log.warning("Refusing %s: %s", cid, err)
            return await self.report(cid, status="REJECTED", error=f"connector: {err}")
        s = self.st.symbols[o["symbol"]]
        bid, ask, ts = self.st.quotes.get(s.symbol_id, (0, 0, 0))
        if bid <= 0 or time.time() - ts > 120:
            return await self.report(cid, status="REJECTED", error="connector: no fresh quote")
        if o["action"] == "OPEN" and (ask - bid) / s.point > MAX_SPREAD_POINTS:
            return await self.report(cid, status="REJECTED", error=f"connector: spread {(ask - bid) / s.point:.0f} pts > {MAX_SPREAD_POINTS}")
        acc = self.st.account_id

        if o["action"] == "CLOSE":
            pos = next((p for p in getattr(self, "_positions", []) if str(i(p["positionId"])) == str(o["position_ticket"])), None)
            if pos is None:
                return await self.report(cid, status="REJECTED", error="connector: position not found (already closed?)")
            req_cid = await self.client.send_nowait(PT["CLOSE_POSITION_REQ"], {"ctidTraderAccountId": acc, "positionId": i(pos["positionId"]),
                                                                            "volume": i(pos["tradeData"]["volume"])})
            status, ev = await self.wait_fill("", req_cid)
            if status != "FILLED":
                return await self.report(cid, status="REJECTED", error=f"connector: close {status} {ev.get('errorCode', '')} {ev.get('description', '')}")
            deal = ev.get("deal") or {}
            return await self.report(cid, status="FILLED", ticket=i(deal.get("dealId")), price=float(deal.get("executionPrice", 0)),
                                     volume=s.lots(i(deal.get("filledVolume", pos["tradeData"]["volume"]))), sl_verified=True)

        buy = o["side"] == "BUY"
        volume = s.cents(o["volume"])
        if volume < s.min_volume:
            return await self.report(cid, status="REJECTED", error="connector: volume below broker minimum")
        ref = ask if buy else bid
        sl = round(float(o["stop_loss"]), s.digits)
        tp = round(float(o["take_profit"]), s.digits) if o.get("take_profit") else None
        if (buy and sl >= ref) or (not buy and sl <= ref):
            return await self.report(cid, status="REJECTED", error="connector: stop-loss on wrong side of market")
        # Market orders only accept RELATIVE stops (in 1/100000 of price, at symbol precision).
        unit = 10 ** (5 - s.digits)
        rel_sl = int(round(abs(ref - sl) * 1e5 / unit)) * unit
        req = {"ctidTraderAccountId": acc, "symbolId": s.symbol_id, "orderType": MARKET, "tradeSide": BUY if buy else SELL,
               "volume": volume, "relativeStopLoss": rel_sl, "label": LABEL, "comment": cid[:100], "clientOrderId": cid[:50]}
        if tp:
            req["relativeTakeProfit"] = int(round(abs(tp - ref) * 1e5 / unit)) * unit
        req_cid = await self.client.send_nowait(PT["NEW_ORDER_REQ"], req)
        status, ev = await self.wait_fill(cid[:50], req_cid)
        if status != "FILLED":
            return await self.report(cid, status="REJECTED", error=f"connector: {status} {ev.get('errorCode', '')} {ev.get('description', '')}".strip())
        pos = ev.get("position") or {}
        pid = i(pos.get("positionId"))
        fill = float(pos.get("price") or (ev.get("deal") or {}).get("executionPrice") or ref)
        log.info("%s filled %s %s @ %s position %s", cid, o["side"], o["volume"], fill, pid)

        # Set the exact absolute SL/TP requested by the risk engine, then verify at the broker.
        # The relative stop sent with the order already protects the position; after slippage it
        # may differ from the requested level by up to the allowed slippage.
        tol = (int(o.get("max_slippage_points") or 10) + 2) * s.point
        sl_ok = False
        try:
            amend = {"ctidTraderAccountId": acc, "positionId": pid, "stopLoss": sl}
            if tp:
                amend["takeProfit"] = tp
            await self.client.send_nowait(PT["AMEND_SLTP_REQ"], amend)
            for _ in range(10):
                await asyncio.sleep(0.5)
                rec = await self.client.send(PT["RECONCILE_REQ"], {"ctidTraderAccountId": acc})
                p = next((x for x in rec.get("position", []) if i(x["positionId"]) == pid), None)
                if p and p.get("stopLoss"):
                    pos = p
                    if abs(float(p["stopLoss"]) - sl) <= s.point * 2:
                        sl_ok = True
                        break
            if not sl_ok and pos.get("stopLoss") and abs(float(pos["stopLoss"]) - sl) <= tol:
                sl_ok = True
        except Exception as e:
            log.error("SL amend/verify failed for %s: %s", cid, e)
        if not sl_ok and not (pos.get("stopLoss")):
            log.error("No stop-loss at broker for %s; closing position %s", cid, pid)
            await self.client.send_nowait(PT["CLOSE_POSITION_REQ"], {"ctidTraderAccountId": acc, "positionId": pid, "volume": volume})
        await self.report(cid, status="FILLED", ticket=pid, position_ticket=pid, price=fill, volume=s.lots(volume),
                          sl_verified=bool(sl_ok), sl=float(pos["stopLoss"]) if pos.get("stopLoss") else None,
                          tp=float(pos["takeProfit"]) if pos.get("takeProfit") else None)

    # --------------------------------------------------------------- loop
    async def cycle(self):
        acct = await self.account_payload()
        hb = await app("POST", "/api/bridge/heartbeat", {"account": acct, "version": f"ctrader-{VERSION}",
                                                        "terminal": {"connected": True, "trade_allowed": True,
                                                                     "company": self.st.broker, "platform": "cTrader Open API"}})
        wanted, tfs = hb.get("symbols", self.symbols_wanted), [t for t in hb.get("timeframes", self.timeframes) if t in PERIODS]
        if set(wanted) != set(self.symbols_wanted):
            self.symbols_wanted = wanted
            await self._load_symbols()
            self.last_specs = 0
        self.timeframes = tfs
        if time.time() - self.last_specs > 3600:
            await app("POST", "/api/bridge/symbols", {"symbols": self.symbols_payload()})
            self.last_specs = time.time()
        await app("POST", "/api/bridge/quotes", {"quotes": self.quotes_payload()})
        await self.push_candles()
        for o in (await app("GET", "/api/bridge/orders/pending")).get("orders", []):
            try:
                await self.execute(o)
            except Exception as e:
                log.exception("Execution error for %s", o.get("client_id"))
                await self.report(o["client_id"], status="REJECTED", error=f"connector exception: {e}")
        await self.account_payload()  # refresh positions after executions
        await app("POST", "/api/bridge/positions", {"positions": self.positions_payload(), "closed": await self.closed_deals()})

    async def run(self):
        backoff = 5
        while True:
            try:
                await self.start()
                backoff = 5
                while not self.client.closed.is_set():
                    started = time.time()
                    try:
                        await self.cycle()
                    except (ConnectionError, websockets.ConnectionClosed):
                        raise
                    except Exception as e:
                        log.error("Cycle error: %s", e)
                    await asyncio.sleep(max(0.5, LOOP_SECONDS - (time.time() - started)))
            except SystemExit:
                raise
            except Exception as e:
                log.error("Connection error: %s (retrying in %ss)", e, backoff)
            try:
                if self.client:
                    await self.client.close()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


if __name__ == "__main__":
    try:
        asyncio.run(Connector().run())
    except KeyboardInterrupt:
        pass
