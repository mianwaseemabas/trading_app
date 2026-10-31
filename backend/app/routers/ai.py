"""AI assistant / trainer. Explains the app's data and teaches trading concepts.

By design (implementation plan §5) the AI is never an authority over trades:
it has no tools, cannot place, modify or close orders, and cannot change settings.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import BacktestRun, Position, Signal, User, utcnow
from ..secretbox import decrypt, encrypt
from ..security import verify_password
from ..services import trading

router = APIRouter(prefix="/api/ai", tags=["ai"])

PROVIDERS = {
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1/chat/completions", "model_hint": "e.g. gpt-4o-mini"},
    "anthropic": {"label": "Anthropic Claude", "url": "https://api.anthropic.com/v1/messages", "model_hint": "e.g. claude-sonnet-5-5"},
}
SYSTEM_PROMPT = """You are the built-in trading coach of "Qivynex Trade", a personal forex trading app.
Your job: explain what the app is doing and teach the user forex trading in plain, friendly language.

Rules you must follow:
- You cannot place, modify or close trades and cannot change settings. If asked, explain how the user can do it
  in the app (pages: Dashboard, Overview, Setups, SMC scanner, Live trading, News, Market, Signals, Trades,
  Backtest, Risk & Strategy, Brokers, Guide). Every strategy trade waits for the user's approval on the Setups page.
- When you review a setup or a market, use three short sections: OBSERVED (facts measured by the app),
  INTERPRETATION (what it may mean) and UNCERTAINTY (what could make it wrong). Always mention reasons against.
- SMC terms (liquidity sweep, BOS, CHoCH, order block, FVG, displacement) describe price patterns using the app's
  written rules; never claim they prove what institutions or "smart money" are doing.
- Never promise or predict profits. Automated and manual trading can lose money. Encourage demo trading first.
- Respect the risk rules (0.5% risk per trade, mandatory stop-loss, daily/weekly loss limits). Never suggest
  removing a stop-loss, martingale, averaging down or increasing risk to recover losses.
- When you use numbers from the APP DATA below, say they come from the app. If data is missing, say so.
- Keep answers short and practical; use simple examples. The user may write in English or Urdu — reply in the
  user's language.
