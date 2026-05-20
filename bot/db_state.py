from __future__ import annotations

import sqlite3
import json
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
"""


class KV:
    def __init__(self, path: str = "state.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute(SCHEMA)
        self.conn.commit()

    # ---- 通用字符串 get/set（JSON 存储需要）----
    def get_str(self, key: str) -> Optional[str]:
        cur = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        return str(row[0])

    def set_str(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, str(value)),
        )
        self.conn.commit()
    # -----------------------------------------------

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.get_str(key)
        if raw is None:
            return default
        return int(raw)

    def set_int(self, key: str, value: int) -> None:
        self.set_str(key, str(int(value)))

    def set_json(self, key: str, value: Any) -> None:
        self.set_str(key, json.dumps(value, separators=(",", ":")))

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_str(key)
        if raw is None:
            return default
        return json.loads(raw)
