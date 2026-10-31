"""Market headlines from public RSS/Atom feeds, tagged by impact and by the markets they affect.

Tagging is a transparent keyword rule (shown on every headline), not a prediction. High-impact headlines
about your markets raise a MARKET_NEWS alert once per headline.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import state
from ..models import NewsHeadline, utcnow

log = logging.getLogger("headlines")

DEFAULT_FEEDS = [
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "category": "central bank"},
    {"name": "ECB", "url": "https://www.ecb.europa.eu/rss/press.html", "category": "central bank"},
    {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news", "category": "forex"},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "crypto"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "category": "crypto"},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "category": "business"},
]

HIGH = ["rate decision", "rate hike", "rate cut", "raises rates", "cuts rates", "interest rate", "fomc", "cpi", "inflation",
        "nonfarm", "non-farm", "payrolls", "jobs report", "gdp", "recession", "default", "war", "sanction", "tariff", "emergency",
        "bankrupt", "collapse", "crash", "hack", "exploit", "etf approv", "etf reject", "sec sues", "sec charges", "lawsuit", "ban",
        "halts", "liquidation", "depeg", "intervention", "quantitative"]
MEDIUM = ["fed", "ecb", "boe", "boj", "central bank", "powell", "lagarde", "yield", "treasur", "unemployment", "pmi", "retail sales",
          "earnings", "oil", "opec", "regulat", "stablecoin", "etf", "inflows", "outflows", "whale", "upgrade", "halving", "debt", "budget"]
ASSET_WORDS = {
    "USD": ["fed", "fomc", "powell", "dollar", "u.s.", "us ", "treasur", "payrolls", "nonfarm", "cpi", "united states"],
    "EUR": ["ecb", "lagarde", "euro", "eurozone", "germany", "france"], "GBP": ["boe", "bank of england", "pound", "sterling", "uk ", "britain"],
    "JPY": ["boj", "bank of japan", "yen", "japan"], "BTC": ["bitcoin", "btc"], "ETH": ["ether", "ethereum", " eth"],
    "SOL": ["solana", " sol "], "CRYPTO": ["crypto", "stablecoin", "binance", "coinbase", "sec ", "etf"], "OIL": ["oil", "opec", "crude"],
    "GOLD": ["gold", "bullion", "silver", "precious metal"],
    "INDICES": ["s&p", "nasdaq", "dow jones", "wall street", "stocks", "equities", "dax", "ftse", "stock market"],
    "SAUDI": ["saudi", "tadawul", "tasi", "aramco", "riyadh", "opec+"],
}


def tag(title: str, summary: str = "") -> tuple[str, list, list]:
    t = f" {title} {summary} ".lower()
    tags = [w for w in HIGH if w in t]
    impact = "High" if tags else "Low"
    if not tags:
        tags = [w for w in MEDIUM if w in t]
        impact = "Medium" if tags else "Low"
    assets = [a for a, ws in ASSET_WORDS.items() if any(w in t for w in ws)]
    return impact, tags[:6], assets


def _text(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or x.get("href")):
            return (x.text or x.get("href") or "").strip()
    return ""


def _date(s: str):
    if not s:
        return utcnow()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return utcnow()
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse(xml_bytes: bytes, feed: dict) -> list[dict]:
    if b"<!ENTITY" in xml_bytes[:5000]:
        raise ValueError("feed uses XML entities (refused for safety)")
    root = ET.fromstring(xml_bytes)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    out = []
    for it in items[:60]:
        title = html.unescape(_text(it, "title", f"{atom}title"))
        link = _text(it, "link", f"{atom}link", "guid")
        if not title or not link:
            continue
        summ = re.sub(r"<[^>]+>", " ", html.unescape(_text(it, "description", f"{atom}summary", f"{atom}content")))[:600]
        pub = _date(_text(it, "pubDate", f"{atom}published", f"{atom}updated", "{http://purl.org/dc/elements/1.1/}date"))
        impact, tags, assets = tag(title, summ)
        out.append({"url": link[:500], "source": feed["name"][:64], "category": feed.get("category", "")[:16], "title": title[:500],
                    "summary": " ".join(summ.split()), "published": pub, "impact": impact, "tags": tags, "assets": assets})
    return out


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (QivynexTrade news reader)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(3_000_000)


_last = {"t": 0.0}


def watched_assets(db: Session) -> set:
    strat = state.get(db, "strategy")
    cc = state.get(db, "crypto")
    from .instruments_meta import headline_assets
    out = {"USD"}
    for s in strat["symbols"]:
        out |= headline_assets(s)
    for s in cc.get("symbols", []):
        out |= {s.replace("USDT", "").replace("USDC", ""), "CRYPTO"}
    return out


def refresh(db: Session, force: bool = False, fetcher=fetch) -> dict:
    if not force and time.time() - _last["t"] < 600:
        return state.get(db, "headline_status")
    _last["t"] = time.time()
    feeds = state.get(db, "news_feeds") or DEFAULT_FEEDS
    st = {"at": utcnow().isoformat(), "feeds": {}}
    new_ids = []
    if fetcher is fetch and os.environ.get("APP_ENV") == "test":
        return st
    from concurrent.futures import ThreadPoolExecutor

    def _get(f):
        try:
            return f, fetcher(f["url"]), None
        except Exception as e:  # noqa: BLE001
            return f, None, e
    with ThreadPoolExecutor(max_workers=6) as ex:
        fetched = list(ex.map(_get, feeds))
    for f, raw, err in fetched:
        try:
            if err:
                raise err
            rows = parse(raw, f)
            rows = [r for r in rows if r["published"] >= utcnow() - timedelta(days=4)]
            if rows:
                res = db.execute(insert(NewsHeadline).values(rows).on_conflict_do_nothing(index_elements=["url"]).returning(NewsHeadline.id))
                new_ids += [x for (x,) in res.all()]
            st["feeds"][f["name"]] = {"ok": True, "items": len(rows)}
        except Exception as e:  # noqa: BLE001
            st["feeds"][f["name"]] = {"ok": False, "error": str(e)[:200]}
    state.put(db, "headline_status", st)
    watched = watched_assets(db)
    from .alerts import raise_alert
    for h in db.scalars(select(NewsHeadline).where(NewsHeadline.id.in_(new_ids or [0]), NewsHeadline.impact == "High")).all():
        hit = [a for a in h.assets if a in watched]
        if hit and h.published >= utcnow() - timedelta(hours=6):
            raise_alert(db, "MARKET_NEWS", f"{h.source}: {h.title}"[:200], f"Affects: {', '.join(hit)} · keywords: {', '.join(h.tags)}\n{h.url}",
                        severity="warning", dedup=f"news:{h.id}", link="#/news")
    db.query(NewsHeadline).filter(NewsHeadline.published < utcnow() - timedelta(days=30)).delete()
    return st
