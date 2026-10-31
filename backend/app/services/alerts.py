"""Alert center: in-app inbox + optional Telegram / email delivery.

* Every alert has a dedup key (unique) so a condition alerts once, not every tick.
* External delivery runs in the worker with retries (max 3) and is logged per channel.
* Alerts are information. They never place, change or close orders.
"""
from __future__ import annotations

import json
import logging
import smtplib
import urllib.request
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import state
from ..config import settings
from ..models import Alert, AlertDelivery, utcnow

log = logging.getLogger("alerts")

TYPES = {
    "SIGNAL_QUALIFIED": "Trade opportunity: a setup passed the score and all hard checks (with timeframe and validity)",
    "AUTO_TRADE": "A trade was placed automatically (auto-trade on)", "MARKET_NEWS": "High-impact market headline for your markets", "SIGNAL_REJECTED": "A setup was rejected (with reasons)",
    "SIGNAL_EXPIRED": "A setup expired without a decision", "APPROVAL_REQUIRED": "A setup waits for your approval",
    "APPROVAL_EXPIRING": "A setup expires in 10 minutes", "ENTRY_TRIGGERED": "A pending limit/stop order triggered",
    "ORDER_FILLED": "Order filled", "ORDER_REJECTED": "Order rejected", "STOP_HIT": "Stop-loss hit",
    "TARGET_1_HIT": "Take-profit hit", "POSITION_CLOSED": "Position closed (other reason)",
    "TRAILING_STOP_UPDATED": "Stop moved by break-even/trailing", "LOSS_LIMIT_REACHED": "Daily/weekly loss limit reached",
    "SPREAD_TOO_WIDE": "Spread too wide in an open market", "DATA_STALE": "Prices stale in an open market",
    "BROKER_DISCONNECTED": "Broker connection lost", "KILL_SWITCH_ACTIVE": "Kill switch engaged",
    "MODE_CHANGED": "Trading mode changed", "PROTECTION_FAILURE": "A stop-loss could not be placed / emergency",
    "RECONCILIATION": "Ledger or broker reconciliation mismatch", "DAILY_SUMMARY": "Daily summary", "WEEKLY_REVIEW": "Weekly review reminder",
    "AI_BRIEF": "AI morning brief (news, markets, your trades)", "AI_COACH": "AI weekly coach: three lessons from your trades",
    "HEALTH": "System health: prices/exchange/broker stopped for 5+ min, backups, recovery", "PRICE_ALERT": "Your price alert / chart line was touched",
}
DEFAULT_EXTERNAL = ["SIGNAL_QUALIFIED", "AUTO_TRADE", "MARKET_NEWS", "APPROVAL_REQUIRED", "STOP_HIT", "TARGET_1_HIT", "ORDER_REJECTED", "LOSS_LIMIT_REACHED", "BROKER_DISCONNECTED",
                    "KILL_SWITCH_ACTIVE", "PROTECTION_FAILURE", "RECONCILIATION", "DAILY_SUMMARY", "WEEKLY_REVIEW"]


def prefs(db: Session) -> dict:
    return state.get(db, "alert_prefs")


def raise_alert(db: Session, type_: str, title: str, body: str = "", *, severity: str = "info", dedup: str | None = None,
                symbol: str = "", signal_id: int | None = None, link: str = "") -> int | None:
    """Create an in-app alert once per dedup key. Returns the id, or None if it already existed."""
    key = (dedup or f"{type_}:{signal_id or ''}:{symbol}:{title}")[:160]
    stmt = insert(Alert).values(type=type_, severity=severity, title=title[:200], body=body[:4000], dedup_key=key, symbol=symbol,
                                signal_id=signal_id, link=link[:200], at=utcnow()).on_conflict_do_nothing(index_elements=["dedup_key"]).returning(Alert.id)
    return db.execute(stmt).scalar()


