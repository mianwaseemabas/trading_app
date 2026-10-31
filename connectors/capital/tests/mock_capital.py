"""Mock Capital.com REST API (documented response shapes) for connector tests."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MARKETS = {"EURUSD": (1.08000, 5), "GBPUSD": (1.27000, 5), "USDJPY": (150.000, 3)}
STATE = {"max_span": 10**9, "balance": 500.0, "positions": {}, "confirms": {}, "activities": [], "no_stop": False, "lot_size": 1, "contract": 1}
LOCK = threading.Lock()


def utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        return self.headers.get("CST") == "cst-token" and self.headers.get("X-SECURITY-TOKEN") == "xst-token"

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/v1/session":
            b = self._body()
            if self.headers.get("X-CAP-API-KEY") != "key123" or b.get("password") != "apipass" or b.get("identifier") != "me@example.com":
                return self._send(401, {"errorCode": "error.invalid.details"})
            return self._send(200, {"accountType": "CFD", "currencyIsoCode": "USD", "currentAccountId": "ACC1",
                                    "accounts": [{"accountId": "ACC1", "accountName": "Demo USD", "preferred": True}]},
                              {"CST": "cst-token", "X-SECURITY-TOKEN": "xst-token"})
        if not self._auth():
            return self._send(401, {"errorCode": "error.invalid.session.token"})
        if u.path == "/api/v1/positions":
            b = self._body()
            px, digits = MARKETS[b["epic"]]
            assert "stopLevel" in b, "stopLevel must be sent"
            buy = b["direction"] == "BUY"
            level = round(px + (0.0001 if b["epic"] != "USDJPY" else 0.012), digits) if buy else px
            ref, deal_id = "o_" + uuid.uuid4().hex[:8], uuid.uuid4().hex[:12]
            with LOCK:
                STATE["positions"][deal_id] = {"position": {
                    "contractSize": STATE["contract"], "createdDateUTC": utc(time.time()), "dealId": deal_id, "dealReference": ref,
                    "size": b["size"], "leverage": 30, "upl": -0.5, "direction": b["direction"], "level": level, "currency": "USD",
                    "guaranteedStop": False, **({} if STATE["no_stop"] else {"stopLevel": b["stopLevel"]}),
                    **({"profitLevel": b["profitLevel"]} if b.get("profitLevel") else {})},
                    "market": {"epic": b["epic"], "bid": px, "offer": round(px + 0.0001, digits), "marketStatus": "TRADEABLE"}}
                STATE["confirms"][ref] = {"date": utc(time.time()), "status": "OPEN", "dealStatus": "ACCEPTED", "epic": b["epic"],
                                          "dealReference": ref, "dealId": "conf_" + deal_id, "level": level, "size": b["size"],
                                          "direction": b["direction"], "affectedDeals": [{"dealId": deal_id, "status": "OPENED"}]}
            return self._send(200, {"dealReference": ref})
        return self._send(404, {"errorCode": "not.found"})

    def do_DELETE(self):
        u = urlparse(self.path)
        if not self._auth():
            return self._send(401, {"errorCode": "error.invalid.session.token"})
        m = re.match(r"/api/v1/positions/(.+)", u.path)
        if m:
            with LOCK:
                p = STATE["positions"].pop(m.group(1), None)
                if p is None:
                    return self._send(404, {"errorCode": "error.not-found.dealId"})
                ref = "p_" + uuid.uuid4().hex[:8]
                close = round(p["position"]["level"] - 0.0010, 5)
                STATE["confirms"][ref] = {"dealStatus": "ACCEPTED", "status": "CLOSED", "dealId": m.group(1), "level": close,
                                          "affectedDeals": [{"dealId": m.group(1), "status": "FULLY_CLOSED"}]}
                STATE["activities"].append({"dealId": m.group(1), "source": "USER", "status": "ACCEPTED", "type": "POSITION",
                                            "details": {"level": close}})
            return self._send(200, {"dealReference": ref})
        return self._send(404, {})

    def do_PUT(self):
        u = urlparse(self.path)
        m = re.match(r"/api/v1/positions/(.+)", u.path)
        if m and self._auth():
            b = self._body()
            with LOCK:
                p = STATE["positions"].get(m.group(1))
                if p is None:
                    return self._send(404, {"errorCode": "error.not-found.dealId"})
                if "stopLevel" in b:
                    p["position"]["stopLevel"] = b["stopLevel"]
                if "profitLevel" in b:
                    p["position"]["profitLevel"] = b["profitLevel"]
                ref = "u_" + uuid.uuid4().hex[:8]
                STATE["confirms"][ref] = {"dealStatus": "ACCEPTED", "status": "AMENDED", "dealId": m.group(1),
                                          "affectedDeals": [{"dealId": m.group(1), "status": "AMENDED"}]}
            return self._send(200, {"dealReference": ref})
        return self._send(200, {"status": "SUCCESS"})

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._auth():
            return self._send(401, {"errorCode": "error.invalid.session.token"})
        if u.path == "/api/v1/ping":
            return self._send(200, {"status": "OK"})
        if u.path == "/api/v1/accounts":
            upl = sum(p["position"]["upl"] for p in STATE["positions"].values())
            return self._send(200, {"accounts": [{"accountId": "ACC1", "accountName": "Demo USD", "status": "ENABLED", "preferred": True,
                                                  "balance": {"balance": STATE["balance"], "deposit": 500, "profitLoss": upl, "available": STATE["balance"] + upl - 36 * len(STATE["positions"])},
                                                  "currency": "USD", "symbol": "$"}]})
        if u.path == "/api/v1/accounts/preferences":
            return self._send(200, {"hedgingMode": False, "leverages": {"CURRENCIES": {"available": [10, 20, 30], "current": 30}}})
        m = re.match(r"/api/v1/markets/(\w+)$", u.path)
        if m and m.group(1) in MARKETS:
            px, digits = MARKETS[m.group(1)]
            return self._send(200, {"instrument": {"epic": m.group(1), "name": m.group(1), "lotSize": STATE["lot_size"], "type": "CURRENCIES",
                                                   "currency": m.group(1)[3:], "streamingPricesAvailable": True},
                                    "dealingRules": {"minDealSize": {"unit": "POINTS", "value": 100}, "maxDealSize": {"unit": "POINTS", "value": 10000000},
                                                     "minSizeIncrement": {"unit": "POINTS", "value": 1}},
                                    "snapshot": {"marketStatus": "TRADEABLE", "bid": px, "offer": round(px + (0.0001 if digits == 5 else 0.012), digits),
                                                 "decimalPlacesFactor": digits, "scalingFactor": 1}})
        m = re.match(r"/api/v1/prices/(\w+)$", u.path)
        if m:
            px, digits = MARKETS[m.group(1)]
            step = {"MINUTE_15": 900, "HOUR": 3600}[q["resolution"]]
            to = datetime.fromisoformat(q["to"]).replace(tzinfo=timezone.utc).timestamp() if "to" in q else time.time()
            frm = datetime.fromisoformat(q["from"]).replace(tzinfo=timezone.utc).timestamp() if "from" in q else to - int(q.get("max", 10)) * step
            if "from" in q and "to" in q and (to - frm) / step > STATE["max_span"]:
                return self._send(400, {"errorCode": "error.invalid.max.daterange"})
            frm = max(frm, time.time() - 40 * 86400)
            t = (int(frm) // step + 1) * step
            prices = []
            while t <= to and len(prices) < int(q.get("max", 10)):
                o = round(px + ((t // step) % 9 - 4) * 0.0001 * (100 if digits == 3 else 1), digits)
                c = round(o + 0.0002 * (100 if digits == 3 else 1), digits)
                sp = 0.0001 * (100 if digits == 3 else 1)
                prices.append({"snapshotTime": utc(t).replace("-", "/"), "snapshotTimeUTC": utc(t),
                               "openPrice": {"bid": o, "ask": round(o + sp, digits)}, "closePrice": {"bid": c, "ask": round(c + sp, digits)},
                               "highPrice": {"bid": round(c + 3 * sp, digits), "ask": round(c + 4 * sp, digits)},
                               "lowPrice": {"bid": round(o - 3 * sp, digits), "ask": round(o - 2 * sp, digits)}, "lastTradedVolume": 120})
                t += step
            return self._send(200, {"prices": prices, "instrumentType": "CURRENCIES"})
        if u.path == "/api/v1/positions":
            return self._send(200, {"positions": list(STATE["positions"].values())})
        m = re.match(r"/api/v1/confirms/(.+)", u.path)
        if m:
            c = STATE["confirms"].get(m.group(1))
            return self._send(200, c) if c else self._send(404, {"errorCode": "error.not-found.dealReference"})
        if u.path == "/api/v1/history/activity":
            return self._send(200, {"activities": [a for a in STATE["activities"] if a["dealId"] == q.get("dealId")]})
        return self._send(404, {"errorCode": "not.found"})


def serve(port=18766):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 18766)
    while True:
        time.sleep(3600)
