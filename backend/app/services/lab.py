"""Strategy lab: backtest every strategy on every watched market with realistic costs and rank them
by out-of-sample evidence. The owner chooses; nothing switches automatically."""
from __future__ import annotations

import logging
import threading
import traceback

from sqlalchemy.orm import Session

from .. import state
from ..db import session_scope
from ..models import Instrument, Quote, utcnow
from . import market_data as md
from .backtest import run_backtest
from .strategy import STRATEGIES
from .trading import spec_of

log = logging.getLogger("lab")
_lock = threading.Lock()


def backfill_crypto(db: Session, days: int, tfs=("H1", "H4")) -> dict:
    from .crypto import desk
    c = state.get(db, "crypto")
    x = desk.client(db)
    out = {}
    for sym in c.get("symbols", []):
        for tf in tfs:
            try:
                rows = x.history(sym, tf, days)
                n, _ = md.upsert_candles(db, sym, tf, rows, c["exchange"][:16])
                db.commit()
                out[f"{sym} {tf}"] = n
            except Exception as e:  # noqa: BLE001
                db.rollback()
                out[f"{sym} {tf}"] = f"error: {e}"[:200]
    return out


def _one(db: Session, symbol: str, tf: str, name: str, crypto: bool) -> dict | None:
    df = md.load_candles(db, symbol, tf, limit=25_000)
    if len(df) < 500:
        return {"symbol": symbol, "tf": tf, "strategy": name, "skipped": f"only {len(df)} candles (need 500+)"}
    inst = db.get(Instrument, symbol) or md.ensure_instrument(db, symbol)
    spec = spec_of(inst)
    spec.trade_allowed = True
    risk = state.get(db, "risk")
    cfg = {**state.get(db, "strategy"), "name": name}
    q = db.get(Quote, symbol)
    spread_pts = ((q.ask - q.bid) / inst.point) if q and inst.point and q.ask > q.bid else (12 if not crypto else 1)
    if crypto:
        cc = state.get(db, "crypto")
        price = float(df["close"].iloc[-1000:].mean())
        params = {"initial_balance": max(100.0, float(cc.get("paper_start", 100))), "account_currency": inst.currency_profit or "USDT",
                  "spread_points": spread_pts, "slippage_points": max(1, round(0.0005 * price / inst.point)),
                  "commission_per_lot": 2 * float(cc.get("fee_pct", 0.1)) / 100 * price, "swap_per_lot_per_night": 0.0,
                  "leverage": 1, "conversion_rate": 1.0}
    else:
        params = {"initial_balance": float(risk.get("reference_balance") or 500), "spread_points": spread_pts, "slippage_points": 3,
                  "commission_per_lot": float(risk.get("commission_per_lot", 7)), "swap_per_lot_per_night": -1.0, "leverage": 30}
    res = run_backtest(df, cfg, risk, spec, params)
    m = res["metrics"]
    a, ft = m["all"], (m.get("segments") or {}).get("final_test", {})
    ok = ft.get("trades", 0) >= 10 and (ft.get("profit_factor") or 0) >= 1.1 and (ft.get("net_pnl") or 0) > 0 and a.get("trades", 0) >= 30
    weak = a.get("trades", 0) < 30
    verdict = "PROMISING — paper test it" if ok else ("TOO FEW TRADES — no conclusion" if weak else "NO EDGE SHOWN after costs")
    return {"symbol": symbol, "tf": tf, "strategy": name, "candles": len(df), "period": [m.get("period_start"), m.get("period_end")],
            "trades": a.get("trades", 0), "win_rate_pct": a.get("win_rate_pct"), "profit_factor": a.get("profit_factor"),
            "avg_r": a.get("avg_r"), "avg_r_ci95": a.get("avg_r_ci95"), "net_pnl": a.get("net_pnl"), "max_dd_pct": a.get("max_drawdown_pct"),
            "final_test": {k: ft.get(k) for k in ("trades", "win_rate_pct", "profit_factor", "net_pnl", "avg_r")},
            "rejections": dict(sorted((m.get("risk_rejections") or {}).items(), key=lambda kv: -kv[1])[:3]),
            "verdict": verdict, "promising": ok, "crypto": crypto}


def run(params: dict) -> None:
    with _lock:
        with session_scope() as db:
            st = state.get(db, "strategy_lab")
            st.update(status="RUNNING", started_at=utcnow().isoformat(), finished_at="", error="", progress="")
            state.put(db, "strategy_lab", st)
        try:
            with session_scope() as db:
                bf = backfill_crypto(db, int(params["backfill_days"])) if params.get("backfill_days") else {}
                strat = state.get(db, "strategy")
                cc = state.get(db, "crypto")
                markets = [(s, False) for s in strat["symbols"]] + [(s, True) for s in cc.get("symbols", [])]
                results = []
                total = len(markets) * len(params["timeframes"]) * len(STRATEGIES)
                k = 0
                for sym, crypto in markets:
                    for tf in params["timeframes"]:
                        for name in STRATEGIES:
                            k += 1
                            try:
                                r = _one(db, sym, tf, name, crypto)
                            except Exception as e:  # noqa: BLE001
                                r = {"symbol": sym, "tf": tf, "strategy": name, "skipped": f"error: {e}"[:200]}
                            results.append(r)
                            if k % 5 == 0:
                                st = state.get(db, "strategy_lab")
                                st["progress"] = f"{k}/{total}"
                                state.put(db, "strategy_lab", st)
                                db.commit()
                ranked = sorted([r for r in results if not r.get("skipped")],
                                key=lambda r: (r["promising"], (r["final_test"].get("avg_r") or -9), r.get("avg_r") or -9), reverse=True)
                st = state.get(db, "strategy_lab")
                st.update(status="DONE", finished_at=utcnow().isoformat(), results=ranked, skipped=[r for r in results if r.get("skipped")],
                          backfill=bf, params=params, progress=f"{total}/{total}")
                state.put(db, "strategy_lab", st)
        except Exception as e:  # noqa: BLE001
            log.exception("lab run failed")
            with session_scope() as db:
                st = state.get(db, "strategy_lab")
                st.update(status="FAILED", error=f"{e}\n{traceback.format_exc()[-800:]}", finished_at=utcnow().isoformat())
                state.put(db, "strategy_lab", st)


def start(params: dict) -> bool:
    if _lock.locked():
        return False
    threading.Thread(target=run, args=(params,), daemon=True).start()
    return True
