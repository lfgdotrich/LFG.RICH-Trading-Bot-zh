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
except Exception:  # 即使没有 dotenv，仪表盘也应该继续加载
    load_dotenv = None

try:
    from web3 import Web3
except Exception:  # 即使没有 web3，仪表盘也应该继续加载
    Web3 = None

try:
    from bot.onchain import lfg as lfg_onchain
except Exception:
    lfg_onchain = None

DB_PATH = "state.db"
LFG_V5_PRICE_SCALE = 10 ** 22

LFG_V3_PRICE_SCALE = 10 ** 18


def _price_scale_for_version(version) -> int:
    v = str(version or "").strip().lower()
    return LFG_V3_PRICE_SCALE if v in ("v3", "legacy", "legacy_v3") else LFG_V5_PRICE_SCALE


def load_dust_map(config_path: str = "config.yaml") -> dict[str, float]:
    """
    从 config.yaml 读取 watchlist.tokens[].dust_size。
    返回 {symbol: dust_size}。缺少 dust_size -> 0.0
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
            # 忽略格式错误的条目
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
    返回缓存的 BNB/USD 价格。最多每 ttl_sec 刷新一次。
    先尝试 Binance，失败则回退到 CoinGecko。
    如果限速/出错，返回最后缓存的值（如果从未成功过，可能是 0）。
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
        return "USD：（价格不可用）"
    usd = pnl_bnb * bnb_price_usd
    # 避免显示 "-0.00"
    if abs(usd) < 0.005:
        usd = 0.0
    return f"${usd:,.2f}"

def load_watch_tokens(config_path: str = "config.yaml") -> list[dict]:
    """从 config.yaml 的 watchlist.tokens 返回代币字典。"""
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
    读取 bot.db_state.KV 保存的 JSON。
    schema 为：kv(k TEXT PRIMARY KEY, v TEXT NOT NULL)
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
    机器人使用的 K 线 key 格式：
      candles:{token_addr_lower}:bpc{blocks_per_candle}
    返回 {symbol: last_close_wbnb_per_token}
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
    """为 web3.py 返回的 bytes32 值返回带 0x 前缀的十六进制字符串。"""
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
    """仪表盘使用与机器人相同的 BSC_RPC_URL。"""
    if load_dotenv is not None:
        try:
            load_dotenv()
        except Exception:
            pass
    return os.environ.get("BSC_RPC_URL", "").strip()


def load_lfg_effective_price_by_symbol(config_path: str = "config.yaml", ttl_sec: int = 30) -> dict[str, float]:
    """Return {symbol: current BNB/token price} from each token's on-chain Hook.

    The dashboard uses the same standalone resolver as the bot: token.FACTORY(),
    token.hook(), token.poolId(), then Factory/Hook fallbacks. It does not query
    the website for token prices.
    """
    now = int(time.time())
    if _LFG_PRICE_CACHE["prices"] and now - int(_LFG_PRICE_CACHE["ts"]) < ttl_sec:
        return dict(_LFG_PRICE_CACHE["prices"])

    prices: dict[str, float] = {}
    errors: list[str] = []

    if Web3 is None or lfg_onchain is None:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": ["web3 或 bot.onchain.lfg 未安装/无法导入"]})
        return prices

    rpc_url = get_rpc_url_from_env()
    if not rpc_url:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": ["缺少 .env 中的 BSC_RPC_URL"]})
        return prices

    raw = load_config_raw(config_path)
    lfg_cfg = raw.get("lfg") or {}
    default_factory = str(lfg_cfg.get("factory") or "").strip() or None
    default_hook = str(lfg_cfg.get("hook") or "").strip() or None

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    except Exception as e:
        _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": [f"LFG 价格初始化失败：{e}"]})
        return prices

    for t in (raw.get("watchlist", {}) or {}).get("tokens", []) or []:
        sym = str(t.get("symbol", "")).strip()
        addr = str(t.get("address", "")).strip()
        dex = str(t.get("dex", "lfg")).lower().strip()
        if not sym or not addr or dex != "lfg":
            continue
        try:
            ctx = lfg_onchain.resolve_token_context(
                w3,
                Web3.to_checksum_address(addr),
                default_factory=default_factory,
                default_hook=default_hook,
                configured_pool_id=str(t.get("pool_id") or t.get("poolId") or "").strip(),
            )
            raw_price = lfg_onchain.get_effective_price_raw(w3, ctx.hook, ctx.pool_id)
            price = lfg_onchain.raw_price_to_bnb_per_token(raw_price, ctx.price_scale)
            if price > 0:
                prices[sym] = float(price)
                continue
        except Exception as e:
            errors.append(f"{sym}: {e}")
            continue

    _LFG_PRICE_CACHE.update({"ts": now, "prices": prices, "errors": errors})
    return prices

def load_price_by_symbol(conn: sqlite3.Connection, config_path: str = "config.yaml") -> dict[str, float]:
    """先使用 K 线收盘价，然后使用 LFG Hook 有效价格作为回退。"""
    prices = {}
    try:
        prices.update(load_last_close_by_symbol(conn, config_path=config_path))
    except Exception:
        pass

    try:
        lfg_prices = load_lfg_effective_price_by_symbol(config_path=config_path)
        for sym, px in lfg_prices.items():
            # 如果存在真实 K 线收盘价则优先使用；缺失/为零的价格用 Hook 价格填充。
            if float(prices.get(sym, 0.0) or 0.0) <= 0 and float(px) > 0:
                prices[sym] = float(px)
    except Exception:
        pass

    return prices


def compute_open_lots_pnl_bnb(conn: sqlite3.Connection, config_path: str = "config.yaml") -> float:
    """使用最后缓存的 K 线收盘价，汇总开放 FIFO lots 的当前（未实现）PnL。"""
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

    # 应用每个 symbol 的 dust 过滤（忽略极小剩余量）
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
    尝试从 candles 表读取 BNB 的 USD 价格。
    查找常见 symbol 名称并返回最新收盘价。
    """
    candidates = [
        "WBNB/USDT",
        "BNB/USDT",
        "WBNB_USDT",
        "BNB_USDT",
        "WBNBUSDT",
        "BNBUSDT",
        "WBNB",   # 有时仪表盘会单独存储报价资产（可能性较低）
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
    # 避免浮点极小负数导致显示 "-0.00"
    if abs(x) < 0.005:
        x = 0.0
    return f"${x:,.2f}"


st.set_page_config(page_title="bsc-bot PnL", layout="wide")
st.title("bsc-bot – 交易与 PnL")

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

# 将 Unix 时间戳转换为可读日期时间
if "ts" in trades.columns:
    trades["ts"] = pd.to_datetime(trades["ts"], unit="s", errors="coerce")

# 确保 tx_hash 始终带有 0x 前缀
if "tx_hash" in trades.columns:
    trades["tx_hash"] = trades["tx_hash"].astype(str)
    trades["tx_hash"] = trades["tx_hash"].apply(
        lambda x: x if x.startswith("0x") else f"0x{x}" if x != "None" else x
    )

# 持仓
pos = pd.read_sql_query(
    "SELECT symbol, token, qty_token, cost_bnb FROM positions ORDER BY symbol",
    conn
)

st.subheader("摘要")

if len(trades) and "realized_pnl_bnb" in trades.columns:
    # 修复 pandas future warning：强制转为数值并安全处理 NaN
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
    f"使用的 BNB 价格：${bnb_price_usd:,.2f}" if bnb_price_usd > 0 else "使用的 BNB 价格：（不可用）"
)

