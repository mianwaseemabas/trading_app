"""Deterministic risk engine.

This module is the single authority on whether a trade may be placed and at
what size. It is pure (no I/O) so it can be unit-tested exhaustively. Neither
the strategy nor any AI/LLM component can override its decision.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


@dataclass
class InstrumentSpec:
    symbol: str
    point: float
    tick_size: float
    tick_value: float | None  # account currency per tick per 1 lot (from MT5), if known
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    currency_base: str = ""
    currency_profit: str = ""
    trade_allowed: bool = True


@dataclass
class AccountState:
    equity: float
    balance: float
    free_margin: float
    currency: str = "USD"
    leverage: int = 100


@dataclass
class OpenPos:
    symbol: str
    side: str
    unrealized_pnl: float = 0.0


@dataclass
class RiskContext:
    side: str  # BUY | SELL
    symbol: str
    bid: float
    ask: float
    quote_age_s: float
    stop_loss: float | None
    take_profit: float | None
    spec: InstrumentSpec
    account: AccountState
    cfg: dict
    open_positions: list[OpenPos] = field(default_factory=list)
    kill_switch: bool = False
    locked: bool = False
    lock_reason: str = ""
    mode: str = "OFF"
    day_loss: float = 0.0  # positive number = loss since day anchor
    week_loss: float = 0.0
    day_anchor_equity: float = 0.0
    week_anchor_equity: float = 0.0
    stale_quote_seconds: float = 120
    data_ok: bool = True
    data_detail: str = ""
    reconciliation_ok: bool = True
    reconciliation_detail: str = ""
    profit_to_account_rate: float | None = None  # 1 unit of profit ccy in account ccy
    base_to_account_rate: float | None = None  # 1 unit of base ccy in account ccy
    duplicate: bool = False


@dataclass
class RiskDecision:
    approved: bool
    volume: float = 0.0
    entry_price: float = 0.0
    risk_amount: float = 0.0
    risk_budget: float = 0.0
    loss_per_lot: float = 0.0
    margin_required: float = 0.0
    checks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def value_per_price_unit(ctx: RiskContext) -> float | None:
    """Account-currency P&L for a 1.0 price move on 1 lot."""
    s = ctx.spec
    if s.tick_value and s.tick_size:
        return s.tick_value / s.tick_size
    if ctx.profit_to_account_rate:
        return s.contract_size * ctx.profit_to_account_rate
    return None


def floor_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    n = math.floor(x / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(n * step, decimals + 2)


def evaluate(ctx: RiskContext) -> RiskDecision:
    cfg = ctx.cfg
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str = "") -> bool:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        return passed

    entry = ctx.ask if ctx.side == "BUY" else ctx.bid
    spread_points = (ctx.ask - ctx.bid) / ctx.spec.point if ctx.spec.point else float("inf")

    check("kill switch released", not ctx.kill_switch)
    check("no loss-limit lock", not ctx.locked, ctx.lock_reason)
    check("trading mode allows orders", ctx.mode in {"PAPER", "MT5_DEMO", "MT5_LIVE"}, ctx.mode)
    check("forbidden techniques disabled",
          not cfg.get("martingale") and not cfg.get("grid"), "martingale/grid must be off")
    check("not a duplicate order", not ctx.duplicate)
    check("market data fresh", ctx.data_ok, ctx.data_detail)
    check("quote fresh", ctx.quote_age_s <= ctx.stale_quote_seconds, f"age {ctx.quote_age_s:.0f}s")
    check("broker reconciliation ok", ctx.reconciliation_ok, ctx.reconciliation_detail)
    check("instrument tradable", ctx.spec.trade_allowed)
    check("valid bid/ask", ctx.bid > 0 and ctx.ask >= ctx.bid, f"bid {ctx.bid} ask {ctx.ask}")
    check("spread within limit", spread_points <= cfg["max_spread_points"],
          f"{spread_points:.1f} pts (max {cfg['max_spread_points']})")

    sl, tp = ctx.stop_loss, ctx.take_profit
    sl_ok = sl is not None and sl > 0 and ((ctx.side == "BUY" and sl < entry) or (ctx.side == "SELL" and sl > entry))
    check("stop-loss present and on the correct side", sl_ok, f"sl {sl} entry {entry}")
    if tp is not None:
        check("take-profit on the correct side",
              (ctx.side == "BUY" and tp > entry) or (ctx.side == "SELL" and tp < entry), f"tp {tp}")

    total_open = len(ctx.open_positions)
    same_symbol = [p for p in ctx.open_positions if p.symbol == ctx.symbol]
    check("concurrent positions below limit", total_open < cfg["max_concurrent_positions"],
          f"{total_open} open (max {cfg['max_concurrent_positions']})")
    check("positions per symbol below limit", len(same_symbol) < cfg["max_positions_per_symbol"],
          f"{len(same_symbol)} on {ctx.symbol}")
    check("no hedging (opposite position on symbol)", not any(p.side != ctx.side for p in same_symbol))
    check("no averaging down", not any(p.side == ctx.side and p.unrealized_pnl < 0 for p in same_symbol))

    day_limit = ctx.day_anchor_equity * cfg["daily_loss_pct"] / 100
    week_limit = ctx.week_anchor_equity * cfg["weekly_loss_pct"] / 100
    check("daily loss below limit", ctx.day_loss < day_limit, f"loss {ctx.day_loss:.2f} / limit {day_limit:.2f}")
    check("weekly loss below limit", ctx.week_loss < week_limit, f"loss {ctx.week_loss:.2f} / limit {week_limit:.2f}")

    equity = ctx.account.equity
    # Size from min(equity, reference balance): risk shrinks after losses but never
    # grows automatically above the configured reference balance.
    ref = cfg.get("reference_balance") or equity
    budget = max(0.0, min(equity, ref) * cfg["risk_per_trade_pct"] / 100)
    decision = RiskDecision(approved=False, entry_price=entry, risk_budget=round(budget, 2), checks=checks)

    vpu = value_per_price_unit(ctx)
    if not check("contract value known", vpu is not None and vpu > 0, "need tick value or conversion rate"):
        return decision
    if not sl_ok:
        return decision

    stop_dist = abs(entry - sl)
    slippage_allow = cfg["max_slippage_points"] * ctx.spec.point
    loss_per_lot = (stop_dist + slippage_allow) * vpu + float(cfg.get("commission_per_lot", 0))
    decision.loss_per_lot = round(loss_per_lot, 4)
    raw = budget / loss_per_lot if loss_per_lot > 0 else 0
    vol = min(floor_to_step(raw, ctx.spec.volume_step), ctx.spec.volume_max)
    sized = check(
        "position size fits risk budget (>= broker minimum)",
        vol >= ctx.spec.volume_min - 1e-12,
        f"computed {raw:.4f} lots, broker min {ctx.spec.volume_min}, budget {budget:.2f}",
    )
    if sized:
        decision.volume = vol
        decision.risk_amount = round(vol * loss_per_lot, 2)
        check("risk amount within budget", decision.risk_amount <= budget + 1e-9,
              f"{decision.risk_amount:.2f} <= {budget:.2f}")

        base_rate = ctx.base_to_account_rate
        if base_rate is None:
            if ctx.spec.currency_base == ctx.account.currency:
                base_rate = 1.0
            elif ctx.spec.currency_profit == ctx.account.currency:
                base_rate = entry
        if base_rate and ctx.account.leverage > 0:
            margin = vol * ctx.spec.contract_size * base_rate / ctx.account.leverage
            decision.margin_required = round(margin, 2)
            remaining = ctx.account.free_margin - margin
            need = equity * cfg["min_free_margin_pct"] / 100
            check("free margin sufficient", remaining >= need,
                  f"after trade {remaining:.2f}, required {need:.2f}")
        else:
            check("margin estimate available", False, "unknown leverage or conversion rate")

    decision.approved = all(c["passed"] for c in checks)
    if not decision.approved:
        decision.volume = decision.volume if sized else 0.0
    return decision
