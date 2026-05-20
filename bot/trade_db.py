from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from typing import Optional, List, Tuple


class TradeDB:
    # 将极小浮点残留视为 0（避免 dust lots 一直保持 “open”）
    _LOT_EPS_QTY = 1e-4

    @dataclass
    class Position:
        symbol: str
        token: str
        qty_token: float
        cost_bnb: float

    @dataclass
    class TradeRow:
        tx_hash: str
        ts: int
        symbol: str
        token: str
        side: str
        status: str
        note: str
        # 快照字段（数据库中以 TEXT 保存，这里返回 int）
        bnb_before_wei: Optional[int]
        tok_before_raw: Optional[int]
        token_dec: Optional[int]

    @dataclass(frozen=True)
    class SentTrade:
        tx_hash: str
        symbol: str
        token: str
        side: str

    def __init__(self, path: str = "state.db") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._ensure_schema()

        # 每个 symbol 的 dust 阈值（qty_open 低于该值时视为已关闭）
        self.dust_by_symbol: dict[str, float] = {}

    def set_dust_map(self, dust_by_symbol: dict[str, float]) -> None:
        # 规范化 key/value
        self.dust_by_symbol = {str(k): float(v) for k, v in (dust_by_symbol or {}).items()}

    def get_dust(self, symbol: str) -> float:
        return float(self.dust_by_symbol.get(str(symbol), 0.0))

    def pick_lot_id_by_qty(self, symbol: str, qty_to_sell: float) -> Optional[int]:
        """当 SELL 缺少目标 lot_id 时，选择一个要消耗的 lot id。

        重要：这个函数不会使用 FIFO。它会尝试把卖出数量与当前 open lots 匹配
        （list_open_lots 已经进行了 dust 过滤）。

        策略：
        1) 优先选择 qty_open >= qty_to_sell 且剩余量最小的 lot。
        2) 如果没有完全匹配（例如部分卖出或舍入误差），选择 qty_open 最接近 qty_to_sell 的 lot。
        """
        try:
            q = float(qty_to_sell)
        except Exception:
            return None
        if q <= 0:
            return None

        lots = self.list_open_lots(symbol)
        if not lots:
            return None

        # 1) 完全覆盖且剩余量最小
        fits = []
        for lot in lots:
            try:
                lo = float(lot.get("qty_open") or 0.0)
                if lo >= q and lo > 0:
                    fits.append((lo - q, int(lot.get("id"))))
            except Exception:
                continue
        if fits:
            fits.sort(key=lambda x: x[0])
            return int(fits[0][1])

        # 2) 按绝对差值选择最接近的 lot
        closest = []
        for lot in lots:
            try:
                lo = float(lot.get("qty_open") or 0.0)
                if lo > 0:
                    closest.append((abs(lo - q), int(lot.get("id"))))
            except Exception:
                continue
        if not closest:
            return None
        closest.sort(key=lambda x: x[0])
        return int(closest[0][1])

    def _columns(self, table: str) -> set[str]:
        cur = self.conn.execute(f"PRAGMA table_info({table});")
        return {row[1] for row in cur.fetchall()}

    def _ensure_schema(self) -> None:
        # trades：注意快照字段使用 TEXT，避免整数溢出
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                token TEXT,
                side TEXT,
                status TEXT,
                tx_hash TEXT UNIQUE,
                delta_bnb REAL,
                delta_token REAL,
                realized_pnl_bnb REAL,
                note TEXT,

                bnb_before_wei TEXT,
                tok_before_raw TEXT,
                token_dec INTEGER
            );
            """
        )

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                token TEXT,
                qty_token REAL,
                cost_bnb REAL
            );
            """
        )

        # FIFO lots：每次 BUY 一行，跟踪剩余数量/成本
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                token TEXT,
                buy_tx TEXT,

                qty_init REAL,
                cost_init_bnb REAL,

                qty_open REAL,
                cost_open_bnb REAL
            );
            """
        )

        # 每次 SELL 可以消耗多个 lots（fills）
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lot_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                symbol TEXT,
                token TEXT,
                lot_id INTEGER,
                sell_tx TEXT,

                qty_sold REAL,
                proceeds_bnb REAL,
                cost_bnb REAL,
                pnl_bnb REAL
            );
            """
        )

        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_symbol ON lots(symbol);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_token ON lots(token);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_ts ON lots(ts);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fills_symbol ON lot_fills(symbol);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fills_ts ON lot_fills(ts);")


        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_tx ON trades(tx_hash);")
        self.conn.commit()

        # 如果数据库是旧 schema，尽力补充缺失列
        cols = self._columns("trades")

        def add_col(col: str, ddl_type: str) -> None:
            self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl_type};")

        desired = [
            ("bnb_before_wei", "TEXT"),
            ("tok_before_raw", "TEXT"),
            ("token_dec", "INTEGER"),
        ]
        changed = False
        for col, typ in desired:
            if col not in cols:
                add_col(col, typ)
                changed = True

        if changed:
            self.conn.commit()

    # -----------------------
    # trades
    # -----------------------

    def insert_sent(
        self,
        symbol: str,
        token: str,
        side: str,
        tx_hash: str,
        note: str = "",
        bnb_before_wei: Optional[int] = None,
        tok_before_raw: Optional[int] = None,
        token_dec: Optional[int] = None,
    ) -> None:
        # 将快照整数以字符串保存，避免 INTEGER 溢出
        bnb_txt = str(int(bnb_before_wei)) if bnb_before_wei is not None else None
        tok_txt = str(int(tok_before_raw)) if tok_before_raw is not None else None
        dec_int = int(token_dec) if token_dec is not None else None

        self.conn.execute(
            """
            INSERT OR REPLACE INTO trades
                (ts, symbol, token, side, status, tx_hash, note, bnb_before_wei, tok_before_raw, token_dec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(time.time()), symbol, token.lower(), side.upper(), "SENT", tx_hash, note, bnb_txt, tok_txt, dec_int),
        )
        self.conn.commit()

    def mark_mined(
        self,
        tx_hash: str,
        ok: bool,
        delta_bnb: Optional[float] = None,
        delta_token: Optional[float] = None,
        realized_pnl_bnb: Optional[float] = None,
        note: str = "",
    ) -> None:
        status = "MINED_OK" if ok else "MINED_FAIL"
        self.conn.execute(
            """
            UPDATE trades
            SET status=?,
                delta_bnb=COALESCE(?, delta_bnb),
                delta_token=COALESCE(?, delta_token),
                realized_pnl_bnb=COALESCE(?, realized_pnl_bnb),
                note=CASE WHEN ? != '' THEN ? ELSE note END
            WHERE tx_hash=?
            """,
            (status, delta_bnb, delta_token, realized_pnl_bnb, note, note, tx_hash),
        )
        self.conn.commit()

    def mark_status_only(self, tx_hash: str, status: str, note: str = "") -> None:
        self.conn.execute(
            """
            UPDATE trades
            SET status=?,
                note=CASE WHEN ? != '' THEN ? ELSE note END
            WHERE tx_hash=?
            """,
            (status, note, note, tx_hash),
        )
        self.conn.commit()

    def list_mined_trades(self):
        """
        按时间顺序返回已上链交易及其已计算的 delta。
        用于重启后重建仓位和成本基础。
        """
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            SELECT symbol, token, side, delta_bnb, delta_token
            FROM trades
            WHERE status IN ('MINED_OK', 'MINED')
              AND (delta_bnb IS NOT NULL OR delta_token IS NOT NULL)
            ORDER BY id ASC
            """
        ).fetchall()

        # 以简单对象/dict 返回，保持最小化
        out = []
        for r in rows:
            out.append({
                "symbol": r[0],
                "token": r[1],
                "side": r[2],
                "delta_bnb": r[3],
                "delta_token": r[4],
            })
        return out

    def list_sent_trades(self) -> list["TradeDB.SentTrade"]:
        cur = self.conn.execute(
            "SELECT tx_hash, symbol, token, side FROM trades WHERE status='SENT' ORDER BY ts ASC"
        )
        out: list[TradeDB.SentTrade] = []
        for tx_hash, symbol, token, side in cur.fetchall():
            out.append(
                TradeDB.SentTrade(
                    tx_hash=str(tx_hash),
                    symbol=str(symbol),
                    token=str(token),
                    side=str(side),
                )
            )
        return out

    def get_trade(self, tx_hash: str) -> Optional["TradeDB.TradeRow"]:
        cur = self.conn.execute(
            """
            SELECT tx_hash, ts, symbol, token, side, status, note, bnb_before_wei, tok_before_raw, token_dec
            FROM trades
            WHERE tx_hash=?
            """,
            (tx_hash,),
        )
        row = cur.fetchone()
        if not row:
            return None

        bnb_before = int(row[7]) if row[7] is not None and str(row[7]).strip() != "" else None
        tok_before = int(row[8]) if row[8] is not None and str(row[8]).strip() != "" else None
        tok_dec = int(row[9]) if row[9] is not None else None

        return TradeDB.TradeRow(
            tx_hash=str(row[0]),
            ts=int(row[1] or 0),
            symbol=str(row[2] or ""),
            token=str(row[3] or ""),
            side=str(row[4] or ""),
            status=str(row[5] or ""),
            note=str(row[6] or ""),
            bnb_before_wei=bnb_before,
            tok_before_raw=tok_before,
            token_dec=tok_dec,
        )

    # -----------------------
    # positions
    # -----------------------

    def upsert_position(self, symbol: str, token: str, qty_token: float, cost_bnb: float) -> None:
        self.conn.execute(
            """
            INSERT INTO positions(symbol, token, qty_token, cost_bnb)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                token=excluded.token,
                qty_token=excluded.qty_token,
                cost_bnb=excluded.cost_bnb
            """,
            (symbol, token.lower(), float(qty_token), float(cost_bnb)),
        )
        self.conn.commit()

    def get_position(self, symbol: str) -> Optional["TradeDB.Position"]:
        cur = self.conn.execute(
            "SELECT symbol, token, qty_token, cost_bnb FROM positions WHERE symbol=?",
            (symbol,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return TradeDB.Position(
            symbol=str(row[0]),
            token=str(row[1]),
            qty_token=float(row[2] or 0.0),
            cost_bnb=float(row[3] or 0.0),
        )


    # -----------------------
    # FIFO lots（每次 BUY 对应一个 lot）
    # -----------------------

    def reset_lots(self) -> None:
        """
        硬重置 lots 和 lot_fills。
        这必须是原子操作，因为 rebuild_positions_from_trades() 会重新插入 fills。
        如果不清空 lot_fills，每次重启都会重复 fills，并可能破坏 open lots。
        """
        with self.conn:
            # 1) 先清空 fills（它们引用 lot ids）
            self.conn.execute("DELETE FROM lot_fills")

            # 2) 清空 lots
            self.conn.execute("DELETE FROM lots")

            # 3) 重置自增计数器，避免 id 永久增长，
            #    同时避免重建时意外发生 lot_id 冲突。
            #    sqlite_sequence 只在表使用 AUTOINCREMENT 创建时存在；
            #    尝试删除是安全的，忽略错误即可。
            try:
                self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('lots','lot_fills')")
            except Exception:
                pass

    def create_lot(
        self,
        symbol: str,
        token: str,
        buy_tx: str,
        qty: float,
        cost_bnb: float,
        ts: Optional[int] = None,
    ) -> None:
        ts_i = int(ts or time.time())
        self.conn.execute(
            """
            INSERT INTO lots (ts, symbol, token, buy_tx, qty_init, cost_init_bnb, qty_open, cost_open_bnb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts_i, symbol, token.lower(), str(buy_tx), float(qty), float(cost_bnb), float(qty), float(cost_bnb)),
        )
        self.conn.commit()

    def list_open_lots(self, symbol: str) -> List[dict]:
        cur = self.conn.execute(
            """
            SELECT id, ts, symbol, token, buy_tx, qty_open, cost_open_bnb, qty_init, cost_init_bnb
            FROM lots
            WHERE symbol=? AND qty_open > ?
            ORDER BY id ASC
            """,
            (symbol, float(self.get_dust(symbol))),
        )
        out: List[dict] = []
        for r in cur.fetchall():
            out.append(
                {
                    "id": int(r[0]),
                    "ts": int(r[1] or 0),
                    "symbol": str(r[2] or ""),
                    "token": str(r[3] or ""),
                    "buy_tx": str(r[4] or ""),
                    "qty_open": float(r[5] or 0.0),
                    "cost_open_bnb": float(r[6] or 0.0),
                    "qty_init": float(r[7] or 0.0),
                    "cost_init_bnb": float(r[8] or 0.0),
                }
            )
        return out

    def consume_lots_fifo(
        self,
        symbol: str,
        token: str,
        sell_tx: str,
        qty_to_sell: float,
        proceeds_total_bnb: float,
        ts: Optional[int] = None,
    ) -> Tuple[float, float]:
        """
        按 FIFO 消耗 open lots。
        返回：(realized_pnl_bnb, cost_sold_bnb)

        如果 qty_to_sell 超过可用 lots，剩余部分会被视为 “免费代币”（成本=0）。
        对这部分剩余数量，我们分配卖出收入但成本为 0（否则百分比会变成无限大），
        因此当存在剩余部分时，决策逻辑应回退到整体 PnL。
        """
        ts_i = int(ts or time.time())
        qty_to_sell = float(max(0.0, qty_to_sell))
        proceeds_total_bnb = float(max(0.0, proceeds_total_bnb))

        if qty_to_sell <= 0:
            return 0.0, 0.0

        # 按卖出的代币数量比例拆分收入（简单且一致）
        price = proceeds_total_bnb / qty_to_sell if qty_to_sell > 0 else 0.0

        lots = self.list_open_lots(symbol)
        remaining = qty_to_sell

        total_cost = 0.0
        total_proceeds = 0.0

        for lot in lots:
            if remaining <= 0:
                break

            lot_qty = float(lot["qty_open"])
            lot_cost = float(lot["cost_open_bnb"])

            if lot_qty <= 0:
                continue

            take = min(lot_qty, remaining)

            # 该 lot 中被卖出部分的成本基础
            # 按 lot 剩余 open 成本/数量比例计算
            lot_avg = (lot_cost / lot_qty) if lot_qty > 0 else 0.0
            cost_part = lot_avg * take
            proceeds_part = price * take
            pnl_part = proceeds_part - cost_part

            # 更新 lot 剩余量
            new_qty = lot_qty - take
            new_cost = max(0.0, lot_cost - cost_part)

            # 将浮点 dust 压为 0，确保 lots 真正关闭。
            if new_qty < self._LOT_EPS_QTY:
                new_qty = 0.0
                new_cost = 0.0

            self.conn.execute(
                "UPDATE lots SET qty_open=?, cost_open_bnb=? WHERE id=?",
                (float(new_qty), float(new_cost), int(lot["id"])),
            )

            # 记录 fill
            self.conn.execute(
                """
                INSERT INTO lot_fills (ts, symbol, token, lot_id, sell_tx, qty_sold, proceeds_bnb, cost_bnb, pnl_bnb)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_i,
                    symbol,
                    token.lower(),
                    int(lot["id"]),
                    str(sell_tx),
                    float(take),
                    float(proceeds_part),
                    float(cost_part),
                    float(pnl_part),
                ),
            )

            total_cost += cost_part
            total_proceeds += proceeds_part
            remaining -= take

        # 剩余部分是 “免费代币”（成本=0）
        if remaining > 0:
            free_proceeds = price * remaining
            total_proceeds += free_proceeds
            # 不增加成本

        self.conn.commit()

        realized = total_proceeds - total_cost
        return float(realized), float(total_cost)

    def consume_lot_by_id(
            self,
            *,
            lot_id: int,
            qty_to_sell: float,
            proceeds_total_bnb: float,
            sell_tx: str | None = None,
            ts: Optional[int] = None,
    ) -> tuple[float, float]:
        """
        根据 id 消耗指定 lot（不是 FIFO）。
        返回 (realized_pnl_bnb, cost_sold_bnb)。

        重要：
        - 使用 self.conn（同一个数据库连接）。
        - 更新正确的列（qty_open, cost_open_bnb）。
        - 插入 lot_fills，确保 Dashboard 和重建逻辑保持一致。
        """

        if qty_to_sell <= 0 or proceeds_total_bnb < 0:
            return 0.0, 0.0

        ts_i = int(ts or time.time())
        sell_tx_s = str(sell_tx or "")

        cur = self.conn.execute(
            "SELECT id, symbol, token, qty_open, cost_open_bnb FROM lots WHERE id=?",
            (int(lot_id),),
        )
        row = cur.fetchone()
        if not row:
            return 0.0, 0.0

        lot_symbol = str(row[1] or "")
        lot_token = str(row[2] or "")
        qty_open = float(row[3] or 0.0)
        cost_open = float(row[4] or 0.0)

        if qty_open <= self._LOT_EPS_QTY:
            return 0.0, 0.0

        qty = min(float(qty_to_sell), qty_open)
        if qty <= 0:
            return 0.0, 0.0

        # 卖出部分的比例成本
        cost_sold = 0.0
        if cost_open > 0:
            cost_sold = cost_open * (qty / qty_open)

        realized = float(proceeds_total_bnb) - float(cost_sold)

        # 更新剩余量（压掉 dust）
        new_qty = qty_open - qty
        new_cost = max(0.0, cost_open - cost_sold)
        if new_qty < self._LOT_EPS_QTY:
            new_qty = 0.0
            new_cost = 0.0

        self.conn.execute(
            "UPDATE lots SET qty_open=?, cost_open_bnb=? WHERE id=?",
            (float(new_qty), float(new_cost), int(lot_id)),
        )

        # 记录 fill
        pnl_part = float(proceeds_total_bnb) - float(cost_sold)
        self.conn.execute(
            """
            INSERT INTO lot_fills (ts, symbol, token, lot_id, sell_tx, qty_sold, proceeds_bnb, cost_bnb, pnl_bnb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_i,
                lot_symbol,
                lot_token.lower(),
                int(lot_id),
                sell_tx_s,
                float(qty),
                float(proceeds_total_bnb),
                float(cost_sold),
                float(pnl_part),
            ),
        )

        self.conn.commit()
        return float(realized), float(cost_sold)

    def estimate_fifo_profit_pct(
        self,
        symbol: str,
        qty_to_sell: float,
        price_wbnb_per_token: float,
        fallback_profit_pct: float,
    ) -> Optional[float]:
        """
        使用 FIFO lot 成本，估算现在卖出 qty_to_sell 的利润百分比。

        如果 qty_to_sell 超过可用 lots（存在免费剩余部分），返回 fallback_profit_pct，
        以避免无限大或被夸大的 pnl%。
        """
        qty_to_sell = float(max(0.0, qty_to_sell))
        price_wbnb_per_token = float(max(0.0, price_wbnb_per_token))

        if qty_to_sell <= 0 or price_wbnb_per_token <= 0:
            return None

        lots = self.list_open_lots(symbol)
        avail = sum(float(l["qty_open"]) for l in lots)

        dust = self.get_dust(symbol)
        eps = max(1e-12, dust)
        if qty_to_sell > avail + eps:
            # 包含免费剩余部分 -> 使用整体 pnl
            return float(fallback_profit_pct)

        # 计算 qty_to_sell 的成本
        remaining = qty_to_sell
        cost = 0.0
        for l in lots:
            if remaining <= 0:
                break
            lot_qty = float(l["qty_open"])
            lot_cost = float(l["cost_open_bnb"])
            take = min(lot_qty, remaining)
            lot_avg = (lot_cost / lot_qty) if lot_qty > 0 else 0.0
            cost += lot_avg * take
            remaining -= take

        proceeds = qty_to_sell * price_wbnb_per_token
        if cost <= 0:
            return float(fallback_profit_pct)

        pct = ((proceeds / cost) - 1.0) * 100.0
        return float(pct)