# 每次页面渲染时预热一次 LFG 价格缓存。如果失败，仪表盘仍然
# 会加载，并且只回退到 K 线收盘价。
try:
    load_lfg_effective_price_by_symbol(config_path="config.yaml")
except Exception:
    pass


def fmt_inline(bnb: float, usd: float) -> str:
    if abs(usd) < 0.005:
        usd = 0.0

    usd_txt = f"${usd:,.2f}"

    # 只给 USD 着色
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


# 只添加一次（靠近页面顶部，或在使用 render_pnl_box 前）
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

st.subheader("开放持仓（成本基础）")
if len(pos):
    pos["qty_token"] = pd.to_numeric(pos["qty_token"], errors="coerce").fillna(0.0)
    pos["cost_bnb"] = pd.to_numeric(pos["cost_bnb"], errors="coerce").fillna(0.0)
    pos["avg_cost_bnb_per_token"] = pos.apply(
        lambda r: (r["cost_bnb"] / r["qty_token"]) if r["qty_token"] > 0 else 0.0,
        axis=1,
    )
    st.dataframe(pos, use_container_width=True)
else:
    st.write("暂无开放持仓。")

# -----------------------------
# FIFO Lots + Lot Fills（新增）
# -----------------------------

st.subheader("开放 Lots（每笔 BUY 的 FIFO）")

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
        # 应用每个 symbol 的 dust 过滤
        lots = lots[
            lots.apply(
                lambda r: float(r["qty_open"]) > float(DUST_BY_SYMBOL.get(str(r["symbol"]), DEFAULT_DUST)),
                axis=1,
            )
        ]

    if len(lots):
        # 将 Unix ts 转换为日期时间
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
        # 优先使用最后缓存的 K 线收盘价。如果 LFG 事件/K 线还不够，
        # 回退到 Hook.getEffectivePrice(poolId)，避免开放 lots 显示 None PnL。
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

        st.dataframe(lots_display, use_container_width=True)
    else:
        st.write("暂无开放 lots（还没有记录 BUY lots，或已经全部卖出）。")

except Exception as e:
    st.write("尚未找到 lots 表（添加 FIFO lots 后运行机器人）。")
    st.caption(str(e))


st.subheader("Lot Fills（SELL 明细）")

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
        # 将 Unix ts 转换为日期时间
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

        st.dataframe(fills_display, use_container_width=True)
    else:
        st.write("暂无 lot fills（至少需要一笔已上链 SELL）。")

except Exception as e:
    st.write("尚未找到 lot_fills 表（添加 FIFO lots 后运行机器人）。")
    st.caption(str(e))


st.subheader("交易")
if len(trades):
    st.dataframe(trades, use_container_width=True)
else:
    st.write("暂无交易。")

st.subheader("已实现 PnL 随时间变化")
if len(trades) and trades["realized_pnl_bnb"].notna().any():
    pnl = trades.dropna(subset=["realized_pnl_bnb"]).copy()

    # ts 以 Unix 秒保存
    pnl["ts"] = pd.to_datetime(pnl["ts"], unit="s", errors="coerce")
    pnl = pnl.dropna(subset=["ts"]).sort_values("ts")

    pnl["cum_pnl_bnb"] = pnl["realized_pnl_bnb"].cumsum()
    st.line_chart(pnl.set_index("ts")["cum_pnl_bnb"])
else:
    st.write("暂无已实现 PnL（至少需要一笔成功 SELL）。")
