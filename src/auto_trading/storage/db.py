from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SCHEMA_STATEMENTS = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('READY', 'OPENING', 'OPEN', 'CLOSING', 'CLOSED', 'ERROR')),
    qty INTEGER NOT NULL DEFAULT 0,
    avg_entry_price REAL,
    current_price REAL,
    score_at_entry INTEGER,
    target_weight REAL,
    opened_at TEXT,
    closed_at TEXT,
    exit_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol_status
    ON positions (symbol, status);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL UNIQUE,
    broker_order_id TEXT,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type TEXT NOT NULL CHECK (order_type IN ('LIMIT', 'MARKET')),
    intent TEXT NOT NULL,
    price REAL,
    qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    remaining_qty INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING_CREATE',
            'SUBMITTED',
            'ACKNOWLEDGED',
            'PARTIALLY_FILLED',
            'FILLED',
            'PENDING_REPLACE',
            'REPLACED',
            'PENDING_CANCEL',
            'CANCELED',
            'REJECTED',
            'FAILED',
            'UNKNOWN'
        )
    ),
    submitted_at TEXT,
    last_broker_update_at TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol_status
    ON orders (symbol, status);

CREATE INDEX IF NOT EXISTS idx_orders_position_id
    ON orders (position_id);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    broker_fill_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    fill_price REAL NOT NULL,
    fill_qty INTEGER NOT NULL,
    fill_amount REAL NOT NULL,
    filled_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_fills_order_id
    ON fills (order_id);

CREATE TABLE IF NOT EXISTS trade_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    entry_order_id INTEGER,
    exit_order_id INTEGER,
    entry_price REAL,
    exit_price REAL,
    qty INTEGER NOT NULL,
    gross_pnl REAL,
    net_pnl REAL,
    pnl_pct REAL,
    entry_at TEXT,
    exit_at TEXT,
    exit_reason TEXT,
    holding_days INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(id),
    FOREIGN KEY (entry_order_id) REFERENCES orders(id),
    FOREIGN KEY (exit_order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_symbol_entry_at
    ON trade_logs (symbol, entry_at);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')),
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_events_occurred_at
    ON system_events (occurred_at);

CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    snapshot_time TEXT NOT NULL,
    score_total INTEGER NOT NULL,
    volume_score INTEGER,
    momentum_score INTEGER,
    ma_score INTEGER,
    atr_score INTEGER,
    rsi_score INTEGER,
    price REAL NOT NULL,
    ma5 REAL,
    ma20 REAL,
    rsi REAL,
    atr REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_snapshots_symbol_snapshot_time
    ON strategy_snapshots (symbol, snapshot_time);
"""


@dataclass(slots=True)
class Database:
    path: Path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(SCHEMA_STATEMENTS)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
