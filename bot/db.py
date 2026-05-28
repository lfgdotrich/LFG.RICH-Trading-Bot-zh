from __future__ import annotations
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  symbol TEXT NOT NULL,
  token_address TEXT NOT NULL,
  side TEXT NOT NULL,
  amount_in_wei TEXT NOT NULL,
  amount_out_min_wei TEXT NOT NULL,
  tx_hash TEXT,
  status TEXT NOT NULL
);
"""

class DB:
    def __init__(self, path: str = "state.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def log_trade(
        self,
        ts_utc: str,
        symbol: str,
        token_address: str,
        side: str,
        amount_in_wei: int,
        amount_out_min_wei: int,
        status: str,
        tx_hash: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO trades (ts_utc, symbol, token_address, side, amount_in_wei, amount_out_min_wei, tx_hash, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts_utc, symbol, token_address, side, str(amount_in_wei), str(amount_out_min_wei), tx_hash, status),
        )
        self.conn.commit()
