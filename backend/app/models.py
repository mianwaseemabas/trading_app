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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


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
    point: Mapped[float] = mapped_column(Float, default=0.00001)
    contract_size: Mapped[float] = mapped_column(Float, default=100000)
    tick_size: Mapped[float] = mapped_column(Float, default=0.00001)
    tick_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # account ccy per tick per 1 lot
    volume_min: Mapped[float] = mapped_column(Float, default=0.01)
    volume_max: Mapped[float] = mapped_column(Float, default=100)
    volume_step: Mapped[float] = mapped_column(Float, default=0.01)
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
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    tick_volume: Mapped[float] = mapped_column(Float, default=0)
    spread_points: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(16), default="mt5")
    collected_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Quote(Base):
    __tablename__ = "quotes"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
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
    balance: Mapped[float] = mapped_column(Float, default=0)
    equity: Mapped[float] = mapped_column(Float, default=0)
    margin: Mapped[float] = mapped_column(Float, default=0)
    free_margin: Mapped[float] = mapped_column(Float, default=0)
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
    entry_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    volume: Mapped[float] = mapped_column(Float)
    requested_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_slippage_points: Mapped[int] = mapped_column(Integer, default=20)
    # PENDING -> SENT -> FILLED | REJECTED | EXPIRED | CANCELLED
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    signature: Mapped[str] = mapped_column(String(128), default="")
    broker_ticket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    risk_amount: Mapped[float] = mapped_column(Float, default=0)
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
    volume: Mapped[float] = mapped_column(Float)
    open_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    status: Mapped[str] = mapped_column(String(8), default="OPEN", index=True)  # OPEN | CLOSED
    closed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(32), default="")
    commission: Mapped[float] = mapped_column(Float, default=0)
    swap: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # net, after costs
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)


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
    trigger_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    sizing_mode: Mapped[str] = mapped_column(String(12), default="risk_pct")  # risk_pct | risk_amount | lots
    sizing_value: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    initial_sl: Mapped[float] = mapped_column(Float)
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
