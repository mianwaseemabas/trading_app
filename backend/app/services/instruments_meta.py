"""Which news currencies / headline topics move a market (forex pairs, metals, oil, indices)."""
from __future__ import annotations

METALS = {"XAUUSD": "GOLD", "GOLD": "GOLD", "XAGUSD": "GOLD", "SILVER": "GOLD"}
OIL = {"USOIL", "XTIUSD", "OIL_CRUDE", "UKOIL", "OIL_BRENT", "XBRUSD", "WTI", "BRENT"}
INDEX_CCY = {"US500": "USD", "US100": "USD", "NAS100": "USD", "US30": "USD", "SPX500": "USD", "US2000": "USD", "DE40": "EUR", "GER40": "EUR",
             "EU50": "EUR", "FR40": "EUR", "UK100": "GBP", "J225": "JPY", "AU200": "AUD", "HK50": "HKD"}


def news_currencies(symbol: str) -> list[str]:
    s = symbol.upper()
    if s in METALS or s in OIL:
        return ["USD"]
    if s in INDEX_CCY:
        return [INDEX_CCY[s]]
    if len(s) >= 6 and s[:6].isalpha():
        return [s[:3], s[3:6]]
    return [s]


def headline_assets(symbol: str) -> set[str]:
    s = symbol.upper()
    if s in METALS:
        return {"GOLD", "USD"}
    if s in OIL:
        return {"OIL", "USD"}
    if s in INDEX_CCY:
        return {"INDICES", INDEX_CCY[s]}
    if len(s) == 6 and s.isalpha():
        return {s[:3], s[3:], s}
    return {s}