# ------------------------------------------------------------ audit bridge
def from_audit(db: Session, action: str, entity_id: str, details: dict) -> None:
    d = details or {}
    sym = str(d.get("symbol") or "")
    if action == "order.filled":
        raise_alert(db, "ORDER_FILLED", f"{d.get('mode', '')} {d.get('side', '')} {d.get('volume', '')} {sym} filled @ {d.get('price', '')}".strip(),
                    f"SL {d.get('sl')} · TP {d.get('tp')}", dedup=f"filled:{entity_id}", symbol=sym, link="#/live")
    elif action == "order.rejected":
        raise_alert(db, "ORDER_REJECTED", f"Order {entity_id} rejected {sym}", str(d.get("error", ""))[:500], severity="warning",
                    dedup=f"rejected:{entity_id}", symbol=sym, link="#/live")
    elif action == "position.closed":
        reason = str(d.get("reason", ""))
        t = {"stop_loss": "STOP_HIT", "take_profit": "TARGET_1_HIT"}.get(reason, "POSITION_CLOSED")
        if reason == "protection_failed":
            t = "PROTECTION_FAILURE"
        pnl = d.get("pnl")
        raise_alert(db, t, f"{sym} closed ({reason.replace('_', ' ')}) P&L {pnl:+.2f}" if isinstance(pnl, (int, float)) else f"{sym} closed ({reason})",
                    f"Mode {d.get('mode', '')}", severity="warning" if t in ("STOP_HIT", "PROTECTION_FAILURE") else "info",
                    dedup=f"closed:{entity_id}", symbol=sym, link="#/trades")
    elif action == "risk.locked":
        raise_alert(db, "LOSS_LIMIT_REACHED", "Loss limit reached — new trades blocked", str(d.get("reason", "")), severity="critical",
                    dedup=f"lock:{utcnow():%Y-%m-%d}:{d.get('reason', '')[:60]}", link="#/dashboard")
    elif action == "trading.kill_switch" and d.get("engaged"):
        raise_alert(db, "KILL_SWITCH_ACTIVE", "Kill switch ENGAGED — no new orders", str(d.get("reason", "")), severity="critical",
                    dedup=f"kill:{utcnow().isoformat(timespec='seconds')}", link="#/dashboard")
    elif action == "position.sl_tp_modified" and any(w in str(d.get("reason", "")) for w in ("trail", "break")):
        raise_alert(db, "TRAILING_STOP_UPDATED", f"Stop moved on position {entity_id}", f"{d.get('from')} → {d.get('to')} ({d.get('reason')})",
                    dedup=f"trail:{entity_id}:{d.get('to')}", link="#/live")
    elif action == "trading.mode_changed":
        raise_alert(db, "MODE_CHANGED", f"Trading mode → {d.get('to')}", f"from {d.get('from')}", dedup=f"mode:{utcnow().isoformat(timespec='seconds')}")
    elif action == "pending.triggered":
        raise_alert(db, "ENTRY_TRIGGERED", f"Pending order triggered {sym}", json.dumps(d)[:300], dedup=f"trig:{entity_id}", symbol=sym)


# --------------------------------------------------------- signal template
def signal_text(sig, assessment=None, risk: dict | None = None, label: str = "PAPER SIGNAL") -> tuple[str, str]:
    dg = 3 if sig.symbol.endswith("JPY") else (2 if sig.symbol.endswith(("USDT", "USDC")) else 5)
    f = lambda v: "unavailable" if v is None else f"{v:.{dg}f}"  # noqa: E731
    title = f"QIVYNEX | {label} — {sig.symbol} {sig.action} · {sig.timeframe}"
    valid = None
    try:
        from . import market_data as _md
        if sig.timeframe in _md.TF_SECONDS:
            valid = sig.candle_time + 3 * _md.tf_delta(sig.timeframe)
    except Exception:
        valid = None
    eco = (assessment.economics if assessment else {}) or {}
    data = (assessment.data if assessment else {}) or {}
    lines = [
        f"Status: {sig.status.replace('_', ' ').title()}",
        f"Strategy: {sig.strategy} v{sig.strategy_version}",
        f"Timeframe: {sig.timeframe} · signal candle {sig.candle_time:%d %b %H:%M} UTC" + (f" · valid until about {valid:%d %b %H:%M} UTC" if valid else ""),
        f"Score: {assessment.score}/100 (quality score, not win probability) · {assessment.verdict}" if assessment else "Score: unavailable",
        f"Data: {data.get('provider') or 'unavailable'}, quote age {data.get('quote_age_s', 'n/a')}s, spread {data.get('spread_pips') or data.get('spread_bps') or 'n/a'}{' pips' if data.get('spread_pips') else ' bps' if data.get('spread_bps') else ''}",
        f"Entry: {f(sig.entry_ref)} · Stop: {f(sig.stop_loss)} · Target: {f(sig.take_profit)}",
        f"Planned account risk: {eco.get('net_stop', 'unavailable')} ({eco.get('risk_pct_of_equity', 'n/a')}%)",
        f"Estimated net gain at target: {eco.get('net_target', 'unavailable')}",
        f"Estimated loss at stop: {eco.get('net_stop', 'unavailable')} — can exceed due to gaps/slippage",
        f"Net R:R: {eco.get('net_rr', 'unavailable')}",
        f"Reason: {'; '.join(r.get('check', '') for r in (sig.reasons or [])[:3] if isinstance(r, dict) and r.get('passed'))}",
        f"Warnings: {'; '.join((assessment.reasons if assessment else [])[:3]) or 'none recorded'}",
        "Action: Review in Qivynex (no automatic live order)",
    ]
    return title, "\n".join(lines)


