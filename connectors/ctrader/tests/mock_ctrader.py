"""Mock cTrader Open API JSON/WebSocket server for connector tests.

Mimics the real server's JSON encoding: int64 values as strings, enums as names.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import time

import websockets
from websockets.asyncio.server import serve

ACC = 4455667
LOGIN = 9900123
SYMS = {1: ("EURUSD", 5, 1.08000), 2: ("GBPUSD", 5, 1.27000), 4: ("USDJPY", 3, 150.000)}
ASSETS = {1: "EUR", 2: "GBP", 3: "JPY", 4: "USD"}
BASEQ = {1: (1, 4), 2: (2, 4), 4: (4, 3)}


class Broker:
    def __init__(self):
        self.balance = 50000  # cents
        self.positions: dict[int, dict] = {}
        self.deals: list[dict] = []
        self.ids = itertools.count(1000)
        self.fail_amend = False
        self.received: list[tuple[int, dict]] = []

    def px(self, sid):
        return SYMS[sid][2]


B = Broker()


def s(v):
    return str(v)


async def handler(ws):
    async def reply(cid, pt, payload):
        await ws.send(json.dumps({"clientMsgId": cid, "payloadType": pt, "payload": payload}))

    async def spots():
        while True:
            for sid, (_, digits, p) in SYMS.items():
                bid = int(round(p * 1e5))
                await ws.send(json.dumps({"payloadType": 2131, "payload": {
                    "ctidTraderAccountId": s(ACC), "symbolId": s(sid), "bid": s(bid), "ask": s(bid + (10 if digits == 5 else 1200)),
                    "timestamp": s(int(time.time() * 1000))}}))
            await asyncio.sleep(1)

    task = None
    try:
        async for raw in ws:
            m = json.loads(raw)
            pt, p, cid = m["payloadType"], m.get("payload", {}), m.get("clientMsgId")
            B.received.append((pt, p))
            if pt == 51:
                continue
            if pt == 2100:
                await reply(cid, 2101, {})
            elif pt == 2173:
                await reply(cid, 2174, {"accessToken": "tok2", "tokenType": "bearer", "expiresIn": "2628000", "refreshToken": "rtok2"})
            elif pt == 2149:
                await reply(cid, 2150, {"accessToken": p["accessToken"], "permissionScope": "SCOPE_TRADE",
                                        "ctidTraderAccount": [{"ctidTraderAccountId": s(ACC), "isLive": False, "traderLogin": s(LOGIN), "brokerTitleShort": "MockBroker"}]})
            elif pt == 2102:
                await reply(cid, 2103, {"ctidTraderAccountId": s(ACC)})
            elif pt == 2121:
                await reply(cid, 2122, {"ctidTraderAccountId": s(ACC), "trader": {"ctidTraderAccountId": s(ACC), "balance": s(B.balance), "depositAssetId": "4",
                                                                                   "leverageInCents": "10000", "moneyDigits": 2, "brokerName": "MockBroker", "traderLogin": s(LOGIN)}})
            elif pt == 2112:
                await reply(cid, 2113, {"ctidTraderAccountId": s(ACC), "asset": [{"assetId": s(k), "name": v} for k, v in ASSETS.items()]})
            elif pt == 2114:
                await reply(cid, 2115, {"ctidTraderAccountId": s(ACC), "symbol": [{"symbolId": s(k), "symbolName": v[0], "enabled": True,
                                                                                    "baseAssetId": s(BASEQ[k][0]), "quoteAssetId": s(BASEQ[k][1])} for k, v in SYMS.items()]})
            elif pt == 2116:
                await reply(cid, 2117, {"ctidTraderAccountId": s(ACC), "symbol": [{"symbolId": s(k), "digits": SYMS[k][1], "pipPosition": SYMS[k][1] - 1,
                                                                                    "lotSize": "10000000", "minVolume": "100000", "maxVolume": "10000000000", "stepVolume": "100000",
                                                                                    "tradingMode": "ENABLED"} for k in map(int, p["symbolId"])]})
            elif pt == 2127:
                await reply(cid, 2128, {"ctidTraderAccountId": s(ACC)})
                task = task or asyncio.create_task(spots())
            elif pt == 2124:
                await reply(cid, 2125, {"ctidTraderAccountId": s(ACC), "position": list(B.positions.values())})
            elif pt == 2187:
                await reply(cid, 2188, {"ctidTraderAccountId": s(ACC), "moneyDigits": 2,
                                        "positionUnrealizedPnL": [{"positionId": k, "grossUnrealizedPnL": "-35", "netUnrealizedPnL": "-50"} for k in B.positions]})
            elif pt == 2137:
                sid, period = int(p["symbolId"]), int(p["period"])
                step = {7: 15, 9: 60}[period]
                frm, to = int(p["fromTimestamp"]) // 60000, int(p["toTimestamp"]) // 60000
                start = (frm // step + 1) * step
                bars = []
                base = int(SYMS[sid][2] * 1e5)
                for k, t in enumerate(range(start, to + 1, step)):
                    if t < (time.time() / 60 - 60 * 24 * 40):
                        continue
                    low = base - 50 + (k % 7) * 3
                    bars.append({"volume": "100", "period": "H1" if period == 9 else "M15", "low": s(low),
                                 "deltaOpen": "20", "deltaClose": "30", "deltaHigh": "60", "utcTimestampInMinutes": t})
                await reply(cid, 2138, {"ctidTraderAccountId": s(ACC), "period": "H1", "trendbar": bars, "symbolId": s(sid)})
            elif pt == 2133:
                await reply(cid, 2134, {"ctidTraderAccountId": s(ACC), "deal": B.deals, "hasMore": False})
            elif pt == 2106:
                sid = int(p["symbolId"])
                assert p["orderType"] == 1 and "stopLoss" not in p, "market orders must use relative SL"
                rel = int(p["relativeStopLoss"])
                digits = SYMS[sid][1]
                assert rel % 10 ** (5 - digits) == 0, "Relative stop loss has invalid precision"
                price = SYMS[sid][2] + (0.0001 if p["tradeSide"] == 1 else 0)
                sl = round(price - rel / 1e5 if p["tradeSide"] == 1 else price + rel / 1e5, digits)
                pid, oid = next(B.ids), next(B.ids)
                pos = {"positionId": s(pid), "tradeData": {"symbolId": s(sid), "volume": s(p["volume"]), "tradeSide": "BUY" if p["tradeSide"] == 1 else "SELL",
                                                           "openTimestamp": s(int(time.time() * 1000)), "label": p.get("label"), "comment": p.get("comment")},
                       "positionStatus": "POSITION_STATUS_OPEN", "swap": "0", "price": price, "stopLoss": sl, "usedMargin": "108000", "moneyDigits": 2}
                order = {"orderId": s(oid), "tradeData": pos["tradeData"], "orderType": "MARKET", "orderStatus": "ORDER_STATUS_ACCEPTED", "clientOrderId": p.get("clientOrderId")}
                await reply(cid, 2126, {"ctidTraderAccountId": s(ACC), "executionType": "ORDER_ACCEPTED", "order": order, "position": pos})
                B.positions[pid] = pos
                order = dict(order, orderStatus="ORDER_STATUS_FILLED", executionPrice=price, executedVolume=p["volume"])
                deal = {"dealId": s(next(B.ids)), "orderId": s(oid), "positionId": s(pid), "volume": s(p["volume"]), "filledVolume": s(p["volume"]),
                        "symbolId": s(sid), "createTimestamp": s(int(time.time() * 1000)), "executionTimestamp": s(int(time.time() * 1000)),
                        "executionPrice": price, "tradeSide": pos["tradeData"]["tradeSide"], "dealStatus": "FILLED", "commission": "-35", "moneyDigits": 2}
                B.deals.append(deal)
                await reply(cid, 2126, {"ctidTraderAccountId": s(ACC), "executionType": "ORDER_FILLED", "order": order, "position": pos, "deal": deal})
            elif pt == 2110:
                pid = int(p["positionId"])
                if not B.fail_amend and pid in B.positions:
                    B.positions[pid]["stopLoss"] = p.get("stopLoss")
                    if p.get("takeProfit"):
                        B.positions[pid]["takeProfit"] = p["takeProfit"]
                await reply(cid, 2126, {"ctidTraderAccountId": s(ACC), "executionType": "ORDER_REPLACED", "position": B.positions.get(pid, {})})
            elif pt == 2111:
                pid = int(p["positionId"])
                pos = B.positions.pop(pid)
                close_px = pos["price"] - 0.0010
                deal = {"dealId": s(next(B.ids)), "orderId": s(next(B.ids)), "positionId": s(pid), "volume": pos["tradeData"]["volume"],
                        "filledVolume": pos["tradeData"]["volume"], "symbolId": pos["tradeData"]["symbolId"],
                        "createTimestamp": s(int(time.time() * 1000)), "executionTimestamp": s(int(time.time() * 1000)),
                        "executionPrice": close_px, "tradeSide": "SELL", "dealStatus": "FILLED", "commission": "-35", "moneyDigits": 2,
                        "closePositionDetail": {"entryPrice": pos["price"], "grossProfit": "-100", "swap": "0", "commission": "-35", "balance": s(B.balance - 170), "moneyDigits": 2}}
                B.deals.append(deal)
                B.balance -= 170
                await reply(cid, 2126, {"ctidTraderAccountId": s(ACC), "executionType": "ORDER_FILLED", "position": dict(pos, positionStatus="POSITION_STATUS_CLOSED"), "deal": deal})
            else:
                await reply(cid, 2142, {"errorCode": "UNSUPPORTED", "description": f"mock does not handle {pt}"})
    except websockets.ConnectionClosed:
        pass
    finally:
        if task:
            task.cancel()


async def main(port=18765):
    async with serve(handler, "127.0.0.1", port):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
