"""Exchange adapters: request signing and the buy → protect → exit flow against fake transports."""
import base64
import hashlib
import hmac
import json
import urllib.parse

import pytest

from app.services.crypto import exchanges as ex


class Fake:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body):
        u = urllib.parse.urlparse(url)
        self.calls.append({"method": method, "path": u.path, "query": dict(urllib.parse.parse_qsl(u.query)), "headers": headers,
                           "body": body.decode() if body else ""})
        r = self.routes.get((method, u.path))
        if r is None:
            return 404, {"msg": f"no route {method} {u.path}"}
        return (200, r(self.calls[-1])) if callable(r) else (200, r)


def test_binance_signature_matches_official_example():
    b = ex.Binance("k", "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
    q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert b.sign(q) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_number_formatting():
    assert ex.fmt_dec(0.0123456, 0.00001) == "0.01234"
    assert ex.fmt_dec(1.99, 1) == "1"
    assert ex.fmt_px(64123.456, 0.01) == "64123.46"
    assert ex.fmt_px(0.123456, 0.0001) == "0.1235"


BINFO = {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
                      "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT"],
                      "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"}, {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                                  {"filterType": "NOTIONAL", "minNotional": "5"}]}]}


def test_binance_buy_and_oco_protection():
    f = Fake({
        ("GET", "/api/v3/exchangeInfo"): BINFO,
        ("POST", "/api/v3/order"): {"orderId": 11, "status": "FILLED", "executedQty": "0.00100", "cummulativeQuoteQty": "64.00",
                                    "fills": [{"price": "64000", "qty": "0.001", "commission": "0.000001", "commissionAsset": "BTC"}]},
        ("POST", "/api/v3/orderList/oco"): {"orderListId": 77},
        ("GET", "/api/v3/orderList"): {"listOrderStatus": "ALL_DONE", "orders": [{"orderId": 5}, {"orderId": 6}]},
        ("GET", "/api/v3/order"): lambda c: {"orderId": int(c["query"]["orderId"]), "type": "STOP_LOSS" if c["query"]["orderId"] == "5" else "LIMIT_MAKER",
                                             "status": "FILLED" if c["query"]["orderId"] == "5" else "EXPIRED", "executedQty": "0.00099", "cummulativeQuoteQty": "62.37"},
    })
    b = ex.Binance("key", "secret", testnet=True, http=f)
    fill = b.market_buy("BTCUSDT", 0.0010049, "S1-CRYPTO")
    assert fill.qty == pytest.approx(0.000999) and fill.avg_price == pytest.approx(64000)
    buy = [c for c in f.calls if c["path"] == "/api/v3/order"][0]
    params = dict(urllib.parse.parse_qsl(buy["body"]))
    assert params["quantity"] == "0.00100" and params["type"] == "MARKET" and buy["headers"]["X-MBX-APIKEY"] == "key"
    unsigned = buy["body"].rsplit("&signature=", 1)[0]
    assert params["signature"] == hmac.new(b"secret", unsigned.encode(), hashlib.sha256).hexdigest()
    assert all(c["path"] != "/api/v3/order" or "testnet" not in "x" for c in f.calls)
    prot = b.protect("BTCUSDT", fill.qty, 63000, 66000, "S1-CRYPTO")
    assert prot.kind == "oco" and prot.exchange_tp
    oco = dict(urllib.parse.parse_qsl([c for c in f.calls if c["path"] == "/api/v3/orderList/oco"][0]["body"]))
    assert oco["aboveType"] == "LIMIT_MAKER" and oco["abovePrice"] == "66000.00" and oco["belowType"] == "STOP_LOSS"
    assert oco["belowStopPrice"] == "63000.00" and oco["quantity"] == "0.00099" and oco["side"] == "SELL"
    st = b.protection_state("BTCUSDT", prot.ids, prot.kind)
    assert st["filled"] and st["reason"] == "stop_loss" and st["exit_price"] == pytest.approx(63000)


