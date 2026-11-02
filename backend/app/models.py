from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# Exact decimal storage for prices, volumes and money; Python keeps working with floats.
Num = Numeric(asdecimal=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TS = DateTime(timezone=True)


# --------------------------------------------------------------------------- auth
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    require_2fa: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Partially-authenticated session waiting for the TOTP code.
    pending_2fa: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    """Append-only (enforced by a database trigger)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


# ------------------------------------------------------------------- settings
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    source: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- market data
class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    digits: Mapped[int] = mapped_column(Integer, default=5)
    point: Mapped[float] = mapped_column(Num, default=0.00001)
    contract_size: Mapped[float] = mapped_column(Num, default=100000)
    tick_size: Mapped[float] = mapped_column(Num, default=0.00001)
    tick_value: Mapped[float | None] = mapped_column(Num, nullable=True)  # account ccy per tick per 1 lot
    volume_min: Mapped[float] = mapped_column(Num, default=0.01)
    volume_max: Mapped[float] = mapped_column(Num, default=100)
    volume_step: Mapped[float] = mapped_column(Num, default=0.01)
    currency_base: Mapped[str] = mapped_column(String(8), default="")
    currency_profit: Mapped[str] = mapped_column(String(8), default="")
    trade_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="default")
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (Index("ix_candles_sym_tf_time", "symbol", "timeframe", "time"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    time: Mapped[datetime] = mapped_column(TS, primary_key=True)  # candle open time (UTC)
    open: Mapped[float] = mapped_column(Num)
    high: Mapped[float] = mapped_column(Num)
    low: Mapped[float] = mapped_column(Num)
    close: Mapped[float] = mapped_column(Num)
    tick_volume: Mapped[float] = mapped_column(Float, default=0)
    spread_points: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(16), default="mt5")
    collected_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Quote(Base):
    __tablename__ = "quotes"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    bid: Mapped[float] = mapped_column(Num)
    ask: Mapped[float] = mapped_column(Num)
    time: Mapped[datetime] = mapped_column(TS)  # broker quote time
    source: Mapped[str] = mapped_column(String(16), default="mt5")
    collected_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16))  # mt5 | paper
    login: Mapped[str] = mapped_column(String(32), default="")
    server: Mapped[str] = mapped_column(String(64), default="")
    trade_mode: Mapped[str] = mapped_column(String(16), default="")  # demo | real | contest | paper
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    balance: Mapped[float] = mapped_column(Num, default=0)
    equity: Mapped[float] = mapped_column(Num, default=0)
    margin: Mapped[float] = mapped_column(Num, default=0)
    free_margin: Mapped[float] = mapped_column(Num, default=0)
    leverage: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------ signals/trading
class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "candle_time", "strategy_version", name="uq_signal_candle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    candle_time: Mapped[datetime] = mapped_column(TS)
    strategy: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(8))  # BUY | SELL | NONE
    entry_ref: Mapped[float | None] = mapped_column(Num, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Num, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Num, nullable=True)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    # NO_TRADE | SIGNAL_ONLY | REJECTED | ORDERED
    status: Mapped[str] = mapped_column(String(16), default="NO_TRADE", index=True)
    risk_checks: Mapped[list] = mapped_column(JSON, default=list)
    trading_mode: Mapped[str] = mapped_column(String(16), default="OFF")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(16))  # PAPER | MT5_DEMO | MT5_LIVE
    action: Mapped[str] = mapped_column(String(8), default="OPEN")  # OPEN | CLOSE
    position_ticket: Mapped[str | None] = mapped_column(String(32), nullable=True)  # for CLOSE
    position_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    volume: Mapped[float] = mapped_column(Num)
    requested_price: Mapped[float] = mapped_column(Num)
    stop_loss: Mapped[float | None] = mapped_column(Num, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Num, nullable=True)
    max_slippage_points: Mapped[int] = mapped_column(Integer, default=20)
    # PENDING -> SENT -> FILLED | REJECTED | EXPIRED | CANCELLED
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    signature: Mapped[str] = mapped_column(String(128), default="")
    broker_ticket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Num, nullable=True)
    filled_volume: Mapped[float | None] = mapped_column(Num, nullable=True)
    slippage_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    risk_amount: Mapped[float] = mapped_column(Num, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    broker_ticket: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    volume: Mapped[float] = mapped_column(Num)
    open_price: Mapped[float] = mapped_column(Num)
    stop_loss: Mapped[float | None] = mapped_column(Num, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Num, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", index=True)  # OPEN | CLOSED
    closed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    close_price: Mapped[float | None] = mapped_column(Num, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(32), default="")
    commission: Mapped[float] = mapped_column(Num, default=0)
    swap: Mapped[float] = mapped_column(Num, default=0)
    realized_pnl: Mapped[float | None] = mapped_column(Num, nullable=True)  # net, after costs
    unrealized_pnl: Mapped[float] = mapped_column(Num, default=0)
    current_price: Mapped[float | None] = mapped_column(Num, nullable=True)


class BridgeNonce(Base):
    __tablename__ = "bridge_nonces"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING | DONE | FAILED
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    trades: Mapped[list] = mapped_column(JSON, default=list)
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    folds: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


# ------------------------------------------------------- news / pro trading
class NewsEvent(Base):
    """Scheduled economic releases (economic calendar)."""

    __tablename__ = "news_events"
    __table_args__ = (UniqueConstraint("title", "currency", "time", name="uq_news_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(TS, index=True)  # UTC
    currency: Mapped[str] = mapped_column(String(8), index=True)
    impact: Mapped[str] = mapped_column(String(12))  # High | Medium | Low | Holiday
    title: Mapped[str] = mapped_column(String(200))
    forecast: Mapped[str] = mapped_column(String(40), default="")
    previous: Mapped[str] = mapped_column(String(40), default="")
    actual: Mapped[str] = mapped_column(String(40), default="")
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PendingOrder(Base):
    """Limit / stop orders held by the app until the price reaches the trigger level."""

    __tablename__ = "pending_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(8))  # LIMIT | STOP
    trigger_price: Mapped[float] = mapped_column(Num)
    stop_loss: Mapped[float] = mapped_column(Num)
    take_profit: Mapped[float | None] = mapped_column(Num, nullable=True)
    sizing_mode: Mapped[str] = mapped_column(String(12), default="risk_pct")  # risk_pct | risk_amount | lots
    sizing_value: Mapped[float | None] = mapped_column(Num, nullable=True)
    management: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(12), default="WORKING", index=True)  # WORKING|TRIGGERED|CANCELLED|EXPIRED|REJECTED
    expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    order_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class PositionMgmt(Base):
    """Automatic trade management for an open position (breakeven / trailing stop)."""

    __tablename__ = "position_mgmt"

    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    initial_sl: Mapped[float] = mapped_column(Num)
    breakeven_r: Mapped[float | None] = mapped_column(Float, nullable=True)  # move SL to entry at +x R
    trailing_atr: Mapped[float | None] = mapped_column(Float, nullable=True)  # trail SL at x * ATR
    breakeven_done: Mapped[bool] = mapped_column(Boolean, default=False)
    last_action_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class TradeNote(Base):
    """Trade journal note for a position."""

    __tablename__ = "trade_notes"

    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    note: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class SignalApproval(Base):
    """Manual approval decision for a strategy signal (human in the loop)."""

    __tablename__ = "signal_approvals"

    signal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS, index=True)
    status: Mapped[str] = mapped_column(String(16), default="WAITING", index=True)  # WAITING|APPROVED|DECLINED|EXPIRED|FAILED
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    order_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # risk preview at approval time


class SmcEvent(Base):
    """Logged SMC detections on closed candles (for review and research)."""

    __tablename__ = "smc_events"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "kind", "dir", "time", name="uq_smc_event"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    dir: Mapped[str] = mapped_column(String(8))
    time: Mapped[datetime] = mapped_column(TS, index=True)
    level: Mapped[float | None] = mapped_column(Num, nullable=True)
    zone_low: Mapped[float | None] = mapped_column(Num, nullable=True)
    zone_high: Mapped[float | None] = mapped_column(Num, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class TradeAttachment(Base):
    """Screenshot / file attached to a trade journal entry."""

    __tablename__ = "trade_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)


class CryptoLink(Base):
    """Exchange-side protection (stop-loss / OCO) attached to a crypto position."""

    __tablename__ = "crypto_links"

    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(16))
    prot_ids: Mapped[list] = mapped_column(JSON, default=list)
    prot_kind: Mapped[str] = mapped_column(String(16), default="")
    exchange_tp: Mapped[bool] = mapped_column(Boolean, default=False)
    buy_order_id: Mapped[str] = mapped_column(String(64), default="")


class DataIncident(Base):
    """A data-quality incident (stale feed, spread too wide) — one row per occurrence."""

    __tablename__ = "data_incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TS, nullable=True, index=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class Alert(Base):
    """In-app notification (alert center). Alerts are information, never orders."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(8), default="info")  # info | warning | critical
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), default="")
    signal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    link: Mapped[str] = mapped_column(String(200), default="")
    read_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    status: Mapped[str] = mapped_column(String(16))  # sent | failed | skipped
    detail: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=1)