- This is education, not financial advice."""

_rate: dict[int, deque] = defaultdict(deque)


def _context(db: Session) -> str:
    tr = state.get(db, "trading")
    mode = tr["mode"] if tr["mode"] in trading.ORDER_MODES else "PAPER"
    acct, meta = trading.account_state(db, mode)
    risk = state.get(db, "risk")
    strat = state.get(db, "strategy")
    positions = trading.open_positions(db, mode)
    sigs = db.scalars(select(Signal).where(Signal.action != "NONE").order_by(Signal.id.desc()).limit(8)).all()
    last_bt = db.scalar(select(BacktestRun).where(BacktestRun.status == "DONE").order_by(BacktestRun.id.desc()).limit(1))
    data = {
        "trading_mode": tr["mode"], "kill_switch": tr["kill_switch"], "locked": tr["locked"], "lock_reason": tr["lock_reason"],
        "broker": state.get(db, "brokers")["active"], "data_source": state.get(db, "data")["source"],
        "account": acct.__dict__ if acct else None, "account_type": meta.get("trade_mode"),
        "risk_settings": {k: risk[k] for k in ("risk_per_trade_pct", "daily_loss_pct", "weekly_loss_pct",
                                               "max_concurrent_positions", "max_spread_points", "commission_per_lot")},
        "strategy": {k: strat[k] for k in strat if k not in ("name",)},
        "open_positions": [{"symbol": p.symbol, "side": p.side, "lots": p.volume, "open": p.open_price, "sl": p.stop_loss,
                            "tp": p.take_profit, "current": p.current_price, "unrealized_pnl": p.unrealized_pnl} for p in positions],
        "recent_signals": [{"time": s.candle_time.isoformat(), "symbol": s.symbol, "tf": s.timeframe, "action": s.action,
                            "status": s.status, "entry": s.entry_ref, "sl": s.stop_loss, "tp": s.take_profit,
                            "failed_risk_checks": [c["check"] for c in (s.risk_checks or []) if not c.get("passed")],
                            "features": {k: v for k, v in (s.features or {}).items() if k in ("rsi", "ema_fast", "ema_slow", "macd_hist", "atr")}}
                           for s in sigs],
        "crypto": (lambda c: {"mode": c["mode"], "exchange": c["exchange"], "coins": c["symbols"], "spot_only": True,
                              "open_positions": [{"symbol": p.symbol, "qty": p.volume, "open": p.open_price, "sl": p.stop_loss,
                                                  "tp": p.take_profit, "unrealized_pnl": p.unrealized_pnl}
                                                 for p in db.scalars(select(Position).where(Position.mode.like("CRYPTO_%"),
                                                                                            Position.status == "OPEN")).all()]})(state.get(db, "crypto")),
        "last_backtest": ({"market": f"{last_bt.params.get('symbol')} {last_bt.params.get('timeframe')}",
                           "all": (last_bt.metrics or {}).get("all"), "out_of_sample": (last_bt.metrics or {}).get("out_of_sample"),
                           "warnings": (last_bt.metrics or {}).get("warnings"), "risk_rejections": (last_bt.metrics or {}).get("risk_rejections")}
                          if last_bt else None),
    }
    return json.dumps(data, default=str)[:12000]


def _call(provider: str, model: str, key: str, system: str, messages: list[dict]) -> str:
    if provider == "openai":
        payload = {"model": model, "messages": [{"role": "system", "content": system}] + messages, "max_tokens": 900}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    else:
        payload = {"model": model, "system": system, "messages": messages, "max_tokens": 900}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    req = urllib.request.Request(PROVIDERS[provider]["url"], data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise HTTPException(502, f"AI provider error {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise HTTPException(502, f"AI provider unreachable: {e}")
    if provider == "openai":
        return data["choices"][0]["message"]["content"]
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


@router.get("/settings")
def get_settings(user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    ai = state.get(db, "ai")
    return {"provider": ai["provider"], "model": ai["model"], "enabled": ai["enabled"], "share_data": ai["share_data"],
            "api_key_set": bool(ai.get("api_key_enc")), "providers": PROVIDERS, "last_test": ai.get("last_test")}


class AISettingsIn(BaseModel):
    provider: str
    model: str = Field(default="", max_length=100)
    api_key: str | None = Field(default=None, max_length=400)
    enabled: bool = False
    share_data: bool = True
    password: str


@router.put("/settings")
def put_settings(body: AISettingsIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "Password confirmation failed")
    if body.provider not in ("none", *PROVIDERS):
        raise HTTPException(400, "Unknown provider")
    ai = state.get_locked(db, "ai")
    ai.update(provider=body.provider, model=body.model.strip(), enabled=body.enabled and body.provider != "none",
              share_data=body.share_data)
    if body.api_key:
        ai["api_key_enc"] = encrypt(body.api_key.strip())
    if ai["enabled"] and not (ai["model"] and ai.get("api_key_enc")):
        raise HTTPException(400, "Enter the model name and API key to enable the assistant")
    state.put(db, "ai", ai, by=user.username)
    audit(db, "ai.settings_updated", user=user, ip=client_ip(request),
          details={"provider": ai["provider"], "model": ai["model"], "enabled": ai["enabled"], "key_changed": bool(body.api_key)})
    db.commit()
    return get_settings(user, db)


class ChatIn(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=30)


@router.post("/chat")
def chat(body: ChatIn, request: Request, user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    ai = state.get(db, "ai")
    if not ai["enabled"] or not ai.get("api_key_enc"):
        raise HTTPException(400, "The AI assistant is not set up. An administrator can enable it on the AI Assistant page.")
    q = _rate[user.id]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "Too many questions in a minute; please wait a moment")
    q.append(now)
    msgs = []
    for m in body.messages[-20:]:
        role = m.get("role")
        content = str(m.get("content", ""))[:4000]
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(400, "Last message must be from the user")
    system = SYSTEM_PROMPT
    if ai.get("share_data", True):
        system += "\n\nAPP DATA (live, JSON):\n" + _context(db)
    reply = _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), system, msgs)
    audit(db, "ai.question", user=user, ip=client_ip(request), details={"chars": len(msgs[-1]["content"])})
    db.commit()
    return {"reply": reply}


@router.post("/test")
def test_ai(request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    """Check that the configured provider + model actually answer (the model name is not assumed valid)."""
    ai = state.get(db, "ai")
    if not ai.get("api_key_enc"):
        raise HTTPException(400, "Save an API key first")
    t0 = time.time()
    try:
        reply = _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), "Reply with exactly: OK", [{"role": "user", "content": "ping"}])
        res = {"ok": True, "model": ai["model"], "provider": ai["provider"], "latency_ms": round((time.time() - t0) * 1000), "reply": reply[:80]}
    except HTTPException as e:
        res = {"ok": False, "model": ai["model"], "provider": ai["provider"], "error": str(e.detail)[:300]}
    ai2 = state.get_locked(db, "ai")
    ai2["last_test"] = {**res, "at": utcnow().isoformat()}
    state.put(db, "ai", ai2, by=user.username)
    audit(db, "ai.test", user=user, ip=client_ip(request), details={k: v for k, v in res.items() if k != "reply"})
    db.commit()
    return res
