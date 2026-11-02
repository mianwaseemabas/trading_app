"""Persistent runtime configuration stored in the `settings` table.

Every value has a safe default. Trading starts OFF with the kill switch
released, and live trading cannot be enabled until the live gate is approved.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

TRADING_MODES = ["OFF", "SIGNALS_ONLY", "PAPER", "MT5_DEMO", "MT5_LIVE"]

DEFAULTS: dict[str, Any] = {
    "trading": {
        "mode": "OFF",
        "kill_switch": False,
        "kill_switch_reason": "",
        "locked": False,  # set automatically when a loss limit is breached
        "lock_reason": "",
    },
    "data": {
        # mt5 = Windows bridge pushes real broker data; simulator = synthetic data for UI testing only
        "source": "mt5",
        "stale_quote_seconds": 120,
        "stale_heartbeat_seconds": 90,
    },
    "risk": {"max_spread_pct_other": 0.2, "slippage_pct_other": 0.05, "max_trades_per_day": 3,
        "reference_balance": 500.0,
        "risk_per_trade_pct": 0.5,
        "daily_loss_pct": 2.0,
        "weekly_loss_pct": 4.0,
        "max_concurrent_positions": 1,
        "max_positions_per_symbol": 1,
        "max_same_currency_positions": 2,  # open positions leaning the same way on one currency (e.g. two USD shorts)
        "max_spread_points": 25,
        "max_slippage_points": 10,
        "commission_per_lot": 7.0,  # round-turn, account currency
        "include_unrealized_in_limits": True,
        "min_free_margin_pct": 50.0,  # require this % of equity to remain free after the trade
        "order_ttl_seconds": 60,
        # Fixed safety switches from the plan. Not editable via the API.
        "martingale": False,
        "grid": False,
        "averaging_down": False,
        "stop_loss_required": True,
    },
    "strategy": {
        "name": "trend_pullback",
        "version": "1.0.0",
        "symbols": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP"],
        "timeframes": ["M15", "H1"],
        "ema_fast": 50,
        "ema_slow": 200,
        "rsi_period": 14,
        "rsi_long_min": 50,
        "rsi_long_max": 70,
        "rsi_short_min": 30,
        "rsi_short_max": 50,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "reward_risk": 2.0,
        "max_atr_pct": 1.0,  # skip if ATR > this % of price (abnormal volatility)
        "trade_timeframes": ["H1"],  # timeframes allowed to place orders; others only log signals
        # --- SMC research definitions (used by the smc_sweep_bos strategy, the SMC scanner and reports)
        "smc_swing_n": 3,             # candles each side that confirm a swing high/low
        "smc_displacement_atr": 1.2,  # displacement candle body >= this x ATR
        "smc_fvg_min_atr": 0.1,       # minimum fair-value-gap size in ATR
        "smc_bos_window": 20,         # candles allowed between sweep and structure break
        "smc_retest_window": 15,      # candles allowed between break and retest
        "smc_max_stop_atr": 3.0,      # reject setups whose stop is wider than this x ATR
        "smc_reward_risk": 2.0,
        "smc_trend_filter": True,     # trade only on the side of EMA(slow)
    },
    "live_gate": {
        "broker_legal_checked": False,
        "strategy_oos_documented": False,
        "demo_results_satisfactory": False,
        "risk_failure_tests_passed": False,
        "academy_exam_passed": False,
        "credentials_secured": False,
        "approved": False,
        "approved_by": "",
        "approved_at": "",
    },
    "paper_account": {"balance": 500.0, "currency": "USD", "start_balance": 500.0, "reset_at": ""},
    "anchors": {},  # per-mode day/week equity anchors for loss limits
    "reconciliation": {"ok": True, "detail": "", "at": ""},
    "bridge": {"last_seen": "", "ip": "", "terminal": {}, "version": ""},
    "notifications": {"telegram_enabled": False, "notify_signals": False, "notify_orders": True},
    # Broker connections managed from the dashboard. Secret fields are stored encrypted (*_enc).
    "brokers": {
        "active": "none",  # none | capital | ctrader | mt5
        "version": 1,
        "allow_real": False,  # second switch (besides the live gate) for real-money orders
        "max_volume": 0.10,  # hard cap per order in lots, enforced by the connector
        "max_spread_points": 30,
        "capital": {"environment": "demo", "identifier": "", "api_key_enc": "", "api_password_enc": "", "account_id": ""},
        "ctrader": {"client_id": "", "client_secret_enc": "", "access_token_enc": "", "refresh_token_enc": "", "account_login": ""},
    },
    # Trading on/off switches and filters (Live trading page control bar).
    "crypto": {"mode": "OFF", "exchange": "binance", "strategy": "", "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"],
               "timeframes": ["M15", "H1", "H4", "D1"], "signal_timeframes": ["H1"], "paper_cash": 100.0, "paper_start": 100.0,
               "fee_pct": 0.1, "max_spread_pct": 0.15, "slippage_pct": 0.05, "allow_real": False, "signals": True},
    "crypto_keys": {"binance": {}, "okx": {}, "bybit": {}},
    "crypto_status": {"market_ok": False, "market_error": "", "market_at": "", "changes": {}, "balances": {}, "keys_ok": False,
                      "keys_error": ""},
    "favorites": [],
    # Independent risk limits for broker demo and real money. "risk" itself is the practice profile.
    # Filled with a copy of the practice limits on first start (migration risk_profiles_v1).
    "risk_profiles": {"demo": {}, "live": {}},
    "news_feeds": [],
    "push": {},
    "ai_jobs": {"brief_enabled": False, "brief_time": "07:30", "timezone": "Asia/Riyadh", "coach_enabled": False,
                "coach_day": 0, "coach_time": "20:00", "last_brief": "", "last_coach": ""},
    "tgbot": {"enabled": False},
    "health": {"active": {}},
    "backup": {"offsite_telegram": False},
    "backup_status": {},
    "migrations": [],
    "race": {"enabled": True, "timeframes": ["H1"], "last": {}},
    "tadawul": {"symbols": ["^TASI.SR", "2222.SR", "1120.SR"], "status": {}},
    "news_auto": {"breakout": False, "ai_filter": False, "wait_min": 15, "window_min": 120, "min_confidence": 0.7},
    "news_ai": {},
    "opportunity_cfg": {"auto_proven": False},
    "ui_prefs": {},
    "autopilot": {},
    "opportunities": {},
    "opportunities_sent": [],
    "headline_status": {"at": "", "feeds": {}},
    "strategy_lab": {"status": "NEVER_RUN", "results": [], "skipped": []},
    "alert_prefs": {"channels": {"telegram": ["SIGNAL_QUALIFIED", "AUTO_TRADE", "MARKET_NEWS", "ORDER_FILLED", "APPROVAL_REQUIRED", "STOP_HIT", "TARGET_1_HIT", "ORDER_REJECTED", "LOSS_LIMIT_REACHED",
                                              "BROKER_DISCONNECTED", "KILL_SWITCH_ACTIVE", "PROTECTION_FAILURE", "RECONCILIATION",
                                              "DAILY_SUMMARY", "WEEKLY_REVIEW"],
                                 "email": ["AUTO_TRADE", "LOSS_LIMIT_REACHED", "KILL_SWITCH_ACTIVE", "PROTECTION_FAILURE", "DAILY_SUMMARY"]},
                    "telegram": {}, "email": {}},
    "assessment": {"min_watch": 60, "min_candidate": 70, "min_eligible": 80, "min_sample": 30, "approval_min_tier": "WATCH"},
    "controls": {"require_approval": True, "approval_minutes": 60, "auto_execute": {}, "auto_min_verdict": "PAPER_TEST",
                 "auto_min_score": 70,
        "auto_trading": True,  # strategy may place orders (when the mode allows orders)
        "manual_trading": True,  # manual order ticket allowed
        "disabled_symbols": [],  # symbols where no new orders are opened
        "session_filter": False,  # only open new trades between these UTC hours
        "session_start_utc": 7,
        "session_end_utc": 20,
        "news_filter": True,  # pause new trades around important economic news
        "news_before_min": 30,
        "news_after_min": 15,
        "news_impacts": ["High"],
        "default_breakeven_r": None,  # auto-management applied to strategy trades
        "default_trailing_atr": None,
    },
    "news_status": {"ok": False, "last_fetch": "", "error": "", "count": 0},
    "connector_status": {},  # per connector: state, message, at
    "external_positions": {"connector": "", "at": "", "positions": []},
    "ai": {"provider": "none", "model": "", "api_key_enc": "", "enabled": False, "share_data": True},
}

# Risk settings where a larger value means more risk. Increases require password re-entry.
RISK_INCREASING_UP = {
    "max_trades_per_day",
    "max_spread_pct_other",
    "slippage_pct_other",
    "risk_per_trade_pct",
    "daily_loss_pct",
    "weekly_loss_pct",
    "max_concurrent_positions",
    "max_positions_per_symbol",
    "max_same_currency_positions",
    "max_spread_points",
    "max_slippage_points",
    "order_ttl_seconds",
}
RISK_INCREASING_DOWN = {"min_free_margin_pct", "commission_per_lot"}
RISK_LOCKED_KEYS = {"martingale", "grid", "averaging_down", "stop_loss_required"}

RISK_BOUNDS = {
    "max_trades_per_day": (1, 20),
    "max_spread_pct_other": (0.01, 2.0),
    "slippage_pct_other": (0.0, 1.0),
    "reference_balance": (1, 10_000_000),
    "risk_per_trade_pct": (0.01, 2.0),
    "daily_loss_pct": (0.1, 10.0),
    "weekly_loss_pct": (0.1, 20.0),
    "max_concurrent_positions": (1, 5),
    "max_positions_per_symbol": (1, 2),
    "max_same_currency_positions": (1, 5),
    "max_spread_points": (1, 200),
    "max_slippage_points": (0, 100),
    "commission_per_lot": (0, 100),
    "min_free_margin_pct": (10, 100),
    "order_ttl_seconds": (10, 600),
}


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    base = copy.deepcopy(DEFAULTS.get(key, {}))
    if row is None:
        return base
    if isinstance(base, dict) and isinstance(row.value, dict):
        base.update(row.value)
        return base
    return row.value


def get_locked(db: Session, key: str) -> Any:
    """Read a setting with a row lock (SELECT ... FOR UPDATE) for read-modify-write."""
    row = db.get(Setting, key, with_for_update=True)
    base = copy.deepcopy(DEFAULTS.get(key, {}))
    if row is None:
        return base
    if isinstance(base, dict) and isinstance(row.value, dict):
        base.update(row.value)
        return base
    return row.value


def put(db: Session, key: str, value: Any, by: str = "system") -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, updated_by=by))
    else:
        row.value = copy.deepcopy(value)
        row.updated_by = by


def ensure_defaults(db: Session) -> None:
    for k, v in DEFAULTS.items():
        if db.get(Setting, k) is None:
            db.add(Setting(key=k, value=copy.deepcopy(v)))


# ------------------------------------------------------------------ risk profiles
RISK_PROFILES = ("practice", "demo", "live")
PROFILE_OF_MODE = {"PAPER": "practice", "CRYPTO_PAPER": "practice", "MT5_DEMO": "demo", "CRYPTO_TEST": "demo",
                   "MT5_LIVE": "live", "CRYPTO_LIVE": "live"}


def tighter(a: dict, b: dict) -> dict:
    """Per key, the more cautious of two risk policies (used to keep live never looser than demo)."""
    out = dict(a)
    for k, bv in b.items():
        av = out.get(k)
        if av is None or bv is None or isinstance(bv, bool) != isinstance(av, bool):
            continue
        if isinstance(bv, bool):
            if k == "include_unrealized_in_limits":
                out[k] = av or bv
        elif k in RISK_INCREASING_UP or k == "reference_balance":
            out[k] = min(av, bv)
        elif k in RISK_INCREASING_DOWN:
            out[k] = max(av, bv)
    return out


def risk_profile(db: Session, name: str) -> dict:
    """Effective limits of one profile. Missing keys fall back to the practice profile; live is clamped to demo."""
    practice = get(db, "risk")
    if name == "practice":
        return practice
    profs = get(db, "risk_profiles")
    demo = {**practice, **(profs.get("demo") or {})}
    if name == "demo":
        return demo
    live = {**practice, **(profs.get("live") or {})}
    return tighter(live, demo)


def risk_for(db: Session, mode: str) -> dict:
    """Risk limits that apply to an account environment / trading mode."""
    return risk_profile(db, PROFILE_OF_MODE.get(mode, "practice"))