# ---------------------------------------------------------------- delivery
def telegram_api(token: str, method: str, payload: dict | None = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _telegram(db: Session, text: str) -> tuple[bool, str]:
    from ..secretbox import decrypt
    p = prefs(db).get("telegram", {})
    token = decrypt(p["bot_token_enc"]) if p.get("bot_token_enc") else settings.telegram_bot_token
    chat = p.get("chat_id") or settings.telegram_chat_id
    if not token or not chat:
        return False, "Telegram not configured"
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=json.dumps({"chat_id": chat, "text": text[:4000]}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True, "sent"
    except Exception as e:
        return False, str(e)[:300]


def _push(db: Session, a) -> tuple[bool, str]:
    from . import webpush
    from ..models import PushSubscription
    if not db.scalar(select(PushSubscription.id).limit(1)):
        return False, "Phone notifications not configured"
    n_ok, n_bad = webpush.send(db, a.title, a.body[:300], a.link or "#/alerts")
    return n_ok > 0, f"{n_ok} sent, {n_bad} failed"


def _email(db: Session, subject: str, text: str) -> tuple[bool, str]:
    from ..secretbox import decrypt
    e = prefs(db).get("email", {})
    if not (e.get("host") and e.get("to") and e.get("from")):
        return False, "Email not configured"
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject[:150], e["from"], e["to"]
        msg.set_content(text)
        with smtplib.SMTP(e["host"], int(e.get("port") or 587), timeout=15) as s:
            if e.get("starttls", True):
                s.starttls()
            if e.get("username"):
                s.login(e["username"], decrypt(e["password_enc"]) if e.get("password_enc") else "")
            s.send_message(msg)
        return True, "sent"
    except Exception as ex:
        return False, str(ex)[:300]


def deliver_pending(db: Session, limit: int = 20) -> None:
    """Send new alerts to the channels the owner chose (worker). Retries failed sends up to 3 times."""
    p = prefs(db)
    since = utcnow() - timedelta(hours=6)
    alerts = db.scalars(select(Alert).where(Alert.at >= since).order_by(Alert.id).limit(500)).all()
    sent = 0
    for a in alerts:
        for ch in ("telegram", "email", "push"):
            if a.type not in p.get("channels", {}).get(ch, []):
                continue
            prev = db.scalar(select(AlertDelivery).where(AlertDelivery.alert_id == a.id, AlertDelivery.channel == ch))
            if prev and (prev.status == "sent" or prev.attempts >= 3 or prev.status == "skipped"):
                continue
            if prev and (utcnow() - prev.at).total_seconds() < 60 * prev.attempts:
                continue
            text = f"{a.title}\n{a.body}".strip()
            if ch == "push":
                ok, detail = _push(db, a)
            else:
                ok, detail = _telegram(db, text) if ch == "telegram" else _email(db, a.title, text)
            status = "sent" if ok else ("skipped" if "not configured" in detail else "failed")
            if prev:
                prev.status, prev.detail, prev.attempts, prev.at = status, detail, prev.attempts + 1, utcnow()
            else:
                db.add(AlertDelivery(alert_id=a.id, channel=ch, status=status, detail=detail))
            sent += 1
            if sent >= limit:
                return


def unread_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Alert).where(Alert.read_at.is_(None))) or 0


# ---------------------------------------------------------- scheduled items
def scheduled(db: Session) -> None:
    """Daily summary (20:30 UTC) and weekly review reminder (Friday 21:30 UTC)."""
    from ..models import Position, Signal
    now = utcnow()
    if now.hour == 20 and now.minute >= 30:
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        closed = db.scalars(select(Position).where(Position.status == "CLOSED", Position.closed_at >= day0)).all()
        by_mode: dict = {}
        for p in closed:
            m = by_mode.setdefault(p.mode, [0, 0.0])
            m[0] += 1
            m[1] += p.realized_pnl or 0
        sigs = db.scalars(select(Signal).where(Signal.created_at >= day0, Signal.action != "NONE")).all()
        funnel = {}
        for s in sigs:
            funnel[s.status] = funnel.get(s.status, 0) + 1
        body = "\n".join([f"{m}: {n} trade(s), net {v:+.2f}" for m, (n, v) in by_mode.items()] or ["No closed trades today."])
        body += "\nSetups today: " + (", ".join(f"{k.lower()} {v}" for k, v in funnel.items()) or "none")
        body += "\nNo trade is a valid result. Review completed trades weekly, not every candle."
        raise_alert(db, "DAILY_SUMMARY", f"Daily summary {now:%d %b}", body, dedup=f"daily:{now:%Y-%m-%d}", link="#/trades")
    if now.weekday() == 4 and now.hour == 21 and now.minute >= 30:
        raise_alert(db, "WEEKLY_REVIEW", "Weekly review (15–30 min)",
                    "Review every completed paper signal and its cost-adjusted outcome; compare expected vs actual fills; "
                    "note rule violations; change at most one thing. If no edge is shown, keep live mode off.",
                    dedup=f"weekly:{now:%G-%V}", link="#/trades")
