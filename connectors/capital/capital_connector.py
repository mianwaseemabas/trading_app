"""Qivynex Trade — Capital.com connector (REST API).

Runs on the Linux VPS (Docker service "capital"). Same job as the MT5 bridge
and the cTrader connector, using the HMAC-signed bridge API of the app:

  * pushes account state, instrument specs, quotes and CLOSED candles
  * fetches signed order instructions, re-validates and executes them with a
    stop-loss attached, confirms the deal and verifies the stop at the broker
  * reports open positions and closed trades for reconciliation

Capital.com API facts used here (open-api.capital.com):
  POST /session with X-CAP-API-KEY + identifier/password -> CST, X-SECURITY-TOKEN headers
  sessions expire after 10 minutes of inactivity -> GET /ping keeps them alive
  GET /markets/{epic} -> instrument (lotSize, currency), dealingRules, snapshot (bid, offer, decimalPlacesFactor)
  GET /prices/{epic}?resolution=HOUR&max=1000&from=&to= -> prices[] with bid/ask OHLC, snapshotTimeUTC
  POST /positions {epic, direction, size, stopLevel, profitLevel} -> dealReference
  GET /confirms/{dealReference} -> dealStatus ACCEPTED/REJECTED, affectedDeals[].dealId
  DELETE /positions/{dealId}, GET /positions, GET /history/activity
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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

VERSION = "1.0.0"
log = logging.getLogger("capital")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

E = os.environ
API_URL = E.get("API_URL", "http://backend:8000").rstrip("/")
KEY_ID = E.get("BRIDGE_KEY_ID", "mt5-bridge-1")
SECRET = E.get("BRIDGE_SECRET", "")
CAP_API_KEY = E.get("CAPITAL_API_KEY", "")
CAP_IDENTIFIER = E.get("CAPITAL_IDENTIFIER", "")  # login email
CAP_PASSWORD = E.get("CAPITAL_API_PASSWORD", "")  # the custom password set for the API key
CAP_ENV = E.get("CAPITAL_ENV", "demo").lower()
CAP_ACCOUNT_ID = E.get("CAPITAL_ACCOUNT_ID", "")
BASE_DEMO = E.get("CAPITAL_BASE_DEMO", "https://demo-api-capital.backend-capital.com")
BASE_LIVE = E.get("CAPITAL_BASE_LIVE", "https://api-capital.backend-capital.com")
STATE_FILE = Path(E.get("CAPITAL_STATE_FILE", "/data/capital_state.json"))
MAX_VOLUME = float(E.get("MAX_VOLUME", "0.10"))  # app lots (1 lot = 100,000 units of base currency)
MAX_SPREAD_POINTS = float(E.get("MAX_SPREAD_POINTS", "30"))
ALLOW_REAL = E.get("ALLOW_REAL_ACCOUNT", "false").lower() == "true"
LOOP_SECONDS = float(E.get("LOOP_SECONDS", "5"))
BACKFILL_BARS = int(E.get("BACKFILL_BARS", "5000"))
UNITS_PER_APP_LOT = 100_000  # FX standard lot
UNITS_PER_SIZE_OVERRIDE = E.get("CAPITAL_UNITS_PER_SIZE", "")  # optional manual override
EPIC_MAP = dict(p.split("=", 1) for p in E.get("SYMBOL_MAP", "").split(",") if "=" in p)

RESOLUTION = {"M1": "MINUTE", "M5": "MINUTE_5", "M15": "MINUTE_15", "M30": "MINUTE_30",
              "H1": "HOUR", "H4": "HOUR_4", "D1": "DAY"}
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
IS_LIVE = CAP_ENV == "live"
BASE = (BASE_LIVE if IS_LIVE else BASE_DEMO).rstrip("/") + "/api/v1"


class Cfg:
    """Runtime settings. Normally managed from the dashboard (Brokers page); .env values are a fallback."""
    active = True
    version = 0
    identifier, api_key, api_password, account_id = CAP_IDENTIFIER, CAP_API_KEY, CAP_PASSWORD, CAP_ACCOUNT_ID
    is_live, allow_real = IS_LIVE, ALLOW_REAL
    max_volume, max_spread = MAX_VOLUME, MAX_SPREAD_POINTS

    @classmethod
    def base(cls) -> str:
        return (BASE_LIVE if cls.is_live else BASE_DEMO).rstrip("/") + "/api/v1"

    @classmethod
    def apply(cls, d: dict) -> bool:
        """Apply dashboard config; returns True when the connection must be re-established."""
        before = (cls.identifier, cls.api_key, cls.api_password, cls.account_id, cls.is_live)
        cls.active = bool(d.get("active"))
        cls.version = d.get("version", 0)
        if d.get("api_key") and d.get("identifier"):
            cls.identifier, cls.api_key, cls.api_password = d["identifier"], d["api_key"], d.get("api_password", "")
            cls.account_id = d.get("account_id", "")
            cls.is_live = d.get("environment", "demo") == "live"
        cls.allow_real = bool(d.get("allow_real"))
        cls.max_volume = float(d.get("max_volume", cls.max_volume))
        cls.max_spread = float(d.get("max_spread_points", cls.max_spread))
        return before != (cls.identifier, cls.api_key, cls.api_password, cls.account_id, cls.is_live)


class CapitalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


# --------------------------------------------------------------- app API
_app = requests.Session()


def _headers(method: str, path: str, body: bytes) -> dict:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    # The server signs the URL path only (no query string).
    msg = "\n".join([ts, nonce, method, path.split("?")[0], hashlib.sha256(body).hexdigest()])
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-Bridge-Key-Id": KEY_ID, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
            "Content-Type": "application/json", "User-Agent": f"qivynex-capital-connector/{VERSION}", "X-Connector": "capital"}


def app(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, default=str).encode() if payload is not None else b""
    r = _app.request(method, API_URL + path, data=body or None, headers=_headers(method, path, body), timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def verify_order_signature(o: dict) -> bool:
    payload = {k: v for k, v in o.items() if k != "signature"}
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.compare_digest(hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest(), o.get("signature", ""))


# --------------------------------------------------------- Capital client
class Capital:
    def __init__(self, base: str | None = None):
        self.base = base or Cfg.base()
        self.http = requests.Session()
        self.cst = self.xst = None
        self.last_call = 0.0
        self.session_info: dict = {}

    def login(self):
        r = self.http.post(f"{self.base}/session", headers={"X-CAP-API-KEY": Cfg.api_key, "Content-Type": "application/json"},
                           json={"identifier": Cfg.identifier, "password": Cfg.api_password, "encryptedPassword": False}, timeout=30)
        if r.status_code >= 400:
            raise CapitalError(r.status_code, self._err(r))
        self.cst, self.xst = r.headers.get("CST"), r.headers.get("X-SECURITY-TOKEN")
        if not self.cst or not self.xst:
            raise CapitalError(r.status_code, "login response missing CST / X-SECURITY-TOKEN")
        self.session_info = r.json()
        self.last_call = time.time()
        if Cfg.account_id and self.session_info.get("currentAccountId") != Cfg.account_id:
            self.call("PUT", "/session", {"accountId": Cfg.account_id})
        log.info("Logged in to Capital.com %s, account %s", "LIVE" if Cfg.is_live else "demo",
                 Cfg.account_id or self.session_info.get("currentAccountId"))

    @staticmethod
    def _err(r) -> str:
        try:
            j = r.json()
            return str(j.get("errorCode") or j.get("message") or j)
        except ValueError:
            return r.text[:200]

    def call(self, method: str, path: str, body: dict | None = None, params: dict | None = None, _retry: bool = True):
        if self.cst is None or time.time() - self.last_call > 540:
            self.login()
        # Capital.com: max 10 requests/second per user.
        time.sleep(max(0.0, 0.12 - (time.time() - self.last_call)))
        r = self.http.request(method, f"{self.base}{path}", json=body, params=params, timeout=30,
                              headers={"X-CAP-API-KEY": Cfg.api_key, "CST": self.cst, "X-SECURITY-TOKEN": self.xst,
                                       "Content-Type": "application/json"})
        self.last_call = time.time()
        if r.status_code in (401, 403) and _retry:
            self.cst = None
            time.sleep(1.1)  # POST /session is limited to 1/s
            return self.call(method, path, body, params, _retry=False)
        if r.status_code >= 400:
            raise CapitalError(r.status_code, self._err(r))
        return r.json() if r.text else {}


# -------------------------------------------------------------- connector
@dataclass
class Market:
    app_name: str
    epic: str
    digits: int
    units_per_size: float  # base-currency units represented by 1.0 of Capital.com "size"
    min_size: float
    max_size: float
    size_step: float
    base: str
    quote: str
    tradeable: bool
    bid: float = 0.0
    ask: float = 0.0
    quote_time: float = 0.0

    @property
    def point(self) -> float:
        return 10 ** -self.digits

    def size_unit_problem(self) -> str | None:
        """Refuse to trade when the meaning of 'size' is ambiguous (protects against 100,000x errors)."""
        if UNITS_PER_SIZE_OVERRIDE:
            return None
        if self.units_per_size == 1 and self.min_size < 1:
            return (f"{self.app_name}: lotSize=1 but minDealSize={self.min_size}; size units unclear. "
                    "Set CAPITAL_UNITS_PER_SIZE in .env (1 if size is in units, 100000 if in lots).")
        if self.units_per_size >= 1000 and self.min_size >= 1:
            return (f"{self.app_name}: lotSize={self.units_per_size} with minDealSize={self.min_size}; size units unclear. "
                    "Set CAPITAL_UNITS_PER_SIZE in .env.")
        return None

    def lots(self, size: float) -> float:
        return round(size * self.units_per_size / UNITS_PER_APP_LOT, 4)

    def size(self, lots: float) -> float:
        raw = lots * UNITS_PER_APP_LOT / self.units_per_size
        steps = int(raw / self.size_step + 1e-9)
        return round(steps * self.size_step, 8)


def parse_utc(s: str) -> float:
    s = s.replace("/", "-").replace(" ", "T")
    return datetime.fromisoformat(s.split(".")[0]).replace(tzinfo=timezone.utc).timestamp()


def fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Connector:
    def __init__(self, cap: Capital | None = None):
        self.cap = cap or Capital()
        self.markets: dict[str, Market] = {}
        self.symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        self.timeframes = ["M15", "H1"]
        self.last_bar: dict = {}
        self.last_specs = 0.0
        self.positions: list[dict] = []
        self.state = self._load_state()
        self.leverage = 30
        self.windows: dict[str, int] = {}

    # ---------------------------------------------------------- state
    def _load_state(self) -> dict:
        try:
            st = json.loads(STATE_FILE.read_text())
            st.setdefault("ours", {})
            return st
        except (OSError, ValueError):
            return {"ours": {}, "initialized": False}

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state))
            tmp.replace(STATE_FILE)
        except OSError as e:
            log.error("Could not save state to %s: %s", STATE_FILE, e)

    # -------------------------------------------------------- markets
    def load_market(self, name: str) -> Market | None:
        epic = EPIC_MAP.get(name, name)
        d = self.cap.call("GET", f"/markets/{epic}")
        inst, rules, snap = d.get("instrument", {}), d.get("dealingRules", {}), d.get("snapshot", {})
        rules = rules or inst.get("dealingRules", {})
        lot = float(UNITS_PER_SIZE_OVERRIDE or inst.get("lotSize") or 1)
        digits = int(snap.get("decimalPlacesFactor") or (3 if name.endswith("JPY") else 5))
        ccy = inst.get("currency") or name[3:]
        m = Market(name, epic, digits, lot,
                   float((rules.get("minDealSize") or {}).get("value") or 1),
                   float((rules.get("maxDealSize") or {}).get("value") or 1e9),
                   float((rules.get("minSizeIncrement") or {}).get("value") or 1),
                   name[:3], ccy if len(ccy) == 3 else name[3:],
                   snap.get("marketStatus", "TRADEABLE") == "TRADEABLE")
        m.bid, m.ask = float(snap.get("bid") or 0), float(snap.get("offer") or 0)
        m.quote_time = time.time()
        if name not in self.markets:
            log.info("%s: epic %s, 1 size = %s units, min %s, step %s, digits %s",
                     name, epic, m.units_per_size, m.min_size, m.size_step, m.digits)
        self.markets[name] = m
        return m

    def refresh_quotes(self):
        for name in self.symbols:
            try:
                self.load_market(name)
            except CapitalError as e:
                log.error("Market %s unavailable: %s", name, e)
                self.markets.pop(name, None)

    def symbols_payload(self) -> list[dict]:
        return [{"symbol": m.app_name, "digits": m.digits, "point": m.point, "trade_contract_size": UNITS_PER_APP_LOT,
                 "trade_tick_size": m.point, "volume_min": m.lots(m.min_size), "volume_max": m.lots(m.max_size),
                 "volume_step": max(0.01, m.lots(m.size_step)), "currency_base": m.base, "currency_profit": m.quote,
                 "trade_allowed": m.tradeable} for m in self.markets.values()]

    def quotes_payload(self) -> list[dict]:
        return [{"symbol": m.app_name, "bid": m.bid, "ask": m.ask, "time": int(m.quote_time)}
                for m in self.markets.values() if m.bid > 0 and m.ask >= m.bid]

    # -------------------------------------------------------- candles
    def candles(self, m: Market, tf: str, frm: float | None, to: float | None, maxn: int = 1000) -> list[dict]:
        params = {"resolution": RESOLUTION[tf], "max": maxn}
        if frm is not None:
            params["from"] = fmt(frm)
        if to is not None:
            params["to"] = fmt(to)
        d = self.cap.call("GET", f"/prices/{m.epic}", params=params)
        out = []
        for p in d.get("prices", []):
            o, h, l, c = p.get("openPrice", {}), p.get("highPrice", {}), p.get("lowPrice", {}), p.get("closePrice", {})
            if o.get("bid") is None or c.get("bid") is None:
                continue
            spread = (float(c.get("ask") or c["bid"]) - float(c["bid"])) / m.point
            out.append({"time": int(parse_utc(p["snapshotTimeUTC"])), "open": float(o["bid"]), "high": float(h["bid"]),
                        "low": float(l["bid"]), "close": float(c["bid"]), "tick_volume": p.get("lastTradedVolume") or 0,
                        "spread": round(spread)})
        return out

    def _window_chunk(self, m: Market, tf: str, to: float) -> list[dict]:
        """One historical chunk ending at `to`. Capital.com limits the from/to span per request
        (error.invalid.max.daterange); the span is halved until accepted and remembered per timeframe."""
        step = TF_SECONDS[tf]
        bars = self.windows.get(tf, 1000)
        while True:
            try:
                return self.candles(m, tf, to - bars * step, to, min(1000, bars + 1))
            except CapitalError as e:
                if "daterange" not in str(e).lower() or bars <= 10:
                    raise
                bars //= 2
                self.windows[tf] = bars
                log.info("%s history window reduced to %s bars per request", tf, bars)

    def push_candles(self):
        now = time.time()
        for m in list(self.markets.values()):
            for tf in self.timeframes:
                try:
                    self._push_candles_one(m, tf, now)
                except Exception as e:  # never let history problems block trading/reconciliation
                    log.error("Candles %s %s failed: %s", m.app_name, tf, e)

    def _push_candles_one(self, m: Market, tf: str, now: float):
        key, step = (m.app_name, tf), TF_SECONDS[tf]
        bars: dict[int, dict] = {}
        if key not in self.last_bar:
            to = now
            empty = 0
            while len(bars) < BACKFILL_BARS:
                chunk = self._window_chunk(m, tf, to)
                new = [b for b in chunk if b["time"] not in bars]
                if not new:
                    empty += 1
                    if empty >= 3:  # e.g. weekend / no older data
                        break
                    to -= self.windows.get(tf, 1000) * step
                    continue
                empty = 0
                bars.update({b["time"]: b for b in new})
                to = min(b["time"] for b in new) - 1
        else:
            # Latest bars only: no date range, just the most recent `max` values.
            bars = {b["time"]: b for b in self.candles(m, tf, None, None, 20)}
        closed = sorted((b for t, b in bars.items() if t + step <= now), key=lambda b: b["time"])
        if not closed or self.last_bar.get(key) == closed[-1]["time"]:
            return
        for k in range(0, len(closed), 2000):
            res = app("POST", "/api/bridge/candles", {"symbol": m.app_name, "timeframe": tf, "candles": closed[k:k + 2000]})
            if res.get("rejected"):
                log.warning("%s %s: %s candles rejected by server validation", m.app_name, tf, res["rejected"])
        self.last_bar[key] = closed[-1]["time"]

    # -------------------------------------------------------- account
    def account_payload(self) -> dict:
        accs = self.cap.call("GET", "/accounts").get("accounts", [])
        cur = Cfg.account_id or self.cap.session_info.get("currentAccountId")
        acc = next((a for a in accs if a.get("accountId") == cur), None) or next((a for a in accs if a.get("preferred")), accs[0] if accs else {})
        b = acc.get("balance", {})
        balance, pl, avail = float(b.get("balance", 0)), float(b.get("profitLoss", 0)), float(b.get("available", 0))
        equity = balance + pl
        return {"login": acc.get("accountId", ""), "server": f"Capital.com {'LIVE' if Cfg.is_live else 'demo'} ({acc.get('accountName', '')})",
                "trade_mode": 2 if Cfg.is_live else 0, "currency": acc.get("currency", "USD"), "balance": round(balance, 2),
                "equity": round(equity, 2), "margin": round(max(0.0, equity - avail), 2), "margin_free": round(avail, 2),
                "leverage": self.leverage}

    def load_leverage(self):
        try:
            prefs = self.cap.call("GET", "/accounts/preferences")
            lev = (prefs.get("leverages") or {}).get("CURRENCIES") or {}
            self.leverage = int(lev.get("current") or self.leverage)
        except Exception as e:
            log.warning("Could not read leverage preferences: %s", e)

    # ------------------------------------------------------ positions
    def load_positions(self):
        self.positions = self.cap.call("GET", "/positions").get("positions", [])
        by_epic = {m.epic: m for m in self.markets.values()}
        if not self.state.get("initialized"):
            # First run without saved state: treat existing positions on traded markets as ours,
            # so any unexplained position is visible to the app's reconciliation.
            for p in self.positions:
                if p.get("market", {}).get("epic") in by_epic:
                    self.state["ours"][p["position"]["dealId"]] = {"epic": p["market"]["epic"]}
            self.state["initialized"] = True
            self._save_state()

    def positions_payload(self) -> list[dict]:
        by_epic = {m.epic: m for m in self.markets.values()}
        out = []
        for p in self.positions:
            pos, mk = p.get("position", {}), p.get("market", {})
            if pos.get("dealId") not in self.state["ours"] or mk.get("epic") not in by_epic:
                continue
            m = by_epic[mk["epic"]]
            sl = pos.get("stopLevel")
            out.append({"ticket": pos["dealId"], "symbol": m.app_name, "side": pos.get("direction", "BUY"),
                        "volume": m.lots(float(pos.get("size", 0))), "price_open": float(pos.get("level", 0)),
                        "price_current": float((mk.get("bid") if pos.get("direction") == "BUY" else mk.get("offer")) or 0),
                        "sl": float(sl) if sl else (self.state["ours"][pos["dealId"]].get("sl") or 0),
                        "tp": float(pos.get("profitLevel") or 0), "profit": float(pos.get("upl", 0)), "swap": 0,
                        "time": parse_utc(pos["createdDateUTC"]) if pos.get("createdDateUTC") else time.time()})
        return out

    def external_payload(self) -> list[dict]:
        """Positions on the account NOT opened by the app (e.g. placed by hand) - shown read-only in the dashboard."""
        out = []
        for p in self.positions:
            pos, mk = p.get("position", {}), p.get("market", {})
            if pos.get("dealId") in self.state["ours"]:
                continue
            out.append({"ticket": pos.get("dealId"), "symbol": mk.get("epic") or mk.get("instrumentName"),
                        "side": pos.get("direction"), "size": pos.get("size"), "open_price": pos.get("level"),
                        "sl": pos.get("stopLevel"), "tp": pos.get("profitLevel"), "pnl": pos.get("upl"),
                        "current": mk.get("bid") if pos.get("direction") == "BUY" else mk.get("offer")})
        return out

    def closed_payload(self) -> list[dict]:
        """Our tracked positions that disappeared from /positions -> report as closed."""
        open_ids = {p.get("position", {}).get("dealId") for p in self.positions}
        out = []
        for deal_id, info in list(self.state["ours"].items()):
            if deal_id in open_ids:
                continue
            m = next((x for x in self.markets.values() if x.epic == info.get("epic")), None)
            close_px, reason = None, "closed_at_broker"
            try:
                acts = self.cap.call("GET", "/history/activity", params={"dealId": deal_id, "detailed": "true", "lastPeriod": 86400})
                for a in acts.get("activities", []):
                    det = a.get("details") or {}
                    if det.get("level") and str(a.get("status", "")).upper() in ("ACCEPTED", "CLOSED", "EXECUTED"):
                        close_px = float(det["level"])
                    src = str(a.get("source", "")).upper()
                    reason = {"SL": "stop_loss", "TP": "take_profit", "USER": "manual", "CLOSE_OUT": "stop_out"}.get(src, reason)
            except Exception as e:
                log.warning("Activity lookup for %s failed: %s", deal_id, e)
            if close_px is None and info.get("closed_price"):
                close_px = info["closed_price"]
            if close_px is None and m:
                close_px = m.bid if info.get("side") == "BUY" else m.ask
            profit = 0.0
            if m and close_px and info.get("open") and info.get("units"):
                diff = (close_px - info["open"]) if info.get("side") == "BUY" else (info["open"] - close_px)
                conv = 1.0 if m.quote == "USD" else (1 / close_px if m.base == "USD" else 1.0)
                profit = diff * info["units"] * conv  # estimate: Capital.com charges spread, no commission
            out.append({"position_ticket": deal_id, "price": close_px or 0, "profit": round(profit, 2), "commission": 0,
                        "swap": 0, "reason": reason, "time": time.time()})
            info["reported"] = info.get("reported", 0) + 1
            if info["reported"] >= 3:
                del self.state["ours"][deal_id]
        self._save_state()
        return out

    # ------------------------------------------------------ execution
    def validate(self, o: dict) -> str | None:
        if not verify_order_signature(o):
            return "invalid order signature"
        if o.get("expires_at") and datetime.fromisoformat(o["expires_at"]) < datetime.now(timezone.utc):
            return "order expired"
        if o["mode"] == "MT5_DEMO" and Cfg.is_live:
            return "order is for a DEMO account but the connector is on the LIVE environment"
        if o["mode"] == "MT5_LIVE" and (not Cfg.is_live or not Cfg.allow_real):
            return "live order refused (demo environment or real-money orders not allowed in Brokers settings)"
        if o["volume"] <= 0 or o["volume"] > Cfg.max_volume:
            return f"volume {o['volume']} outside connector limit {Cfg.max_volume} lots"
        if o["symbol"] not in self.markets:
            return f"market {o['symbol']} not available"
        if o["action"] == "OPEN" and not o.get("stop_loss"):
            return "missing stop-loss"
        if o["action"] == "OPEN" and any(v.get("client_id") == o["client_id"] for v in self.state["ours"].values()):
            return "duplicate: already executed"
        return None

    def report(self, cid: str, **kw):
        try:
            app("POST", f"/api/bridge/orders/{cid}/result", kw)
        except Exception as e:
            log.error("Failed to report result for %s: %s", cid, e)

    def confirm(self, ref: str, timeout: float = 15) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                c = self.cap.call("GET", f"/confirms/{ref}")
                if c.get("dealStatus") in ("ACCEPTED", "REJECTED"):
                    return c
            except CapitalError as e:
                if e.status != 404:
                    raise
            time.sleep(0.5)
        return {"dealStatus": "TIMEOUT"}

    def execute(self, o: dict):
        cid = o["client_id"]
        err = self.validate(o)
        if err:
            log.warning("Refusing %s: %s", cid, err)
            return self.report(cid, status="REJECTED", error=f"connector: {err}")
        m = self.load_market(o["symbol"])
        if not m.tradeable:
            return self.report(cid, status="REJECTED", error="connector: market not tradeable now")
        spread_pts = (m.ask - m.bid) / m.point

        if o["action"] == "CLOSE":
            ref = self.cap.call("DELETE", f"/positions/{o['position_ticket']}").get("dealReference")
            c = self.confirm(ref) if ref else {"dealStatus": "REJECTED"}
            if c.get("dealStatus") != "ACCEPTED":
                return self.report(cid, status="REJECTED", error=f"connector: close {c.get('dealStatus')} {c.get('reason', '')}")
            px = float(c.get("level") or (m.bid if o["side"] == "SELL" else m.ask))
            info = self.state["ours"].get(str(o["position_ticket"]))
            if info:
                info["closed_price"] = px
                self._save_state()
            return self.report(cid, status="FILLED", ticket=c.get("dealId"), price=px, volume=o["volume"], sl_verified=True)

        problem = m.size_unit_problem()
        if problem:
            log.error(problem)
            return self.report(cid, status="REJECTED", error=f"connector: {problem}")
        if spread_pts > Cfg.max_spread:
            return self.report(cid, status="REJECTED", error=f"connector: spread {spread_pts:.0f} pts > {Cfg.max_spread}")
        size = m.size(o["volume"])
        if size < m.min_size:
            return self.report(cid, status="REJECTED", error=f"connector: size {size} below broker minimum {m.min_size}")
        buy = o["side"] == "BUY"
        ref_px = m.ask if buy else m.bid
        sl = round(float(o["stop_loss"]), m.digits)
        tp = round(float(o["take_profit"]), m.digits) if o.get("take_profit") else None
        if (buy and sl >= ref_px) or (not buy and sl <= ref_px):
            return self.report(cid, status="REJECTED", error="connector: stop-loss on wrong side of market")
        body = {"epic": m.epic, "direction": "BUY" if buy else "SELL", "size": size, "guaranteedStop": False, "stopLevel": sl}
        if tp:
            body["profitLevel"] = tp
        ref = self.cap.call("POST", "/positions", body).get("dealReference")
        c = self.confirm(ref) if ref else {"dealStatus": "REJECTED", "reason": "no dealReference"}
        if c.get("dealStatus") != "ACCEPTED":
            return self.report(cid, status="REJECTED", error=f"connector: {c.get('dealStatus')} {c.get('reason', '')}".strip())
        deal_id = next((a["dealId"] for a in c.get("affectedDeals", []) if str(a.get("status", "")).upper() in ("OPENED", "OPEN")),
                       c.get("dealId"))
        fill = float(c.get("level") or ref_px)
        self.state["ours"][deal_id] = {"epic": m.epic, "client_id": cid, "side": o["side"], "open": fill,
                                       "units": size * m.units_per_size, "sl": sl}
        self._save_state()
        log.info("%s filled %s %s (%s size) @ %s deal %s", cid, o["side"], o["volume"], size, fill, deal_id)

        # Verify the stop (and the real position size) at the broker.
        stop_seen, stop_value, p = None, None, None
        for _ in range(6):
            self.load_positions()
            p = next((x["position"] for x in self.positions if x.get("position", {}).get("dealId") == deal_id), None)
            if p is not None:
                if "stopLevel" in p:
                    stop_seen, stop_value = True, p.get("stopLevel")
                break
            time.sleep(0.5)
        if p is not None and p.get("contractSize") and p.get("size"):
            actual_units = float(p["contractSize"]) * float(p["size"])
            expected = size * m.units_per_size
            if abs(actual_units - expected) > 0.01 * expected:
                log.error("SIZE MISMATCH on %s: expected %s units, broker shows %s; closing", deal_id, expected, actual_units)
                try:
                    self.cap.call("DELETE", f"/positions/{deal_id}")
                except Exception as e:
                    log.error("Emergency close failed for %s: %s", deal_id, e)
                return self.report(cid, status="FILLED", ticket=deal_id, position_ticket=deal_id, price=fill,
                                   volume=m.lots(size), sl_verified=False,
                                   error=f"size mismatch (expected {expected} units, broker {actual_units}); position closed")
        if stop_seen is None and "stopLevel" in c:
            stop_seen, stop_value = True, c.get("stopLevel")
        tol = (int(o.get("max_slippage_points") or 10) + 2) * m.point
        sl_ok = bool(stop_seen and stop_value and abs(float(stop_value) - sl) <= tol)
        if not sl_ok:
            # Fail closed: a position whose stop-loss cannot be confirmed at the broker is closed.
            log.error("Stop-loss not confirmed for %s (broker shows %s, wanted %s); closing", deal_id, stop_value, sl)
            try:
                self.cap.call("DELETE", f"/positions/{deal_id}")
            except Exception as e:
                log.error("Emergency close failed for %s: %s", deal_id, e)
        self.report(cid, status="FILLED", ticket=deal_id, position_ticket=deal_id, price=fill, volume=m.lots(size),
                    sl_verified=sl_ok, sl=float(stop_value) if stop_value else sl, tp=tp,
                    error="" if sl_ok else "stop-loss could not be confirmed from broker data")

    # ---------------------------------------------------------- loop
    def cycle(self):
        acct = self.account_payload()
        hb = app("POST", "/api/bridge/heartbeat", {"account": acct, "version": f"capital-{VERSION}",
                                                  "terminal": {"connected": True, "trade_allowed": True,
                                                               "company": "Capital.com", "platform": "Capital.com REST API"}})
        self.symbols = hb.get("symbols", self.symbols)
        self.timeframes = [t for t in hb.get("timeframes", self.timeframes) if t in RESOLUTION]
        self.refresh_quotes()
        if time.time() - self.last_specs > 3600:
            self.load_leverage()
            app("POST", "/api/bridge/symbols", {"symbols": self.symbols_payload()})
            self.last_specs = time.time()
        app("POST", "/api/bridge/quotes", {"quotes": self.quotes_payload()})
        self.push_candles()
        for o in app("GET", "/api/bridge/orders/pending").get("orders", []):
            try:
                self.execute(o)
            except Exception as e:
                log.exception("Execution error for %s", o.get("client_id"))
                self.report(o["client_id"], status="REJECTED", error=f"connector exception: {e}")
        self.load_positions()
        app("POST", "/api/bridge/positions", {"positions": self.positions_payload(), "closed": self.closed_payload(),
                                              "external": self.external_payload()})

    def status(self, st: str, message: str = ""):
        try:
            app("POST", "/api/bridge/status", {"state": st, "message": message})
        except Exception as e:
            log.debug("status report failed: %s", e)

    def sync_config(self) -> None:
        """Pull settings from the dashboard; seed them from .env on first run."""
        d = app("GET", "/api/bridge/config?connector=capital")
        if not d.get("api_key") and CAP_API_KEY and CAP_IDENTIFIER and CAP_PASSWORD:
            app("POST", "/api/bridge/seed", {"fields": {"identifier": CAP_IDENTIFIER, "api_key": CAP_API_KEY,
                                                         "api_password": CAP_PASSWORD, "environment": CAP_ENV,
                                                         "account_id": CAP_ACCOUNT_ID}})
            log.info("Copied .env credentials into the dashboard settings (manage them on the Brokers page from now on)")
            d = app("GET", "/api/bridge/config?connector=capital")
        first = not hasattr(self, "_seen_version")
        if Cfg.apply(d) or first:  # reconnect only when *this* broker's credentials/environment change
            if not first:
                log.info("Capital.com settings changed in the dashboard; reconnecting")
            self._seen_version = Cfg.version
            self.cap = Capital()
            self.markets.clear()
            self.last_bar.clear()
            self.last_specs = 0.0

    def run(self):
        if len(SECRET) < 32:
            raise SystemExit("BRIDGE_SECRET must match the server .env (32+ chars)")
        backoff, idle_logged = 5, False
        while True:
            started = time.time()
            try:
                self.sync_config()
                if not Cfg.active:
                    if not idle_logged:
                        log.info("Capital.com is not the active broker (choose it on the Brokers page); waiting")
                        idle_logged = True
                    self.status("idle", "Not the active broker")
                    time.sleep(15)
                    continue
                idle_logged = False
                if not (Cfg.api_key and Cfg.identifier and Cfg.api_password):
                    self.status("error", "Enter the Capital.com email, API key and API password on the Brokers page")
                    time.sleep(15)
                    continue
                if Cfg.is_live and not Cfg.allow_real:
                    log.warning("LIVE environment and real-money orders not allowed: orders will be refused; data still flows.")
                self.cycle()
                backoff = 5
            except CapitalError as e:
                msg = f"Capital.com error: {e}"
                if e.status in (401, 403) or "invalid.details" in str(e) or "api.key" in str(e).lower():
                    msg += " - check the email, API key and API password on the Brokers page and that the key is active"
                log.error(msg)
                self.status("error", msg)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            except Exception as e:
                log.error("Cycle error: %s", e)
                self.status("error", f"{e}"[:300])
            time.sleep(max(0.5, LOOP_SECONDS - (time.time() - started)))

if __name__ == "__main__":
    try:
        Connector(cap=Capital(base="http://unused")).run()
    except KeyboardInterrupt:
        pass
