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
from . import benchmark
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


def _setup(db: Session, symbol: str, tf: str, crypto: bool, limit: int = 25_000, fee_pct: float | None = None):
    """Candles, instrument spec, risk config and realistic cost parameters for one market (shared by lab and compare)."""
    df = md.load_candles(db, symbol, tf, limit=limit)
    inst = db.get(Instrument, symbol) or md.ensure_instrument(db, symbol)
    spec = spec_of(inst)
    spec.trade_allowed = True
    risk = state.get(db, "risk")
    q = db.get(Quote, symbol)
    spread_pts = ((q.ask - q.bid) / inst.point) if q and inst.point and q.ask > q.bid else (12 if not crypto else 1)
    if crypto:
        cc = state.get(db, "crypto")
        price = float(df["close"].iloc[-1000:].mean()) if len(df) else 1.0
        fee = float(cc.get("fee_pct", 0.1)) if fee_pct is None else fee_pct
        params = {"initial_balance": max(100.0, float(cc.get("paper_start", 100))), "account_currency": inst.currency_profit or "USDT",
                  "spread_points": spread_pts, "slippage_points": max(1, round(0.0005 * price / inst.point)),
                  "commission_per_lot": 2 * fee / 100 * price, "swap_per_lot_per_night": 0.0,
                  "leverage": 1, "conversion_rate": 1.0}
    else:
        fee = 0.0
        params = {"initial_balance": float(risk.get("reference_balance") or 500), "spread_points": spread_pts, "slippage_points": 3,
                  "commission_per_lot": float(risk.get("commission_per_lot", 7)), "swap_per_lot_per_night": -1.0, "leverage": 30}
    return df, inst, spec, risk, params, fee


def hold_for(df, cfg: dict, inst, params: dict, fee_pct: float) -> dict:
    from .indicators import required_history
    return benchmark.buy_and_hold(df, start=required_history(cfg) // 2, initial=float(params["initial_balance"]),
                                  spread=float(params["spread_points"]) * float(inst.point or 0), fee_pct=fee_pct)