def test_okx_signing_demo_header_and_algo():
    f = Fake({
        ("GET", "/api/v5/public/instruments"): {"code": "0", "data": [{"instId": "BTC-USDT", "baseCcy": "BTC", "quoteCcy": "USDT", "tickSz": "0.1",
                                                                      "lotSz": "0.00000001", "minSz": "0.00001", "state": "live"}]},
        ("POST", "/api/v5/trade/order"): {"code": "0", "data": [{"ordId": "O1", "sCode": "0"}]},
        ("GET", "/api/v5/trade/order"): {"code": "0", "data": [{"state": "filled", "accFillSz": "0.001", "avgPx": "64000", "fee": "-0.000001",
                                                                "feeCcy": "BTC", "side": "buy"}]},
        ("POST", "/api/v5/trade/order-algo"): {"code": "0", "data": [{"algoId": "A1", "sCode": "0"}]},
    })
    o = ex.Okx("key", "secret", "pass", testnet=True, http=f)
    fill = o.market_buy("BTCUSDT", 0.001, "S9-CRYPTO_TEST")
    assert fill.qty == pytest.approx(0.000999)
    call = [c for c in f.calls if c["path"] == "/api/v5/trade/order" and c["method"] == "POST"][0]
    h = call["headers"]
    assert h["x-simulated-trading"] == "1" and h["OK-ACCESS-PASSPHRASE"] == "pass"
    expect = base64.b64encode(hmac.new(b"secret", f"{h['OK-ACCESS-TIMESTAMP']}POST/api/v5/trade/order{call['body']}".encode(), hashlib.sha256).digest()).decode()
    assert h["OK-ACCESS-SIGN"] == expect
    body = json.loads(call["body"])
    assert body["tdMode"] == "cash" and body["tgtCcy"] == "base_ccy" and body["clOrdId"].isalnum()
    prot = o.protect("BTCUSDT", fill.qty, 63000, 66000, "S9")
    algo = json.loads([c for c in f.calls if c["path"] == "/api/v5/trade/order-algo"][0]["body"])
    assert prot.ids == ["A1"] and algo["ordType"] == "oco" and algo["slOrdPx"] == "-1" and algo["tpTriggerPx"] == "66000.0"


def test_bybit_stop_order_and_errors():
    f = Fake({
        ("GET", "/v5/market/instruments-info"): {"retCode": 0, "result": {"list": [{"symbol": "ETHUSDT", "baseCoin": "ETH", "quoteCoin": "USDT", "status": "Trading",
                                                                                  "priceFilter": {"tickSize": "0.01"},
                                                                                  "lotSizeFilter": {"basePrecision": "0.0001", "minOrderQty": "0.0001", "minOrderAmt": "5"}}]}},
        ("POST", "/v5/order/create"): lambda c: {"retCode": 0, "result": {"orderId": "SL1" if "StopOrder" in c["body"] else "B1"}},
        ("GET", "/v5/order/history"): {"retCode": 0, "result": {"list": [{"orderStatus": "Filled", "cumExecQty": "0.0100", "avgPrice": "3000",
                                                                          "cumExecValue": "30", "cumExecFee": "0.00001", "side": "Buy"}]}},
    })
    b = ex.Bybit("key", "secret", testnet=True, http=f)
    fill = b.market_buy("ETHUSDT", 0.01, "S3")
    prot = b.protect("ETHUSDT", fill.qty, 2900, 3200, "S3")
    assert prot.kind == "stop+app_tp" and not prot.exchange_tp and prot.ids == ["SL1"]
    sl = json.loads([c for c in f.calls if "StopOrder" in c["body"]][0]["body"])
    assert sl["triggerPrice"] == "2900.00" and sl["side"] == "Sell" and sl["qty"] == "0.0099"
    assert all(c["path"].startswith("/v5") for c in f.calls)
    with pytest.raises(ex.ExchangeError):
        ex.Bybit("", "", http=f).balances()


def test_binance_history_pages_forward():
    import time as _t
    now = int(_t.time() * 1000)
    step = 3600_000

    def kl(c):
        start = int(c["query"]["startTime"])
        rows = [[t, "1", "2", "0.5", "1.5", "10"] for t in range(start, min(start + 1000 * step, now), step)]
        return rows
    f = Fake({("GET", "/api/v3/klines"): kl})
    rows = ex.Binance("", "", http=f).history("BTCUSDT", "H1", 90)
    assert len(f.calls) == 3 and 2150 <= len(rows) <= 2170  # 90 days of hourly candles in 3 pages
    assert all(rows[i]["time"] < rows[i + 1]["time"] for i in range(len(rows) - 1))
