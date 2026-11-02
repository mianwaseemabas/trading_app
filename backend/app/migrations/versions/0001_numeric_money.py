"""Store prices, volumes and money as exact NUMERIC instead of double precision.

Idempotent: only columns that are still double precision are converted, so it is a no-op on
databases created after this change (create_all already makes them NUMERIC).

Revision ID: 0001_numeric_money
Revises:
"""
from alembic import op
from sqlalchemy import text

revision = "0001_numeric_money"
down_revision = None
branch_labels = None
depends_on = None

COLUMNS = {
    "instruments": ["point", "contract_size", "tick_size", "tick_value", "volume_min", "volume_max", "volume_step"],
    "candles": ["open", "high", "low", "close"],
    "quotes": ["bid", "ask"],
    "account_snapshots": ["balance", "equity", "margin", "free_margin"],
    "signals": ["entry_ref", "stop_loss", "take_profit"],
    "orders": ["volume", "requested_price", "stop_loss", "take_profit", "fill_price", "filled_volume", "risk_amount"],
    "positions": ["volume", "open_price", "stop_loss", "take_profit", "close_price", "commission", "swap", "realized_pnl", "unrealized_pnl", "current_price"],
    "pending_orders": ["trigger_price", "stop_loss", "take_profit", "sizing_value"],
    "position_mgmt": ["initial_sl"],
    "smc_events": ["level", "zone_low", "zone_high"],
    "signal_outcomes": ["exit_price"],
    "ticks": ["bid", "ask"],
    "race_trades": ["entry", "stop_loss", "take_profit", "exit_price"],
}


def _convert(target: str, source: str) -> None:
    conn = op.get_bind()
    for table, cols in COLUMNS.items():
        found = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = :t AND data_type = :d"), {"t": table, "d": source})}
        todo = [c for c in cols if c in found]
        if todo:
            op.execute(f'ALTER TABLE "{table}" ' + ", ".join(f'ALTER COLUMN "{c}" TYPE {target} USING "{c}"::{target}' for c in todo))


def upgrade() -> None:
    _convert("numeric", "double precision")


def downgrade() -> None:
    _convert("double precision", "numeric")
