"""Spot crypto exchange adapters: Binance, OKX, Bybit.

Scope (deliberately small and safe):
* SPOT only — the app buys coins with USDT and sells them back. No futures,
  no margin, no borrowing, no withdrawals (API keys must not allow withdrawals).
* Every position gets a protective stop-loss AT THE EXCHANGE right after the buy:
    Binance — OCO sell (take-profit LIMIT_MAKER + stop-loss) or a stop-loss order
    OKX     — algo order (oco / conditional) with market execution
    Bybit   — spot conditional StopOrder (market); the take-profit is watched by the app
  If the protection cannot be placed, the coins are sold again immediately (fail closed).
* Test environments: Binance Spot Testnet, OKX demo trading, Bybit testnet.

Each adapter talks plain REST through `self.http(method, url, headers, body)` so
tests can replace the transport. Numbers are sent as strings, rounded to the
exchange's tick/step size.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

TF = {"S1": ("1s", "1s", None), "M1": ("1m", "1m", "1"), "M5": ("5m", "5m", "5"), "M30": ("30m", "30m", "30"),
      "M15": ("15m", "15m", "15"), "H1": ("1h", "1H", "60"), "H4": ("4h", "4H", "240"), "D1": ("1d", "1Dutc", "D")}


class ExchangeError(Exception):
    pass


@dataclass
class SymbolInfo:
    symbol: str
    base: str
    quote: str
    tick: float
    step: float
    min_qty: float
    min_notional: float = 0.0
    trading: bool = True
    stop_market: bool = True  # exchange supports a market stop-loss for this symbol


@dataclass
class Fill:
    order_id: str
    qty: float           # base coin actually received / sold (after fees in base, if any)
    avg_price: float
    fee_quote: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class Protection:
    ids: list[str]
    kind: str            # "oco" | "stop" | "stop+app_tp"
    exchange_tp: bool    # True when the take-profit also lives at the exchange


def fmt_dec(x: float, step: float) -> str:
    """Round DOWN to the step and format without exponent."""
    d = Decimal(str(step)).normalize()
    q = (Decimal(str(x)) / d).to_integral_value(rounding=ROUND_DOWN) * d
    s = format(q.quantize(d) if d.as_tuple().exponent < 0 else q, "f")
    return s


def fmt_px(x: float, tick: float) -> str:
    d = Decimal(str(tick)).normalize()
    q = (Decimal(str(x)) / d).to_integral_value() * d
    return format(q.quantize(d) if d.as_tuple().exponent < 0 else q, "f")


def default_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 15) -> tuple[int, dict | list]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"msg": str(e)}


class Exchange:
    name = ""
    has_testnet = True

    def __init__(self, api_key: str = "", secret: str = "", passphrase: str = "", testnet: bool = True, http=None):
        self.key, self.secret, self.passphrase, self.testnet = api_key, secret, passphrase, testnet
        self.http = http or default_http
        self._info: dict[str, SymbolInfo] = {}

    # --- helpers
    @staticmethod
    def split(symbol: str) -> tuple[str, str]:
        for q in ("USDT", "USDC", "FDUSD", "BTC", "EUR"):
            if symbol.endswith(q) and len(symbol) > len(q):
                return symbol[: -len(q)], q
        raise ExchangeError(f"unknown quote currency in {symbol}")

    def info(self, symbol: str) -> SymbolInfo:
        if symbol not in self._info:
            self._info[symbol] = self.load_info(symbol)
        return self._info[symbol]

    # --- interface (implemented per exchange)
    def load_info(self, symbol: str) -> SymbolInfo: ...
    def book(self, symbol: str) -> tuple[float, float]: ...
    def change_24h(self, symbol: str) -> float | None: return None
    def klines(self, symbol: str, tf: str, limit: int = 300) -> list[dict]: ...

    def history(self, symbol: str, tf: str, days: int, max_calls: int = 60) -> list[dict]:
        """Older candles by paging backwards (public data). Default: only the latest batch."""
        return self.klines(symbol, tf, 1000)
    def balances(self) -> dict[str, float]: ...
    def market_buy(self, symbol: str, qty: float, client_id: str) -> Fill: ...
    def market_sell(self, symbol: str, qty: float, client_id: str) -> Fill: ...
    def protect(self, symbol: str, qty: float, sl: float, tp: float | None, client_id: str) -> Protection: ...
    def cancel_protection(self, symbol: str, prot_ids: list[str], kind: str) -> None: ...
    def protection_state(self, symbol: str, prot_ids: list[str], kind: str) -> dict: ...
    """-> {"active": bool, "filled": bool, "exit_price": float|None, "reason": "stop_loss"|"take_profit"|...}"""

    def check_keys(self) -> dict:
        """Verify the keys work and report what they can do (never requires withdrawal rights)."""
        bal = self.balances()
        return {"ok": True, "assets": {k: v for k, v in bal.items() if v}}


# ====================================================================== Binance
class Binance(Exchange):
    name = "binance"

    @property
    def base(self) -> str:
        return "https://testnet.binance.vision" if self.testnet else "https://api.binance.com"

    def _pub(self, path: str, params: dict) -> dict | list:
        code, data = self.http("GET", f"{self.base}{path}?{urllib.parse.urlencode(params)}", {}, None)
        if code != 200:
            raise ExchangeError(f"binance {path}: {data}")
        return data

    def sign(self, query: str) -> str:
        return hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _priv(self, method: str, path: str, params: dict) -> dict | list:
        if not self.key or not self.secret:
            raise ExchangeError("binance API key/secret not set")
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        q = urllib.parse.urlencode(params)
        q += "&signature=" + self.sign(q)
        headers = {"X-MBX-APIKEY": self.key}
        if method == "GET" or method == "DELETE":
            code, data = self.http(method, f"{self.base}{path}?{q}", headers, None)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            code, data = self.http(method, f"{self.base}{path}", headers, q.encode())
        if code != 200:
            raise ExchangeError(f"binance {path}: {data.get('msg') if isinstance(data, dict) else data}")
        return data

    def load_info(self, symbol):
        d = self._pub("/api/v3/exchangeInfo", {"symbol": symbol})["symbols"][0]
        f = {x["filterType"]: x for x in d["filters"]}
        mn = f.get("NOTIONAL", f.get("MIN_NOTIONAL", {}))
        return SymbolInfo(symbol, d["baseAsset"], d["quoteAsset"], float(f["PRICE_FILTER"]["tickSize"]),
                          float(f["LOT_SIZE"]["stepSize"]), float(f["LOT_SIZE"]["minQty"]),
                          float(mn.get("minNotional", 0) or 0), d.get("status") == "TRADING",
                          "STOP_LOSS" in d.get("orderTypes", []))

    def book(self, symbol):
        d = self._pub("/api/v3/ticker/bookTicker", {"symbol": symbol})
        return float(d["bidPrice"]), float(d["askPrice"])

    def change_24h(self, symbol):
        d = self._pub("/api/v3/ticker/24hr", {"symbol": symbol})
        return float(d["priceChangePercent"])

    def klines(self, symbol, tf, limit=300):
        rows = self._pub("/api/v3/klines", {"symbol": symbol, "interval": TF[tf][0], "limit": min(limit, 1000)})
        return [{"time": datetime.fromtimestamp(r[0] / 1000, timezone.utc), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "tick_volume": float(r[5])} for r in rows]

    def history(self, symbol, tf, days, max_calls=60):
        step = {"M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}[tf] * 1000
        end = int(time.time() * 1000)
        start = end - days * 86400 * 1000
        out, cur = [], start
        for _ in range(max_calls):
            rows = self._pub("/api/v3/klines", {"symbol": symbol, "interval": TF[tf][0], "startTime": cur, "limit": 1000})
            if not rows:
                break
            out += [{"time": datetime.fromtimestamp(r[0] / 1000, timezone.utc), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "tick_volume": float(r[5])} for r in rows]
            cur = rows[-1][0] + step
            if cur >= end or len(rows) < 1000:
                break
        return out

    def balances(self):
        d = self._priv("GET", "/api/v3/account", {})
        return {b["asset"]: float(b["free"]) + float(b["locked"]) for b in d["balances"] if float(b["free"]) + float(b["locked"]) > 0}

    def _fill(self, d: dict, base: str) -> Fill:
        qty = float(d.get("executedQty", 0))
        quote = float(d.get("cummulativeQuoteQty", 0))
        fee_base = fee_quote = 0.0
        for f in d.get("fills", []):
            if f.get("commissionAsset") == base:
                fee_base += float(f["commission"])
            elif f.get("commissionAsset") in ("USDT", "USDC", "FDUSD"):
                fee_quote += float(f["commission"])
            # fees paid in BNB are not converted (shown in the raw fill only)
        if qty <= 0:
            raise ExchangeError(f"binance order not filled: {d.get('status')}")
        return Fill(str(d["orderId"]), qty - fee_base, quote / qty, fee_quote + fee_base * (quote / qty), d)

    def market_buy(self, symbol, qty, client_id):
        i = self.info(symbol)
        d = self._priv("POST", "/api/v3/order", {"symbol": symbol, "side": "BUY", "type": "MARKET",
                                                  "quantity": fmt_dec(qty, i.step), "newClientOrderId": client_id[:36],
                                                  "newOrderRespType": "FULL"})
        return self._fill(d, i.base)

    def market_sell(self, symbol, qty, client_id):
        i = self.info(symbol)
        d = self._priv("POST", "/api/v3/order", {"symbol": symbol, "side": "SELL", "type": "MARKET",
                                                  "quantity": fmt_dec(qty, i.step), "newClientOrderId": client_id[:36],
                                                  "newOrderRespType": "FULL"})
        return self._fill(d, i.base)

    def protect(self, symbol, qty, sl, tp, client_id):
        i = self.info(symbol)
        q = fmt_dec(qty, i.step)
        stop_limit = fmt_px(sl * 0.995, i.tick)  # limit 0.5% below the trigger, for STOP_LOSS_LIMIT
        if tp:
            p = {"symbol": symbol, "side": "SELL", "quantity": q, "aboveType": "LIMIT_MAKER", "abovePrice": fmt_px(tp, i.tick),
                 "belowStopPrice": fmt_px(sl, i.tick), "listClientOrderId": client_id[:32]}
            if i.stop_market:
                p["belowType"] = "STOP_LOSS"
            else:
                p.update(belowType="STOP_LOSS_LIMIT", belowPrice=stop_limit, belowTimeInForce="GTC")
            d = self._priv("POST", "/api/v3/orderList/oco", p)
            return Protection([str(d["orderListId"])], "oco", True)
        p = {"symbol": symbol, "side": "SELL", "quantity": q, "stopPrice": fmt_px(sl, i.tick), "newClientOrderId": client_id[:36]}
        if i.stop_market:
            p["type"] = "STOP_LOSS"
        else:
            p.update(type="STOP_LOSS_LIMIT", price=stop_limit, timeInForce="GTC")
        d = self._priv("POST", "/api/v3/order", p)
        return Protection([str(d["orderId"])], "stop", False)

    def cancel_protection(self, symbol, prot_ids, kind):
        try:
            if kind == "oco":
                self._priv("DELETE", "/api/v3/orderList", {"symbol": symbol, "orderListId": prot_ids[0]})
            else:
                self._priv("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": prot_ids[0]})
        except ExchangeError as e:
            if "Unknown order" not in str(e):
                raise

    def protection_state(self, symbol, prot_ids, kind):
        if kind == "oco":
            ol = self._priv("GET", "/api/v3/orderList", {"orderListId": prot_ids[0]})
            if ol.get("listOrderStatus") != "ALL_DONE":
                return {"active": True, "filled": False}
            for o in ol.get("orders", []):
                od = self._priv("GET", "/api/v3/order", {"symbol": symbol, "orderId": o["orderId"]})
                if od["status"] == "FILLED":
                    qty = float(od["executedQty"])
                    return {"active": False, "filled": True, "exit_price": float(od["cummulativeQuoteQty"]) / qty if qty else None,
                            "reason": "take_profit" if od["type"] == "LIMIT_MAKER" else "stop_loss"}
            return {"active": False, "filled": False}
        od = self._priv("GET", "/api/v3/order", {"symbol": symbol, "orderId": prot_ids[0]})
        if od["status"] in ("NEW", "PARTIALLY_FILLED"):
            return {"active": True, "filled": False}
        if od["status"] == "FILLED":
            qty = float(od["executedQty"])
            return {"active": False, "filled": True, "exit_price": float(od["cummulativeQuoteQty"]) / qty if qty else None, "reason": "stop_loss"}
        return {"active": False, "filled": False}

    def check_keys(self):
        d = self._priv("GET", "/api/v3/account", {})
        perms = d.get("permissions", []) or d.get("permissionSets", [])
        return {"ok": True, "can_trade": bool(d.get("canTrade")), "can_withdraw": bool(d.get("canWithdraw")), "permissions": perms,
                "assets": {b["asset"]: float(b["free"]) + float(b["locked"]) for b in d["balances"] if float(b["free"]) + float(b["locked"]) > 0}}


# ========================================================================== OKX
class Okx(Exchange):
    name = "okx"
    base = "https://www.okx.com"

    @staticmethod
    def inst(symbol: str) -> str:
        b, q = Exchange.split(symbol)
        return f"{b}-{q}"

    def sign(self, ts: str, method: str, path: str, body: str) -> str:
        return base64.b64encode(hmac.new(self.secret.encode(), f"{ts}{method}{path}{body}".encode(), hashlib.sha256).digest()).decode()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | list | None = None, private: bool = False):
        if params:
            path = f"{path}?{urllib.parse.urlencode(params)}"
        raw = json.dumps(body) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if self.testnet:
            headers["x-simulated-trading"] = "1"
        if private:
            if not (self.key and self.secret and self.passphrase):
                raise ExchangeError("OKX API key, secret and passphrase are required")
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
            headers.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": self.sign(ts, method, path, raw),
                            "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase})
        code, d = self.http(method, f"{self.base}{path}", headers, raw.encode() if raw else None)
        if code != 200 or str(d.get("code")) != "0":
            detail = d.get("msg") or ""
            if d.get("data") and isinstance(d["data"], list) and isinstance(d["data"][0], dict):
                detail += " " + str(d["data"][0].get("sMsg", ""))
            raise ExchangeError(f"okx {path.split('?')[0]}: {detail.strip() or d}")
        return d["data"]

    def load_info(self, symbol):
        d = self._req("GET", "/api/v5/public/instruments", {"instType": "SPOT", "instId": self.inst(symbol)})[0]
        return SymbolInfo(symbol, d["baseCcy"], d["quoteCcy"], float(d["tickSz"]), float(d["lotSz"]), float(d["minSz"]), 0.0,
                          d.get("state") == "live", True)

    def book(self, symbol):
        d = self._req("GET", "/api/v5/market/ticker", {"instId": self.inst(symbol)})[0]
        return float(d["bidPx"]), float(d["askPx"])

    def change_24h(self, symbol):
        d = self._req("GET", "/api/v5/market/ticker", {"instId": self.inst(symbol)})[0]
        o = float(d.get("open24h") or 0)
        return (float(d["last"]) - o) / o * 100 if o else None

    def klines(self, symbol, tf, limit=300):
        rows = self._req("GET", "/api/v5/market/candles", {"instId": self.inst(symbol), "bar": TF[tf][1], "limit": min(limit, 300)})
        out = [{"time": datetime.fromtimestamp(int(r[0]) / 1000, timezone.utc), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "tick_volume": float(r[5])} for r in rows if len(r) < 9 or r[8] == "1"]
        return sorted(out, key=lambda x: x["time"])

    def history(self, symbol, tf, days, max_calls=60):
        stop = (time.time() - days * 86400) * 1000
        out, after = [], None
        for _ in range(max_calls):
            p = {"instId": self.inst(symbol), "bar": TF[tf][1], "limit": 100}
            if after:
                p["after"] = after
            rows = self._req("GET", "/api/v5/market/history-candles", p)
            if not rows:
                break
            out += [{"time": datetime.fromtimestamp(int(r[0]) / 1000, timezone.utc), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "tick_volume": float(r[5])} for r in rows if len(r) < 9 or r[8] == "1"]
            after = rows[-1][0]
            if int(after) <= stop:
                break
        return sorted(out, key=lambda x: x["time"])

    def balances(self):
        d = self._req("GET", "/api/v5/account/balance", private=True)
        return {x["ccy"]: float(x.get("cashBal") or x.get("eq") or 0) for x in (d[0].get("details", []) if d else [])
                if float(x.get("cashBal") or x.get("eq") or 0) > 0}

    def _order_fill(self, symbol, ord_id) -> Fill:
        for _ in range(10):
            d = self._req("GET", "/api/v5/trade/order", {"instId": self.inst(symbol), "ordId": ord_id}, private=True)[0]
            if d["state"] in ("filled", "partially_filled", "canceled") and float(d.get("accFillSz") or 0) > 0:
                i = self.info(symbol)
                qty, px = float(d["accFillSz"]), float(d["avgPx"])
                fee = -float(d.get("fee") or 0)  # OKX reports fees as negative numbers
                fee_q = fee * px if d.get("feeCcy") == i.base else fee
                if d.get("side") == "buy" and d.get("feeCcy") == i.base:
                    qty -= fee
                return Fill(ord_id, qty, px, fee_q, d)
            time.sleep(0.3)
        raise ExchangeError(f"okx order {ord_id} not filled")

    def _market(self, symbol, side, qty, client_id):
        i = self.info(symbol)
        d = self._req("POST", "/api/v5/trade/order", body={"instId": self.inst(symbol), "tdMode": "cash", "side": side, "ordType": "market",
                                                          "sz": fmt_dec(qty, i.step), "tgtCcy": "base_ccy",
                                                          "clOrdId": "".join(ch for ch in client_id if ch.isalnum())[:32]}, private=True)
        return self._order_fill(symbol, d[0]["ordId"])

    def market_buy(self, symbol, qty, client_id):
        return self._market(symbol, "buy", qty, client_id)

    def market_sell(self, symbol, qty, client_id):
        return self._market(symbol, "sell", qty, client_id)

    def protect(self, symbol, qty, sl, tp, client_id):
        i = self.info(symbol)
        body = {"instId": self.inst(symbol), "tdMode": "cash", "side": "sell", "sz": fmt_dec(qty, i.step),
                "slTriggerPx": fmt_px(sl, i.tick), "slOrdPx": "-1", "ordType": "oco" if tp else "conditional"}
        if tp:
            body.update(tpTriggerPx=fmt_px(tp, i.tick), tpOrdPx="-1")
        d = self._req("POST", "/api/v5/trade/order-algo", body=body, private=True)
        return Protection([d[0]["algoId"]], "oco" if tp else "stop", bool(tp))

    def cancel_protection(self, symbol, prot_ids, kind):
        try:
            self._req("POST", "/api/v5/trade/cancel-algos", body=[{"algoId": prot_ids[0], "instId": self.inst(symbol)}], private=True)
        except ExchangeError as e:
            if "canceled" not in str(e).lower() and "not exist" not in str(e).lower():
                raise

    def protection_state(self, symbol, prot_ids, kind):
        d = self._req("GET", "/api/v5/trade/order-algo", {"algoId": prot_ids[0]}, private=True)[0]
        st = d.get("state")
        if st == "live":
            return {"active": True, "filled": False}
        if st == "effective" and d.get("ordId"):
            f = self._order_fill(symbol, d["ordId"])
            reason = "stop_loss"
            if d.get("actualSide") == "tp" or (d.get("tpTriggerPx") and f.avg_price >= float(d["tpTriggerPx"]) * 0.995):
                reason = "take_profit"
            return {"active": False, "filled": True, "exit_price": f.avg_price, "reason": reason}
        return {"active": False, "filled": False}


# ======================================================================== Bybit
class Bybit(Exchange):
    name = "bybit"

    @property
    def base(self) -> str:
        return "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"

    def sign(self, ts: str, payload: str) -> str:
        return hmac.new(self.secret.encode(), f"{ts}{self.key}5000{payload}".encode(), hashlib.sha256).hexdigest()

    def _req(self, method, path, params=None, body=None, private=False):
        q = urllib.parse.urlencode(params or {})
        raw = json.dumps(body) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if private:
            if not (self.key and self.secret):
                raise ExchangeError("Bybit API key/secret not set")
            ts = str(int(time.time() * 1000))
            headers.update({"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000",
                            "X-BAPI-SIGN": self.sign(ts, q if method == "GET" else raw)})
        code, d = self.http(method, f"{self.base}{path}{'?' + q if q else ''}", headers, raw.encode() if raw else None)
        if code != 200 or d.get("retCode") != 0:
            raise ExchangeError(f"bybit {path}: {d.get('retMsg') or d}")
        return d["result"]

    def load_info(self, symbol):
        d = self._req("GET", "/v5/market/instruments-info", {"category": "spot", "symbol": symbol})["list"][0]
        lot = d["lotSizeFilter"]
        return SymbolInfo(symbol, d["baseCoin"], d["quoteCoin"], float(d["priceFilter"]["tickSize"]), float(lot["basePrecision"]),
                          float(lot["minOrderQty"]), float(lot.get("minOrderAmt") or 0), d.get("status") == "Trading", True)

    def book(self, symbol):
        d = self._req("GET", "/v5/market/tickers", {"category": "spot", "symbol": symbol})["list"][0]
        return float(d["bid1Price"]), float(d["ask1Price"])

    def change_24h(self, symbol):
        d = self._req("GET", "/v5/market/tickers", {"category": "spot", "symbol": symbol})["list"][0]
        return float(d["price24hPcnt"]) * 100

    def klines(self, symbol, tf, limit=300):
        rows = self._req("GET", "/v5/market/kline", {"category": "spot", "symbol": symbol, "interval": TF[tf][2], "limit": min(limit, 1000)})["list"]
        out = [{"time": datetime.fromtimestamp(int(r[0]) / 1000, timezone.utc), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "tick_volume": float(r[5])} for r in rows]
        return sorted(out, key=lambda x: x["time"])

    def history(self, symbol, tf, days, max_calls=60):
        stop = (time.time() - days * 86400) * 1000
        out, end = [], None
        for _ in range(max_calls):
            p = {"category": "spot", "symbol": symbol, "interval": TF[tf][2], "limit": 1000}
            if end:
                p["end"] = end
            rows = self._req("GET", "/v5/market/kline", p)["list"]
            if not rows:
                break
            out += [{"time": datetime.fromtimestamp(int(r[0]) / 1000, timezone.utc), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "tick_volume": float(r[5])} for r in rows]
            end = int(rows[-1][0]) - 1
            if end <= stop or len(rows) < 1000:
                break
        return sorted({x["time"]: x for x in out}.values(), key=lambda x: x["time"])

    def balances(self):
        d = self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, private=True)["list"]
        return {c["coin"]: float(c.get("walletBalance") or 0) for c in (d[0]["coin"] if d else []) if float(c.get("walletBalance") or 0) > 0}

    def _fill(self, symbol, order_id, filt="Order") -> Fill:
        for _ in range(10):
            lst = self._req("GET", "/v5/order/history", {"category": "spot", "orderId": order_id}, private=True)["list"]
            if lst and lst[0]["orderStatus"] in ("Filled", "PartiallyFilledCanceled") and float(lst[0].get("cumExecQty") or 0) > 0:
                o = lst[0]
                i = self.info(symbol)
                qty = float(o["cumExecQty"])
                px = float(o.get("avgPrice") or 0) or float(o["cumExecValue"]) / qty
                fee = float(o.get("cumExecFee") or 0)
                fee_q = fee * px if o["side"] == "Buy" else fee
                if o["side"] == "Buy":
                    qty -= fee  # Bybit spot charges buy fees in the base coin
                return Fill(order_id, qty, px, fee_q, o)
            time.sleep(0.3)
        raise ExchangeError(f"bybit order {order_id} not filled")

    def _market(self, symbol, side, qty, client_id):
        i = self.info(symbol)
        d = self._req("POST", "/v5/order/create", body={"category": "spot", "symbol": symbol, "side": side, "orderType": "Market",
                                                       "qty": fmt_dec(qty, i.step), "marketUnit": "baseCoin",
                                                       "orderLinkId": client_id[:36]}, private=True)
        return self._fill(symbol, d["orderId"])

    def market_buy(self, symbol, qty, client_id):
        return self._market(symbol, "Buy", qty, client_id)

    def market_sell(self, symbol, qty, client_id):
        return self._market(symbol, "Sell", qty, client_id)

    def protect(self, symbol, qty, sl, tp, client_id):
        i = self.info(symbol)
        d = self._req("POST", "/v5/order/create", body={"category": "spot", "symbol": symbol, "side": "Sell", "orderType": "Market",
                                                       "qty": fmt_dec(qty, i.step), "marketUnit": "baseCoin",
                                                       "triggerPrice": fmt_px(sl, i.tick), "orderFilter": "StopOrder",
                                                       "orderLinkId": (client_id + "-sl")[:36]}, private=True)
        return Protection([d["orderId"]], "stop+app_tp" if tp else "stop", False)

    def cancel_protection(self, symbol, prot_ids, kind):
        try:
            self._req("POST", "/v5/order/cancel", body={"category": "spot", "symbol": symbol, "orderId": prot_ids[0],
                                                       "orderFilter": "StopOrder"}, private=True)
        except ExchangeError as e:
            if "not exist" not in str(e).lower() and "too late" not in str(e).lower():
                raise

    def protection_state(self, symbol, prot_ids, kind):
        lst = self._req("GET", "/v5/order/realtime", {"category": "spot", "orderId": prot_ids[0], "orderFilter": "StopOrder"},
                        private=True)["list"]
        if lst and lst[0]["orderStatus"] in ("Untriggered", "New"):
            return {"active": True, "filled": False}
        hist = self._req("GET", "/v5/order/history", {"category": "spot", "orderId": prot_ids[0]}, private=True)["list"]
        o = hist[0] if hist else {}
        if o.get("orderStatus") in ("Filled", "Triggered") and float(o.get("cumExecQty") or 0) > 0:
            qty = float(o["cumExecQty"])
            return {"active": False, "filled": True, "exit_price": float(o.get("avgPrice") or 0) or float(o["cumExecValue"]) / qty,
                    "reason": "stop_loss"}
        if o.get("orderStatus") == "Triggered":
            return {"active": True, "filled": False}
        return {"active": False, "filled": False}


EXCHANGES = {"binance": Binance, "okx": Okx, "bybit": Bybit}
LABELS = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
TEST_LABEL = {"binance": "Spot Testnet", "okx": "Demo trading", "bybit": "Testnet"}


def make(name: str, creds: dict | None = None, testnet: bool = True, http=None) -> Exchange:
    if name not in EXCHANGES:
        raise ExchangeError(f"unknown exchange {name}")
    c = creds or {}
    return EXCHANGES[name](c.get("api_key", ""), c.get("secret", ""), c.get("passphrase", ""), testnet, http)
