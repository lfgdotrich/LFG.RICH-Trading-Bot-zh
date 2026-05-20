from __future__ import annotations

import sqlite3
import pandas as pd
import streamlit as st
import yaml
import os
import json
import time
import urllib.request

try:
    from dotenv import load_dotenv
except Exception:  # dashboard should still load without dotenv
    load_dotenv = None

try:
    from web3 import Web3
except Exception:  # dashboard should still load without web3
    Web3 = None

DB_PATH = "state.db"

def load_dust_map(config_path: str = "config.yaml") -> dict[str, float]:
    """
    从 config.yaml 读取 watchlist.tokens[].dust_size。
    返回 {symbol: dust_size}。缺少 dust_size 时使用 0.0
    """
    if not os.path.exists(config_path):
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    wl = data.get("watchlist", {}) or {}
    tokens = wl.get("tokens", []) or []

    out: dict[str, float] = {}
    for t in tokens:
        try:
            sym = str(t.get("symbol", "")).strip()
            if not sym:
                continue
            out[sym] = float(t.get("dust_size", 0.0))
        except Exception:
            # 忽略格式错误的配置项
            continue
    return out


DUST_BY_SYMBOL = load_dust_map("config.yaml")
DEFAULT_DUST = 0.0

_BNB_USD_CACHE = {"ts": 0, "price": 0.0}

def _http_get_json(url: str, timeout_sec: int = 5) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_bnb_usd_from_binance(timeout_sec: int = 5) -> float:
    data = _http_get_json("https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT", timeout_sec)
    return float(data["price"])

def fetch_bnb_usd_from_coingecko(timeout_sec: int = 5) -> float:
    data = _http_get_json(
        "https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd",
        timeout_sec,
    )
    return float(data["binancecoin"]["usd"])

def get_bnb_usd_price_cached(ttl_sec: int = 60) -> float:
    """
    返回缓存的 BNB/USD 价格。最多每 ttl_sec 秒刷新一次。
    优先尝试 Binance，失败时回退到 CoinGecko。
    遇到限流或错误时，返回上一次缓存值（如果从未成功过，可能为 0）。
    """
    now = int(time.time())
    if _BNB_USD_CACHE["price"] > 0 and now - int(_BNB_USD_CACHE["ts"]) < ttl_sec:
        return float(_BNB_USD_CACHE["price"])

    last = float(_BNB_USD_CACHE["price"] or 0.0)

    try:
        px = fetch_bnb_usd_from_binance()
        if px > 0:
            _BNB_USD_CACHE.update({"ts": now, "price": float(px)})
            return float(px)
    except Exception:
        pass

    try:
        px = fetch_bnb_usd_from_coingecko()
        if px > 0:
            _BNB_USD_CACHE.update({"ts": now, "price": float(px)})
            return float(px)
    except Exception:
        pass

    return last