class SignalAssessment(Base):
    """Score breakdown, hard-gate results, economics and verdict for a strategy signal."""

    __tablename__ = "signal_assessments"

    signal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    score: Mapped[int] = mapped_column(Integer, default=0)
    breakdown: Mapped[list] = mapped_column(JSON, default=list)
    gates: Mapped[list] = mapped_column(JSON, default=list)
    verdict: Mapped[str] = mapped_column(String(24), index=True)  # NO_TRADE | WATCH | PAPER_TEST | RESEARCH_SUPPORTED
    tier: Mapped[str] = mapped_column(String(12), index=True)     # REJECT | WATCH | CANDIDATE | ELIGIBLE
    reliability: Mapped[str] = mapped_column(String(24), default="INSUFFICIENT_EVIDENCE")
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    economics: Mapped[dict] = mapped_column(JSON, default=dict)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(24), default="score-v1")


class SignalEvent(Base):
    """Lifecycle transition of a signal (separate from order status)."""

    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    signal_id: Mapped[int] = mapped_column(BigInteger, index=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    state: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(Text, default="")


class SignalOutcome(Base):
    """What happened after a signal (shadow tracking, even if it was never traded)."""

    __tablename__ = "signal_outcomes"

    signal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)  # OPEN | TARGET | STOP | EXPIRED | NO_DATA
    resolved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    bars: Mapped[int] = mapped_column(Integer, default=0)
    r_gross: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_net: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_favorable_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_adverse_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Num, nullable=True)
    traded: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_to: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class NewsHeadline(Base):
    """Market-moving headlines from RSS/Atom feeds (central banks, forex, crypto, business)."""

    __tablename__ = "news_headlines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    source: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16), default="")
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[datetime] = mapped_column(TS, index=True)
    impact: Mapped[str] = mapped_column(String(8), default="Low", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    assets: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Tick(Base):
    """Recent price updates (kept a few hours) — used to draw seconds/minute charts."""

    __tablename__ = "ticks"
    __table_args__ = (Index("ix_ticks_sym_time", "symbol", "time"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    time: Mapped[datetime] = mapped_column(TS)
    bid: Mapped[float] = mapped_column(Num)
    ask: Mapped[float] = mapped_column(Num)


class AcademyProgress(Base):
    """Per-user progress through the in-app Academy lessons."""

    __tablename__ = "academy_progress"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_academy_user_lesson"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    lesson_id: Mapped[str] = mapped_column(String(64))
    steps_done: Mapped[list] = mapped_column(JSON, default=list)
    quiz_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PushSubscription(Base):
    """A phone/browser that accepts Web Push notifications."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    endpoint: Mapped[str] = mapped_column(String(800), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    device: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_ok: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    fails: Mapped[int] = mapped_column(Integer, default=0)


class ChartDrawing(Base):
    """A line drawn on a chart (horizontal or trend line), optionally with a one-time price alert."""

    __tablename__ = "chart_drawings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # hline | trend
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # {price} or {t1, p1, t2, p2} (t = unix seconds)
    color: Mapped[str] = mapped_column(String(16), default="#f2b443")
    label: Mapped[str] = mapped_column(String(60), default="")
    alert: Mapped[bool] = mapped_column(Boolean, default=False)
    side_last: Mapped[str] = mapped_column(String(6), default="")
    triggered_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class RaceTrade(Base):
    """Strategy race: every strategy paper-trades the same markets side by side, measured in R after spread."""

    __tablename__ = "race_trades"
    __table_args__ = (UniqueConstraint("strategy", "symbol", "timeframe", "candle_time", name="uq_race_trade"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(4))
    side: Mapped[str] = mapped_column(String(4))
    candle_time: Mapped[datetime] = mapped_column(TS)
    entry: Mapped[float] = mapped_column(Num)
    stop_loss: Mapped[float] = mapped_column(Num)
    take_profit: Mapped[float] = mapped_column(Num)
    cost_r: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", index=True)  # OPEN | WIN | LOSS | EXPIRED
    exit_time: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Num, nullable=True)
    r: Mapped[float | None] = mapped_column(Float, nullable=True)


class AcademyDay(Base):
    """A day on which the user learned something in the Academy (for streaks)."""

    __tablename__ = "academy_days"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_academy_day"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD in the user's time zone


class ExamResult(Base):
    """Academy readiness exam attempt."""

    __tablename__ = "exam_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    score: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    cert_id: Mapped[str] = mapped_column(String(24), default="")