def _one(db: Session, symbol: str, tf: str, name: str, crypto: bool, hold_cache: dict | None = None) -> dict | None:
    df, inst, spec, risk, params, fee = _setup(db, symbol, tf, crypto)
    if len(df) < 500:
        return {"symbol": symbol, "tf": tf, "strategy": name, "skipped": f"only {len(df)} candles (need 500+)"}
    cfg = {**state.get(db, "strategy"), "name": name}
    res = run_backtest(df, cfg, risk, spec, params)
    m = res["metrics"]
    a, ft = m["all"], (m.get("segments") or {}).get("final_test", {})
    ok = ft.get("trades", 0) >= 10 and (ft.get("profit_factor") or 0) >= 1.1 and (ft.get("net_pnl") or 0) > 0 and a.get("trades", 0) >= 30
    weak = a.get("trades", 0) < 30
    verdict = "PROMISING — paper test it" if ok else ("TOO FEW TRADES — no conclusion" if weak else "NO EDGE SHOWN after costs")
    hk = (symbol, tf)
    hold = (hold_cache or {}).get(hk) or hold_for(df, cfg, inst, params, fee)
    if hold_cache is not None:
        hold_cache[hk] = hold
    return {"symbol": symbol, "tf": tf, "strategy": name, "candles": len(df), "period": [m.get("period_start"), m.get("period_end")],
            "trades": a.get("trades", 0), "win_rate_pct": a.get("win_rate_pct"), "profit_factor": a.get("profit_factor"),
            "avg_r": a.get("avg_r"), "avg_r_ci95": a.get("avg_r_ci95"), "net_pnl": a.get("net_pnl"), "max_dd_pct": a.get("max_drawdown_pct"),
            "return_pct": a.get("return_pct"),
            "final_test": {k: ft.get(k) for k in ("trades", "win_rate_pct", "profit_factor", "net_pnl", "avg_r")},
            "rejections": dict(sorted((m.get("risk_rejections") or {}).items(), key=lambda kv: -kv[1])[:3]),
            "vs_hold": benchmark.versus(a, hold),
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
                holds: dict = {}
                total = len(markets) * len(params["timeframes"]) * len(STRATEGIES)
                k = 0
                for sym, crypto in markets:
                    for tf in params["timeframes"]:
                        for name in STRATEGIES:
                            k += 1
                            try:
                                r = _one(db, sym, tf, name, crypto, holds)
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


# ------------------------------------------------------------------ side-by-side compare vs holding
_cmp_lock = threading.Lock()
MAX_COMPARE = 6


def compare(params: dict) -> None:
    """Backtest up to MAX_COMPARE strategies on ONE market with the same costs, plus buy-and-hold, as % equity curves."""
    with _cmp_lock:
        with session_scope() as db:
            state.put(db, "strategy_compare", {"status": "RUNNING", "started_at": utcnow().isoformat(), "params": params, "progress": ""})
        try:
            with session_scope() as db:
                sym, tf = params["symbol"], params["timeframe"]
                crypto = sym in set(state.get(db, "crypto").get("symbols", []))
                df, inst, spec, risk, prm, fee = _setup(db, sym, tf, crypto, limit=int(params.get("candles", 8000)),
                                                        fee_pct=params.get("fee_pct"))
                if len(df) < 500:
                    raise ValueError(f"only {len(df)} {tf} candles stored for {sym} (need 500+) — backfill history first")
                base = state.get(db, "strategy")
                init = float(prm["initial_balance"])
                hold = hold_for(df, {**base, "name": params["strategies"][0]}, inst, prm, fee)
                rows = []
                for k, name in enumerate(params["strategies"], 1):
                    cfg = {**base, "name": name}
                    res = run_backtest(df, cfg, risk, spec, prm)
                    a = res["metrics"]["all"]
                    ft = res["metrics"].get("segments", {}).get("final_test", {})
                    curve = [{"time": p["time"], "pct": round((p["equity"] - init) / init * 100, 2)} for p in res["equity_curve"]]
                    rows.append({"strategy": name, "trades": a.get("trades", 0), "win_rate_pct": a.get("win_rate_pct"),
                                 "profit_factor": a.get("profit_factor"), "return_pct": a.get("return_pct", 0.0),
                                 "max_dd_pct": a.get("max_drawdown_pct"), "avg_r": a.get("avg_r"), "total_costs": a.get("total_costs"),
                                 "final_test": {x: ft.get(x) for x in ("trades", "profit_factor", "net_pnl", "avg_r")},
                                 "warnings": res["metrics"].get("warnings", []), "vs_hold": benchmark.versus(a, hold),
                                 "curve": benchmark.downsample(curve)})
                    st = state.get(db, "strategy_compare")
                    st["progress"] = f"{k}/{len(params['strategies'])}"
                    state.put(db, "strategy_compare", st)
                    db.commit()
                rows.sort(key=lambda r: (r["vs_hold"].get("beats_risk_adjusted", False), r["return_pct"]), reverse=True)
                hold_curve = [{"time": p["time"], "pct": round((p["equity"] - init) / init * 100, 2)} for p in hold.get("curve", [])]
                beat = [r["strategy"] for r in rows if r["vs_hold"].get("code") == "BEATS_HOLD"]
                state.put(db, "strategy_compare", {
                    "status": "DONE", "finished_at": utcnow().isoformat(), "params": params, "symbol": sym, "timeframe": tf,
                    "crypto": crypto, "candles": len(df), "period": [df.index[0].isoformat(), df.index[-1].isoformat()],
                    "costs": {"spread_points": prm["spread_points"], "slippage_points": prm["slippage_points"],
                              "commission_per_lot": round(prm["commission_per_lot"], 4), "fee_pct_per_side": fee if crypto else None},
                    "hold": {k: v for k, v in hold.items() if k != "curve"}, "hold_curve": hold_curve, "results": rows,
                    "summary": (f"{len(beat)} of {len(rows)} beat just holding {sym} on both return and drawdown: {', '.join(beat)}."
                                if beat else f"None of the {len(rows)} strategies beat just holding {sym} on both return and drawdown."),
                    "honesty": "One market, one period, costs included. Holding excludes swap. Beating hold in the past does not mean it will again.",
                })
        except Exception as e:  # noqa: BLE001
            log.exception("strategy compare failed")
            with session_scope() as db:
                state.put(db, "strategy_compare", {"status": "FAILED", "error": str(e)[:400], "finished_at": utcnow().isoformat(),
                                                   "params": params})


def start_compare(params: dict) -> bool:
    if _cmp_lock.locked():
        return False
    threading.Thread(target=compare, args=(params,), daemon=True).start()
    return True