def kv_get_str(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        df = pd.read_sql_query("SELECT v FROM kv WHERE k = ? LIMIT 1", conn, params=(key,))
        if df is not None and len(df):
            v = df.iloc[0]["v"]
            return None if v is None else str(v)
    except Exception:
        pass
    return None


def fmt_usd_from_bnb(pnl_bnb: float, bnb_price_usd: float) -> str:
    if bnb_price_usd <= 0:
        return "USD:（价格不可用）"
    usd = pnl_bnb * bnb_price_usd
    # 避免显示 "-0.00"
    if abs(usd) < 0.005:
        usd = 0.0
    return f"${usd:,.2f}"

def load_watch_tokens(config_path: str = "config.yaml") -> list[dict]:
    """返回 config.yaml 中 watchlist.tokens 的代币配置。"""
    if not os.path.exists(config_path):
        return []
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    wl = data.get("watchlist", {}) or {}
    return wl.get("tokens", []) or []

def load_blocks_per_candle(config_path: str = "config.yaml", default: int = 20) -> int:
    """从 config.yaml 读取 bot.blocks_per_candle。"""
    if not os.path.exists(config_path):
        return int(default)
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    bot = data.get("bot", {}) or {}
    return int(bot.get("blocks_per_candle", default) or default)

def kv_get_json(conn: sqlite3.Connection, key: str):
    """
    Read JSON stored by bot.db_state.KV.
    Your schema is: kv(k TEXT PRIMARY KEY, v TEXT NOT NULL)
    """
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    if not row or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None

def load_last_close_by_symbol(conn: sqlite3.Connection, config_path: str = "config.yaml") -> dict[str, float]:
    """
    Candle key format used by your bot:
      candles:{token_addr_lower}:bpc{blocks_per_candle}
    Returns {symbol: last_close_wbnb_per_token}
    """
    bpc = load_blocks_per_candle(config_path=config_path, default=20)
    out: dict[str, float] = {}

    for t in load_watch_tokens(config_path):
        sym = str(t.get("symbol", "")).strip()
        addr = str(t.get("address", "")).strip().lower()
        if not sym or not addr:
            continue

        key_new = f"candles:{addr}:bpc{int(bpc)}"
        candles = kv_get_json(conn, key_new)

        # 向后兼容：旧数据库使用不带 bpc 后缀的 candles:{addr}
        if not candles:
            key_old = f"candles:{addr}"
            candles = kv_get_json(conn, key_old)

        if not candles:
            continue

        try:
            last = candles[-1]
            close = float(last.get("close", 0.0) or 0.0)
            if close > 0:
                out[sym] = close
        except Exception:
            continue

    return out


LFG_HOOK_ABI = [
    {
        "type": "function",
        "name": "tokenToPoolId",
        "stateMutability": "view",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "getEffectivePrice",
        "stateMutability": "view",
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

_LFG_PRICE_CACHE = {"ts": 0, "prices": {}, "errors": []}


def _hex0x(value) -> str:
    """Return a 0x-prefixed hex string for bytes32 values returned by web3.py."""
    if isinstance(value, str):
        v = value.strip()
    elif hasattr(value, "hex"):
        v = value.hex()
    else:
        v = str(value).strip()
    if not v.startswith("0x"):
        v = "0x" + v
    return v.lower()


def load_config_raw(config_path: str = "config.yaml") -> dict:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_rpc_url_from_env() -> str:
    """Dashboard 使用与机器人相同的 BSC_RPC_URL。"""
    if load_dotenv is not None:
        try:
            load_dotenv()
        except Exception:
            pass
    return os.environ.get("BSC_RPC_URL", "").strip()


def load_lfg_effective_price_by_symbol(config_path: str = "config.yaml", ttl_sec: int = 30) -> dict[str, float]:
    """
    从 LFG Hook 返回 {symbol: 当前 BNB/token 价格}。

    某些代币可能没有足够的近期 K 线历史。此时 Dashboard 不能把 lots 价格当作 0。
    对 LFG 代币来说，协议的 getEffectivePrice(poolId) 是正确的备用当前价格。
    """
    now = int(time.time())
    if _LFG_PRICE_CACHE["prices"] and now - int(_LFG_PRICE_CACHE["ts"]) < ttl_sec:
        return dict(_LFG_PRICE_CACHE["prices"])

    prices: dict[str, float] = {}
    errors: list[str] = []

    if Web3 is None:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": ["未安装 web3"]})
        return prices

    rpc_url = get_rpc_url_from_env()
    if not rpc_url:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": [".env 中缺少 BSC_RPC_URL"]})
        return prices

    raw = load_config_raw(config_path)
    hook_addr = str(((raw.get("lfg") or {}).get("hook") or "")).strip()
    if not hook_addr:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": ["config.yaml 中缺少 lfg.hook"]})
        return prices

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        hook = w3.eth.contract(address=Web3.to_checksum_address(hook_addr), abi=LFG_HOOK_ABI)
    except Exception as e:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": [f"LFG 价格初始化失败: {e}"]})
        return prices

    for t in (raw.get("watchlist", {}) or {}).get("tokens", []) or []:
        sym = str(t.get("symbol", "")).strip()
        addr = str(t.get("address", "")).strip()
        dex = str(t.get("dex", "lfg")).lower().strip()
        if not sym or not addr or dex != "lfg":
            continue
        try:
            pool_id = _hex0x(hook.functions.tokenToPoolId(Web3.to_checksum_address(addr)).call())
            raw_price = int(hook.functions.getEffectivePrice(pool_id).call())
            price = raw_price / 1e18 if raw_price > 0 else 0.0
            if price > 0:
                prices[sym] = float(price)
        except Exception as e:
            errors.append(f"{sym}: {e}")
            continue

    _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": errors})
    return prices


def load_price_by_symbol(conn: sqlite3.Connection, config_path: str = "config.yaml") -> dict[str, float]:
    """优先使用 K 线收盘价，然后使用 LFG Hook 有效价格作为备用。"""
    prices = {}
    try:
        prices.update(load_last_close_by_symbol(conn, config_path=config_path))
    except Exception:
        pass

    try:
        lfg_prices = load_lfg_effective_price_by_symbol(config_path=config_path)
        for sym, px in lfg_prices.items():
            # 如果存在真实 K 线收盘价则优先使用；缺失或为 0 时使用 Hook 价格填充。
            if float(prices.get(sym, 0.0) or 0.0) <= 0 and float(px) > 0:
                prices[sym] = float(px)
    except Exception:
        pass

    return prices


def compute_open_lots_pnl_bnb(conn: sqlite3.Connection, config_path: str = "config.yaml") -> float:
    """汇总 open FIFO lots 的当前（未实现）PnL，价格来自最新 K 线或 LFG Hook 备用价格。"""
    try:
        lots = pd.read_sql_query(
            """
            SELECT symbol, qty_open, cost_open_bnb
            FROM lots
            WHERE qty_open > 0
            """,
            conn,
        )
    except Exception:
        return 0.0

    if lots is None or not len(lots):
        return 0.0

    # 应用每个 symbol 的 dust 过滤器（忽略极小剩余量）
    try:
        lots = lots[
            lots.apply(
                lambda r: float(r["qty_open"]) > float(DUST_BY_SYMBOL.get(str(r["symbol"]), DEFAULT_DUST)),
                axis=1,
            )
        ]
    except Exception:
        pass

    if not len(lots):
        return 0.0

    lots["qty_open"] = pd.to_numeric(lots["qty_open"], errors="coerce").fillna(0.0)
    lots["cost_open_bnb"] = pd.to_numeric(lots["cost_open_bnb"], errors="coerce").fillna(0.0)

    try:
        price_by_symbol = load_price_by_symbol(conn, config_path=config_path)
    except Exception:
        price_by_symbol = {}

    lots["last_close_wbnb_per_token"] = lots["symbol"].apply(
        lambda s: float(price_by_symbol.get(str(s), 0.0) or 0.0)
    )

    lots["value_open_bnb"] = lots.apply(
        lambda r: float(r["qty_open"]) * float(r["last_close_wbnb_per_token"])
        if float(r["qty_open"]) > 0 and float(r["last_close_wbnb_per_token"]) > 0
        else 0.0,
        axis=1,
    )

    lots["lot_pnl_bnb"] = lots.apply(
        lambda r: float(r["value_open_bnb"]) - float(r["cost_open_bnb"])
        if float(r["cost_open_bnb"]) > 0 and float(r["value_open_bnb"]) > 0
        else 0.0,
        axis=1,
    )

    return float(lots["lot_pnl_bnb"].fillna(0.0).sum())

def get_bnb_price_usd(conn: sqlite3.Connection) -> float:
    """
    Try to read BNB price in USD from candles table.
    Looks for common symbol names and returns latest close.
    """
    candidates = [
        "WBNB/USDT",
        "BNB/USDT",
        "WBNB_USDT",
        "BNB_USDT",
        "WBNBUSDT",
        "BNBUSDT",
        "WBNB",   # sometimes dashboards store the quote separately (unlikely)
        "BNB",
    ]

    for sym in candidates:
        try:
            df = pd.read_sql_query(
                """
                SELECT close
                FROM candles
                WHERE symbol = ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                conn,
                params=(sym,),
            )
            if df is not None and len(df) and pd.notna(df.iloc[0]["close"]):
                px = float(df.iloc[0]["close"])
                if px > 0:
                    return px
        except Exception:
            continue

    return 0.0

def fmt_usd(x: float) -> str:
    # 避免显示 "-0.00" caused by floating point tiny negatives
    if abs(x) < 0.005:
        x = 0.0
    return f"${x:,.2f}"


st.set_page_config(page_title="LFG.RICH 交易机器人 PnL", layout="wide")
st.title("LFG.RICH 交易机器人 – 交易与 PnL")

conn = sqlite3.connect(DB_PATH)

# 交易
trades = pd.read_sql_query(
    """
    SELECT
        ts, symbol, side, status, tx_hash,
        delta_bnb, delta_token, realized_pnl_bnb, note
    FROM trades
    ORDER BY id DESC
    """,
    conn
)

# 将 Unix 时间戳转换为可读时间
if "ts" in trades.columns:
    trades["ts"] = pd.to_datetime(trades["ts"], unit="s", errors="coerce")

# 确保 tx_hash 始终带有 0x 前缀
if "tx_hash" in trades.columns:
    trades["tx_hash"] = trades["tx_hash"].astype(str)
    trades["tx_hash"] = trades["tx_hash"].apply(
        lambda x: x if x.startswith("0x") else f"0x{x}" if x != "None" else x
    )

# 仓位
pos = pd.read_sql_query(
    "SELECT symbol, token, qty_token, cost_bnb FROM positions ORDER BY symbol",
    conn
)

st.subheader("概览")

if len(trades) and "realized_pnl_bnb" in trades.columns:
    # 修复 pandas 未来警告：强制转换为数值并安全处理 NaN
    trades["realized_pnl_bnb"] = pd.to_numeric(trades["realized_pnl_bnb"], errors="coerce")
    realized = trades["realized_pnl_bnb"].fillna(0.0).sum()
else:
    realized = 0.0

bnb_price_usd = get_bnb_usd_price_cached(ttl_sec=60)

# realized_usd = float(realized) * bnb_price_usd
realized_bnb = float(realized)
realized_usd = realized_bnb * bnb_price_usd
open_lots_pnl = compute_open_lots_pnl_bnb(conn, config_path="config.yaml")
total_unrealized = float(realized_bnb) + float(open_lots_pnl)
total_unrealized_usd = float(total_unrealized) * bnb_price_usd

st.caption(
    f"使用的 BNB 价格: ${bnb_price_usd:,.2f}" if bnb_price_usd > 0 else "使用的 BNB 价格:（不可用）"
)

# 每次页面渲染时预热一次 LFG 价格缓存。如果失败，Dashboard 仍会加载，
# 并只回退到 K 线收盘价。
try:
    load_lfg_effective_price_by_symbol(config_path="config.yaml")
except Exception:
    pass


def fmt_inline(bnb: float, usd: float) -> str:
    if abs(usd) < 0.005:
        usd = 0.0

    usd_txt = f"${usd:,.2f}"

    # 只给 USD 部分着色
    if usd > 0:
        usd_html = f"<span style='color:#00a000'>{usd_txt}</span>"
    elif usd < 0:
        usd_html = f"<span style='color:#d00000'>{usd_txt}</span>"
    else:
        usd_html = f"<span style='color:#808080'>{usd_txt}</span>"

    return f"{bnb:.6f} BNB &nbsp;&nbsp;≈ {usd_html}"

def render_pnl_box(title: str, pnl_bnb: float, bnb_price_usd: float) -> None:
    pnl_usd = float(pnl_bnb) * float(bnb_price_usd)

    # 避免显示 "-0.00"
    if abs(pnl_usd) < 0.005:
        pnl_usd = 0.0

    usd_color = "#00a000" if pnl_usd > 0 else "#d00000" if pnl_usd < 0 else "#808080"
    usd_txt = f"${pnl_usd:,.2f}"

    st.markdown(
        f"""
        <div class="pnl-box">
          <div class="pnl-title">{title}</div>
          <div class="pnl-value">
            {pnl_bnb:.6f} <span class="pnl-unit">BNB</span>
            <span class="pnl-sep">≈</span>
            <span style="color:{usd_color}">{usd_txt}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# 只添加一次（在页面顶部附近，或调用 render_pnl_box 之前）
st.markdown(
    """
    <style>
      .pnl-box {
        padding: 0.6rem 0.8rem;
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 0.6rem;
        margin-bottom: 0.6rem;
      }
      .pnl-title {
        font-size: 0.9rem;
        color: rgba(49, 51, 63, 0.75);
        margin-bottom: 0.1rem;
      }
      .pnl-value {
        font-size: 1.6rem;
        font-weight: 650;
        line-height: 1.2;
      }
      .pnl-unit {
        font-size: 1.1rem;
        font-weight: 500;
        color: rgba(49, 51, 63, 0.6);
      }
      .pnl-sep {
        font-size: 1.0rem;
        color: rgba(49, 51, 63, 0.6);
        padding: 0 0.35rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


render_pnl_box("总已实现 PnL (BNB)", float(realized), float(bnb_price_usd))
render_pnl_box("总未实现 PnL (BNB)", float(total_unrealized), float(bnb_price_usd))

st.subheader("开放仓位（成本基础）")
if len(pos):
    pos["qty_token"] = pd.to_numeric(pos["qty_token"], errors="coerce").fillna(0.0)
    pos["cost_bnb"] = pd.to_numeric(pos["cost_bnb"], errors="coerce").fillna(0.0)
    pos["avg_cost_bnb_per_token"] = pos.apply(
        lambda r: (r["cost_bnb"] / r["qty_token"]) if r["qty_token"] > 0 else 0.0,
        axis=1,
    )
    pos_display = pos.rename(columns={
        "symbol": "代币",
        "token": "合约地址",
        "qty_token": "代币数量",
        "cost_bnb": "成本 BNB",
        "avg_cost_bnb_per_token": "平均成本 BNB/token",
    })
    st.dataframe(pos_display, use_container_width=True)
else:
    st.write("暂无开放仓位。")

# -----------------------------
# FIFO Lots + Lot Fills（新增）
# -----------------------------

st.subheader("开放 Lots（每次 BUY 对应一个 FIFO lot）")

try:
    lots = pd.read_sql_query(
        """
        SELECT
            id, ts, symbol, token, buy_tx,
            qty_open, cost_open_bnb,
            qty_init, cost_init_bnb
        FROM lots
        WHERE qty_open > 0
        ORDER BY id ASC
        """,
        conn,
    )

    if len(lots):
        # 应用每个 symbol 的 dust 过滤器
        lots = lots[
            lots.apply(
                lambda r: float(r["qty_open"]) > float(DUST_BY_SYMBOL.get(str(r["symbol"]), DEFAULT_DUST)),
                axis=1,
            )
        ]

    if len(lots):
        # 将 Unix 时间戳转换为日期时间
        if "ts" in lots.columns:
            lots["ts"] = pd.to_datetime(lots["ts"], unit="s", errors="coerce")

        # 数值安全处理
        lots["qty_open"] = pd.to_numeric(lots["qty_open"], errors="coerce").fillna(0.0)
        lots["cost_open_bnb"] = pd.to_numeric(lots["cost_open_bnb"], errors="coerce").fillna(0.0)

        lots["avg_entry_wbnb_per_token"] = lots.apply(
            lambda r: (r["cost_open_bnb"] / r["qty_open"]) if r["qty_open"] > 0 else 0.0,
            axis=1,
        )

        # --- 每个 lot 的当前 PnL ---
        # 优先使用最新缓存 K 线收盘价。如果 LFG 事件/K 线不足，
        # 回退到 Hook.getEffectivePrice(poolId)，避免 open lots 显示 None PnL。
        try:
            price_by_symbol = load_price_by_symbol(conn, config_path="config.yaml")
        except Exception:
            price_by_symbol = {}

        lots["last_close_wbnb_per_token"] = lots["symbol"].apply(
            lambda s: float(price_by_symbol.get(str(s), 0.0) or 0.0)
        )

        lots["value_open_bnb"] = lots.apply(
            lambda r: float(r["qty_open"]) * float(r["last_close_wbnb_per_token"])
            if float(r["qty_open"]) > 0 and float(r["last_close_wbnb_per_token"]) > 0
            else 0.0,
            axis=1,
        )

        lots["lot_pnl_pct"] = lots.apply(
            lambda r: ((float(r["value_open_bnb"]) / float(r["cost_open_bnb"]) - 1.0) * 100.0)
            if float(r["cost_open_bnb"]) > 0 and float(r["value_open_bnb"]) > 0
            else None,
            axis=1,
        )


        # 可选：把最有用的列放在前面
        lots_display = lots[
            [
                "id",
                "ts",
                "symbol",
                "buy_tx",
                "qty_open",
                "cost_open_bnb",
                "avg_entry_wbnb_per_token",
                "last_close_wbnb_per_token",
                "value_open_bnb",
                "lot_pnl_pct",
                "qty_init",
                "cost_init_bnb",
            ]
        ]

        lots_display = lots_display.rename(columns={
            "id": "ID",
            "ts": "时间",
            "symbol": "代币",
            "buy_tx": "买入交易",
            "qty_open": "剩余数量",
            "cost_open_bnb": "剩余成本 BNB",
            "avg_entry_wbnb_per_token": "平均入场 BNB/token",
            "last_close_wbnb_per_token": "当前价格 BNB/token",
            "value_open_bnb": "当前价值 BNB",
            "lot_pnl_pct": "Lot PnL %",
            "qty_init": "初始数量",
            "cost_init_bnb": "初始成本 BNB",
        })
        st.dataframe(lots_display, use_container_width=True)
    else:
        st.write("暂无开放 lots（还没有记录 BUY lot，或全部已经卖出）。")

except Exception as e:
    st.write("尚未找到 lots 表（运行机器人并创建 FIFO lots 后会出现）。")
    st.caption(str(e))


st.subheader("Lot Fills（SELL 拆分明细）")

try:
    fills = pd.read_sql_query(
        """
        SELECT
            f.id,
            f.ts,
            f.symbol,
            f.sell_tx,
            f.lot_id,
            l.buy_tx AS buy_tx,
            f.qty_sold,
            f.proceeds_bnb,
            f.cost_bnb,
            f.pnl_bnb
        FROM lot_fills f
        LEFT JOIN lots l ON l.id = f.lot_id
        ORDER BY f.id DESC
        LIMIT 500
        """,
        conn,
    )

    if len(fills):
        # 将 Unix 时间戳转换为日期时间
        if "ts" in fills.columns:
            fills["ts"] = pd.to_datetime(fills["ts"], unit="s", errors="coerce")

        # 数值安全处理
        for col in ["qty_sold", "proceeds_bnb", "cost_bnb", "pnl_bnb"]:
            fills[col] = pd.to_numeric(fills[col], errors="coerce").fillna(0.0)

        fills["pnl_pct"] = fills.apply(
            lambda r: ((r["pnl_bnb"] / r["cost_bnb"]) * 100.0) if r["cost_bnb"] > 0 else 0.0,
            axis=1,
        )

        fills_display = fills[
            [
                "id",
                "ts",
                "symbol",
                "sell_tx",
                "lot_id",
                "buy_tx",
                "qty_sold",
                "proceeds_bnb",
                "cost_bnb",
                "pnl_bnb",
                "pnl_pct",
            ]
        ]

        fills_display = fills_display.rename(columns={
            "id": "ID",
            "ts": "时间",
            "symbol": "代币",
            "sell_tx": "卖出交易",
            "lot_id": "Lot ID",
            "buy_tx": "买入交易",
            "qty_sold": "卖出数量",
            "proceeds_bnb": "卖出收入 BNB",
            "cost_bnb": "成本 BNB",
            "pnl_bnb": "PnL BNB",
            "pnl_pct": "PnL %",
        })
        st.dataframe(fills_display, use_container_width=True)
    else:
        st.write("暂无 lot fills（至少需要一笔已上链 SELL）。")

except Exception as e:
    st.write("尚未找到 lot_fills 表（运行机器人并创建 FIFO lots 后会出现）。")
    st.caption(str(e))


st.subheader("交易")
if len(trades):
    trades_display = trades.rename(columns={
        "ts": "时间",
        "symbol": "代币",
        "side": "方向",
        "status": "状态",
        "tx_hash": "交易哈希",
        "delta_bnb": "BNB 变化",
        "delta_token": "代币变化",
        "realized_pnl_bnb": "已实现 PnL BNB",
        "note": "备注",
    })
    st.dataframe(trades_display, use_container_width=True)
else:
    st.write("暂无交易。")

st.subheader("已实现 PnL 时间走势")
if len(trades) and trades["realized_pnl_bnb"].notna().any():
    pnl = trades.dropna(subset=["realized_pnl_bnb"]).copy()

    # ts 以 Unix 秒存储
    pnl["ts"] = pd.to_datetime(pnl["ts"], unit="s", errors="coerce")
    pnl = pnl.dropna(subset=["ts"]).sort_values("ts")

    pnl["cum_pnl_bnb"] = pnl["realized_pnl_bnb"].cumsum()
    st.line_chart(pnl.set_index("ts")["cum_pnl_bnb"])
else:
    st.write("暂无已实现 PnL（至少需要一笔成功的 SELL）。")
