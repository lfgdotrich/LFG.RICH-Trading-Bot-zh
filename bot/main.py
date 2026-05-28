from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict

from web3 import Web3

from bot.config import load_config
from bot.data.candles_lfg_block import Candle, trades_to_block_candles
from bot.db_state import KV
from bot.execution.lfg_trader import TraderLFG
from bot.logging_setup import setup_logging
from bot.onchain import lfg
from bot.onchain.erc20 import balance_of, decimals
from bot.rpc import make_web3, with_retries
from bot.strategy.trend_momentum import compute_signal
from bot.trade_db import TradeDB

log = logging.getLogger("bot")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def norm_tx_hash(txh: str | None) -> str:
    if not txh:
        return ""
    txh = str(txh).strip()
    if not txh:
        return ""
    return txh if txh.startswith("0x") else "0x" + txh


def is_pruned_history_error(exc: Exception) -> bool:
    """检测拒绝历史 eth_getLogs 范围的 RPC 提供商。

    一些 BSC RPC 提供商会保留最新区块状态，但裁剪较旧的日志历史。
    发生这种情况时，即使配置的 warmup_lookback_blocks 很小，
    如果 state.db 仍然包含上次运行保存的较旧 last_block，扫描旧区块也会失败。
    """
    msg = str(exc).lower()
    return (
        "history has been pruned" in msg
        or "pruned for this block" in msg
        or ("pruned" in msg and "block" in msg)
    )


def pick_first_sellable_lot(
    lots: list[dict],
    *,
    token_dec: int,
    wallet_qty: float,
    price_wbnb_per_token: float | None = None,
) -> tuple[dict | None, float, float | None]:
    """选择预估 PnL 最好的开放 lot；如果无法估算，则回退到第一个可卖 lot。"""
    if not lots or wallet_qty <= 0:
        return None, 0.0, None

    scale = 10 ** int(token_dec)
    first_lot = None
    first_qty = 0.0
    best_lot = None
    best_qty = 0.0
    best_profit = None
    best_score = float("-inf")

    for lot in lots:
        lot_qty_open = float(lot.get("qty_open", 0.0) or 0.0)
        if lot_qty_open <= 0:
            continue
        qty_to_sell = min(lot_qty_open, float(wallet_qty))
        if qty_to_sell <= 0 or int(qty_to_sell * scale) <= 0:
            continue
        if first_lot is None:
            first_lot = lot
            first_qty = qty_to_sell
        if price_wbnb_per_token is None or price_wbnb_per_token <= 0:
            return lot, qty_to_sell, None
        lot_cost = float(lot.get("cost_open_bnb", 0.0) or 0.0)
        if lot_cost <= 0:
            continue
        cost_for_qty = lot_cost * (qty_to_sell / lot_qty_open)
        value_for_qty = qty_to_sell * float(price_wbnb_per_token)
        if cost_for_qty <= 0:
            continue
        profit_pct = (value_for_qty / cost_for_qty - 1.0) * 100.0
        if profit_pct > best_score:
            best_score = profit_pct
            best_lot = lot
            best_qty = qty_to_sell
            best_profit = profit_pct

    if best_lot is not None:
        return best_lot, best_qty, best_profit
    if first_lot is not None:
        return first_lot, first_qty, None
    return None, 0.0, None


def fast_downtrend_last_n(hist: list[Candle], n: int, min_drop_pct: float, min_down_steps: int) -> bool:
    real = [c for c in hist if float(getattr(c, "volume_token", 0.0) or 0.0) > 0]
    if len(real) < max(2, n):
        return False
    window = real[-int(n):]
    first = float(window[0].close)
    last = float(window[-1].close)
    if first <= 0:
        return False
    drop_pct = (last / first - 1.0) * 100.0
    down_steps = sum(1 for i in range(1, len(window)) if float(window[i].close) < float(window[i - 1].close))
    return drop_pct <= -abs(float(min_drop_pct)) and down_steps >= int(min_down_steps)


def main() -> None:
    setup_logging()
    cfg = load_config()
    w3 = make_web3(cfg.rpc_url, cfg.request_timeout_sec)

    log.info("已连接 chain_id=%s latest_block=%s", w3.eth.chain_id, w3.eth.block_number)
    if int(w3.eth.chain_id) != int(cfg.chain_id):
        raise RuntimeError(f"RPC chain_id 不匹配：期望 {cfg.chain_id}，实际 {w3.eth.chain_id}")

    if not cfg.watch_tokens:
        log.warning("Watchlist 为空。请在 config.yaml 的 watchlist.tokens 下添加 LFG.RICH 代币")
        return

    kv = KV("state.db")
    tdb = TradeDB("state.db")
    tdb.set_dust_map({t.symbol: float(t.dust_size) for t in cfg.watch_tokens})

    wallet = (os.environ.get("WALLET_ADDRESS") or "").strip()
    private_key = (os.environ.get("PRIVATE_KEY") or "").strip()
    trader = None

    if wallet:
        wallet = Web3.to_checksum_address(wallet)
        log.info("钱包：%s", wallet)
        if not private_key and not cfg.dry_run:
            raise RuntimeError("缺少 .env 中的 PRIVATE_KEY（dry_run=false 时必需）")
        trader = TraderLFG(
            w3,
            factory=cfg.lfg_factory,
            hook=cfg.lfg_hook,
            swap_router=cfg.lfg_swap_router,
            wallet=wallet,
            private_key=private_key,
            gas_limit=cfg.gas_limit,
            slippage_bps=cfg.slippage_bps,
        )
    else:
        log.warning("未设置 WALLET_ADDRESS。机器人会跟踪价格/信号，但不能交易。")

    blocks_per_candle = int(cfg.blocks_per_candle)
    confirmations = int(cfg.confirmations)
    chunk_size = int(cfg.log_chunk_blocks)
    warmup_lookback_blocks = int(cfg.warmup_lookback_blocks)
    max_history_candles = int(cfg.max_history_candles)

    decimals_cache: dict[str, int] = {}
    pool_id_cache: dict[str, str] = {}
    meta_cache: dict[str, dict] = {}
    candle_history: dict[str, list[Candle]] = {}
    log.info(
        "LFG 扫描器配置：confirmations=%s log_chunk_blocks=%s warmup_lookback_blocks=%s max_history_candles=%s",
        confirmations,
        chunk_size,
        warmup_lookback_blocks,
        max_history_candles,
    )

    balance_refresh_sec = 120
    last_bal_ts = 0.0
    cached_bnb_balance_wei: int | None = None
    cached_token_balance_raw: dict[str, int] = {}
    did_rebuild_positions = False
    did_sync_positions_from_chain = False

    def _token_dec(token_addr: str) -> int:
        token_addr = Web3.to_checksum_address(token_addr)
        if token_addr not in decimals_cache:
            try:
                decimals_cache[token_addr] = int(with_retries(lambda: decimals(w3, token_addr), cfg.max_retries, cfg.backoff_sec))
            except Exception:
                decimals_cache[token_addr] = 18
        return int(decimals_cache[token_addr])

    def _candles_key(token_addr: str) -> str:
        return f"lfg:onchain:candles:{token_addr.lower()}:bpc{blocks_per_candle}"

    def save_history(token_addr: str, hist: list[Candle]) -> None:
        trimmed = hist[-max_history_candles:]
        kv.set_json(_candles_key(token_addr), [asdict(c) for c in trimmed])

    def load_history(token_addr: str) -> list[Candle]:
        data = kv.get_json(_candles_key(token_addr), default=None)
        if not data:
            return []
        return [Candle(**d) for d in data]

    def ensure_flat_candles(hist: list[Candle], spot_price: float, safe_to_block: int, token_cfg) -> list[Candle]:
        if spot_price <= 0:
            return hist
        current_bucket = int(safe_to_block) // int(blocks_per_candle)
        if not hist:
            slow = int(getattr(token_cfg, "ema_slow", 26) or 26)
            rsi_p = int(getattr(token_cfg, "rsi_period", 14) or 14)
            bootstrap = min(max(slow * 3, rsi_p * 3, 60), max_history_candles)
            start_bucket = max(0, current_bucket - bootstrap + 1)
            return [
                Candle(b, float(spot_price), float(spot_price), float(spot_price), float(spot_price), 0.0)
                for b in range(start_bucket, current_bucket + 1)
            ]

        last = hist[-1]
        if int(last.bucket) < current_bucket:
            prev = float(last.close or spot_price)
            for b in range(int(last.bucket) + 1, current_bucket + 1):
                hist.append(Candle(b, prev, prev, prev, prev, 0.0))
            last = hist[-1]
        last.close = float(spot_price)
        last.high = max(float(last.high), float(spot_price))
        last.low = min(float(last.low), float(spot_price))
        return hist[-max_history_candles:]

    def _get_pool_id(token_addr: str, token_cfg=None) -> str:
        token_addr = Web3.to_checksum_address(token_addr)
        if token_addr in pool_id_cache:
            return pool_id_cache[token_addr]

        configured_pool = getattr(token_cfg, "pool_id", "") if token_cfg is not None else ""
        ctx = with_retries(
            lambda: lfg.resolve_token_context(
                w3,
                token_addr,
                default_factory=cfg.lfg_factory,
                default_hook=cfg.lfg_hook,
                configured_pool_id=configured_pool,
            ),
            cfg.max_retries,
            cfg.backoff_sec,
        )

        meta = {
            "context": ctx,
            "factory": ctx.factory,
            "hook": ctx.hook,
            "pool_id": ctx.pool_id,
            "pool_key": ctx.pool_key,
            "token_dec": _token_dec(token_addr),
            "totalFeeBps": int(ctx.state.get("totalFeeBps", 125) or 125),
            "priceScale": int(ctx.price_scale or lfg.V5_PRICE_SCALE),
            "protocolVersion": str(ctx.protocol_version or "unknown"),
            "metadataSource": ctx.metadata_source,
            "canLiveTrade": bool(ctx.initialized),
        }

        if not ctx.initialized:
            log.warning(
                "[%s] 已解析 poolId=%s factory=%s hook=%s 来源=%s，但 tokenStates(poolId) 尚未初始化/不匹配。在初始化前会跳过真实交易。",
                token_addr, ctx.pool_id, ctx.factory, ctx.hook, ctx.metadata_source or "链上解析器"
            )

        pool_id_cache[token_addr] = str(ctx.pool_id)
        meta_cache[token_addr] = meta
        if trader:
            trader.set_token_context(token_addr, context=ctx)
        log.info(
            "[%s] LFG 上下文 pool_id=%s factory=%s hook=%s 来源=%s 版本=%s price_scale=%s live_trade=%s",
            token_addr,
            ctx.pool_id,
            ctx.factory,
            ctx.hook,
            ctx.metadata_source or "on-chain",
            meta.get("protocolVersion"),
            meta.get("priceScale"),
            meta.get("canLiveTrade"),
        )
        return pool_id_cache[token_addr]

    def _spot_price(pool_id: str, token_addr=None) -> float:
        if token_addr:
            token_addr = Web3.to_checksum_address(token_addr)
            meta = meta_cache.get(token_addr) or {}
            hook_addr = meta.get("hook") or cfg.lfg_hook
            price_scale = int(meta.get("priceScale") or lfg.V5_PRICE_SCALE)
        else:
            hook_addr = cfg.lfg_hook
            price_scale = lfg.V5_PRICE_SCALE
        return with_retries(
            lambda: lfg.get_effective_price_bnb_per_token(w3, hook_addr, pool_id, price_scale=price_scale),
            cfg.max_retries,
            cfg.backoff_sec,
        )

    def _get_gas_cost_wei(tx_hash: str) -> int:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            gas_used = int(receipt.get("gasUsed", 0))
            eff = receipt.get("effectiveGasPrice", None)
            if eff is None:
                tx = w3.eth.get_transaction(tx_hash)
                eff = tx.get("gasPrice", 0)
            return gas_used * int(eff)
        except Exception:
            return 0

    def _compute_and_record_mined_trade(tx_hash: str, token_addr: str, symbol: str) -> bool:
        if not wallet:
            return False
        tr = tdb.get_trade(tx_hash)
        if tr is None or tr.bnb_before_wei is None or tr.tok_before_raw is None or tr.token_dec is None:
            return False

        bnb_before = int(tr.bnb_before_wei)
        tok_before = int(tr.tok_before_raw)
        token_dec = int(tr.token_dec)
        side = str(tr.side or "").upper()

        bnb_after = int(w3.eth.get_balance(wallet))
        tok_after = int(balance_of(w3, token_addr, wallet))
        gas_cost_bnb = _get_gas_cost_wei(tx_hash) / 1e18
        delta_bnb_raw = (bnb_after - bnb_before) / 1e18
        delta_bnb = delta_bnb_raw + gas_cost_bnb
        delta_token = (tok_after - tok_before) / (10 ** token_dec)

        realized = None
        pos = tdb.get_position(symbol)
        qty = 0.0 if pos is None else float(pos.qty_token or 0.0)
        cost = 0.0 if pos is None else float(pos.cost_bnb or 0.0)

        if side == "BUY":
            spent = max(0.0, -delta_bnb)
            got = max(0.0, delta_token)
            tdb.upsert_position(symbol, token_addr, qty + got, cost + spent)
            if got > 0 and spent > 0:
                tdb.create_lot(symbol=symbol, token=token_addr, buy_tx=tx_hash, qty=got, cost_bnb=spent)
        elif side == "SELL":
            proceeds = max(0.0, delta_bnb)
            sold = max(0.0, -delta_token)
            cost_sold = 0.0
            if sold > 0:
                note = str(tr.note or "")
                m = re.search(r"lot_id=(\d+)", note)
                lot_id = int(m.group(1)) if m else tdb.pick_lot_id_by_qty(symbol=symbol, qty_to_sell=float(sold))
                if lot_id is not None:
                    realized, cost_sold = tdb.consume_lot_by_id(
                        lot_id=lot_id,
                        qty_to_sell=float(sold),
                        proceeds_total_bnb=float(proceeds),
                        sell_tx=tx_hash,
                        ts=int(tr.ts or time.time()),
                    )
            tdb.upsert_position(symbol, token_addr, max(0.0, qty - sold), max(0.0, cost - cost_sold))

        tdb.mark_mined(
            tx_hash=tx_hash,
            ok=True,
            delta_bnb=delta_bnb,
            delta_token=delta_token,
            realized_pnl_bnb=realized,
            note=f"通过 LFG reconcile 确认上链 (gas={gas_cost_bnb:.8f})",
        )
        log.info("[%s] 已确认上链 %s delta_bnb=%.8f delta_token=%.8f realized=%s", symbol, side, delta_bnb, delta_token, realized)
        return True

    def _reconcile_sent_trades() -> None:
        if not trader:
            return
        now_ts = int(time.time())
        for tr in tdb.list_sent_trades():
            tx_hash = norm_tx_hash(tr.tx_hash)
            status = trader.try_get_receipt_status(tx_hash)
            if status is None:
                continue
            token_addr = Web3.to_checksum_address(tr.token)
            if int(status) != 1:
                tdb.mark_status_only(tx_hash, "MINED_FAIL", "receipt status=0")
                kv.set_str(f"pending_tx:{token_addr.lower()}", "")
                continue
            ok = _compute_and_record_mined_trade(tx_hash, token_addr, tr.symbol)
            if not ok:
                tdb.mark_status_only(tx_hash, "MINED_OK", "已上链，但没有可用快照")
            kv.set_str(f"pending_tx:{token_addr.lower()}", "")
            kv.set_int(f"cooldown_until:{token_addr.lower()}", now_ts + int(cfg.trade_cooldown_sec))

    def rebuild_positions_from_trades() -> None:
        for t in cfg.watch_tokens:
            token_addr = Web3.to_checksum_address(t.address)
            tdb.upsert_position(t.symbol, token_addr, 0.0, 0.0)
        tdb.reset_lots()
        mined = tdb.list_mined_trades()
        for tr in mined:
            symbol = tr["symbol"]
            token_addr = Web3.to_checksum_address(tr["token"])
            side = str(tr["side"] or "").upper()
            pos = tdb.get_position(symbol)
            qty = 0.0 if pos is None else float(pos.qty_token or 0.0)
            cost = 0.0 if pos is None else float(pos.cost_bnb or 0.0)
            delta_bnb = float(tr["delta_bnb"] or 0.0)
            delta_token = float(tr["delta_token"] or 0.0)
            if side == "BUY":
                spent = max(0.0, -delta_bnb)
                got = max(0.0, delta_token)
                tdb.upsert_position(symbol, token_addr, qty + got, cost + spent)
                if got > 0 and spent > 0:
                    tdb.create_lot(symbol=symbol, token=token_addr, buy_tx="rebuild", qty=got, cost_bnb=spent)
            elif side == "SELL":
                proceeds = max(0.0, delta_bnb)
                sold = max(0.0, -delta_token)
                realized, cost_sold = tdb.consume_lots_fifo(symbol, token_addr, "rebuild", sold, proceeds)
                tdb.upsert_position(symbol, token_addr, max(0.0, qty - sold), max(0.0, cost - cost_sold))
        log.info("rebuild_positions_from_trades：已从 %d 笔已上链交易重建", len(mined))

    def sync_positions_from_chain() -> None:
        if not wallet:
            return
        for t in cfg.watch_tokens:
            token_addr = Web3.to_checksum_address(t.address)
            dec = _token_dec(token_addr)
            raw = int(balance_of(w3, token_addr, wallet))
            qty_onchain = raw / (10 ** dec)
            pos = tdb.get_position(t.symbol)
            if pos is None:
                tdb.upsert_position(t.symbol, token_addr, qty_onchain, 0.0)
            elif qty_onchain <= 0:
                tdb.upsert_position(t.symbol, token_addr, 0.0, 0.0)
            else:
                old_qty = float(pos.qty_token or 0.0)
                old_cost = float(pos.cost_bnb or 0.0)
                if old_qty > 0 and old_cost > 0:
                    avg = old_cost / old_qty
                    tdb.upsert_position(t.symbol, token_addr, qty_onchain, avg * qty_onchain)
                else:
                    tdb.upsert_position(t.symbol, token_addr, qty_onchain, 0.0)

    def _wait_mined_ok(tx_hash: str, timeout_sec: int) -> bool:
        if not trader:
            return False
        deadline = time.time() + int(timeout_sec)
        while time.time() < deadline:
            st = trader.try_get_receipt_status(tx_hash)
            if st == 1:
                return True
            if st == 0:
                return False
            time.sleep(2)
        return False

    # 为卖出预热授权。
    if trader and cfg.warmup_approve:
        for t in cfg.watch_tokens:
            try:
                appr = trader.ensure_allowance_or_approve_max(Web3.to_checksum_address(t.address), 10 ** 30, cfg.dry_run)
                if appr.action == "APPROVE":
                    log.info("[%s] 预热授权：%s tx=%s note=%s", t.symbol, appr.status, appr.tx_hash, appr.note)
            except Exception as e:
                log.warning("[%s] 预热授权失败：%s", t.symbol, e)

    try:
        while True:
            latest_block = with_retries(lambda: w3.eth.block_number, cfg.max_retries, cfg.backoff_sec)
            safe_to = max(1, int(latest_block) - confirmations)
            log.info("调试：latest_block=%s safe_to=%s blocks_per_candle=%s", latest_block, safe_to, blocks_per_candle)

            _reconcile_sent_trades()
            if not did_rebuild_positions:
                rebuild_positions_from_trades()
                did_rebuild_positions = True

            for t in cfg.watch_tokens:
                token = Web3.to_checksum_address(t.address)
                if str(getattr(t, "dex", "lfg")).lower().strip() not in ("lfg", "v4"):
                    log.warning("[%s] 此 LFG 构建不支持 dex=%s；请使用 dex=lfg", t.symbol, t.dex)
                    continue

                try:
                    dec = _token_dec(token)
                    pool_id = _get_pool_id(token, t)
                    meta = meta_cache[token]

                    if token not in candle_history:
                        candle_history[token] = load_history(token)
                        if candle_history[token]:
                            log.info("[%s] 已从数据库恢复 LFG K 线：%d", t.symbol, len(candle_history[token]))

                    hist = candle_history.get(token, [])
                    trades = []
                    new_from = None
                    new_to = None
                    scan_source = "on-chain"

                    last_key = f"lfg:last_block:{pool_id}"
                    last = kv.get_int(last_key, default=0)

                    # 永远不要让旧的 state.db 值强迫扫描器低于
                    # 配置的 warmup 窗口。对于裁剪历史的 RPC
                    # 提供商来说这很重要：降低配置中的 warmup_lookback_blocks 必须
                    # 生效，即使旧运行保存了更旧的区块。
                    warmup_from = max(1, int(safe_to) - int(warmup_lookback_blocks) + 1)
                    min_last_for_window = warmup_from - 1
                    if last <= 0:
                        last = min_last_for_window
                    elif last < min_last_for_window:
                        log.warning(
                            "[%s] 保存的 last_block=%s 早于配置的 warmup 窗口；限制为 %s 以避免 RPC 历史被裁剪",
                            t.symbol,
                            last,
                            min_last_for_window,
                        )
                        last = min_last_for_window
                        kv.set_int(last_key, last)

                    if safe_to <= last:
                        trades = []
                    else:
                        new_from = max(last + 1, warmup_from)
                        new_to = safe_to
                        try:
                            trades = with_retries(
                                lambda: lfg.fetch_trades(
                                    w3,
                                    meta.get("hook") or cfg.lfg_hook,
                                    pool_id,
                                    new_from,
                                    new_to,
                                    chunk_size=chunk_size,
                                    price_scale=int(meta.get("priceScale") or lfg.V5_PRICE_SCALE),
                                ),
                                cfg.max_retries,
                                cfg.backoff_sec,
                            )
                            kv.set_int(last_key, safe_to)
                        except Exception as e:
                            if not is_pruned_history_error(e):
                                raise

                            # 针对裁剪历史提供商的最后恢复手段：只尝试
                            # 最新的区块片段。如果连它也被裁剪，则跳过本轮日志
                            # 历史扫描，并继续使用当前
                            # 链上现货价格，避免机器人卡住。
                            recent_blocks = max(1, min(int(chunk_size), int(warmup_lookback_blocks), 100))
                            retry_from = max(1, int(safe_to) - recent_blocks + 1)
                            log.warning(
                                "[%s] RPC 裁剪了范围 %s-%s 的历史；正在重试最近范围 %s-%s",
                                t.symbol,
                                new_from,
                                new_to,
                                retry_from,
                                new_to,
                            )
                            try:
                                trades = with_retries(
                                    lambda: lfg.fetch_trades(
                                        w3,
                                        meta.get("hook") or cfg.lfg_hook,
                                        pool_id,
                                        retry_from,
                                        new_to,
                                        chunk_size=max(1, min(int(chunk_size), recent_blocks)),
                                        price_scale=int(meta.get("priceScale") or lfg.V5_PRICE_SCALE),
                                    ),
                                    1,
                                    cfg.backoff_sec,
                                )
                                new_from = retry_from
                                kv.set_int(last_key, safe_to)
                            except Exception as e2:
                                if not is_pruned_history_error(e2):
                                    raise
                                log.warning(
                                    "[%s] RPC 连最新 %s 个区块也裁剪了；本轮跳过事件扫描并继续使用现货价格",
                                    t.symbol,
                                    recent_blocks,
                                )
                                trades = []
                                # 将游标移动到 safe_to，避免机器人
                                # 永远重试同一段被裁剪的范围。之后的
                                # 循环只会扫描新区块。
                                kv.set_int(last_key, safe_to)

                    new_candles = trades_to_block_candles(
                        trades=trades,
                        blocks_per_candle=blocks_per_candle,
                        token_decimals=dec,
                        price_scale=int(meta.get("priceScale") or lfg.V5_PRICE_SCALE),
                    )
                    if new_candles:
                        by_bucket = {c.bucket: c for c in hist}
                        for c in new_candles:
                            by_bucket[c.bucket] = c
                        hist = [by_bucket[k] for k in sorted(by_bucket.keys())][-max_history_candles:]

                    spot = _spot_price(pool_id, token)
                    hist = ensure_flat_candles(hist, spot, safe_to, t)

                    candle_history[token] = hist[-max_history_candles:]
                    save_history(token, candle_history[token])
                    hist = candle_history[token]

                    if not hist:
                        log.info("[%s] 暂无价格/K 线", t.symbol)
                        continue

                    sig = compute_signal(
                        hist,
                        t.ema_fast,
                        t.ema_slow,
                        t.rsi_period,
                        confirm_candles=int(cfg.trend_confirm_candles),
                        ema_deadband_pct=float(cfg.ema_deadband_pct),
                        dump_lookback=int(cfg.dump_lookback),
                        dump_drop_pct=float(cfg.dump_drop_pct),
                        pump_lookback=int(cfg.pump_lookback),
                        pump_rise_pct=float(cfg.pump_rise_pct),
                        bleed_lookback=int(cfg.bleed_lookback),
                        bleed_drop_pct=float(cfg.bleed_drop_pct),
                        bleed_rise_pct=float(cfg.bleed_rise_pct),
                        bleed_min_steps=int(cfg.bleed_min_steps),
                    )

                    real_candles = sum(1 for c in hist if float(getattr(c, "volume_token", 0.0) or 0.0) > 0)
                    price_token_in_wbnb = float(hist[-1].close)
                    if price_token_in_wbnb <= 0:
                        log.info("[%s] 无效价格", t.symbol)
                        continue

                    if (not did_sync_positions_from_chain) and wallet:
                        sync_positions_from_chain()
                        did_sync_positions_from_chain = True

                    now_ts = int(time.time())
                    intended = "HOLD"
                    trade_bnb = 0.0
                    approx_token_out = 0.0
                    note = ""
                    force_sell = False
                    trade_lot_id = None
                    trade_profit_pct = None

                    bnb_balance = 0.0
                    token_balance = 0.0
                    current_alloc_bnb = 0.0
                    allocatable_bnb = 0.0

                    if wallet:
                        now = time.time()
                        if cached_bnb_balance_wei is None or now - last_bal_ts >= balance_refresh_sec:
                            cached_bnb_balance_wei = int(w3.eth.get_balance(wallet))
                            cached_token_balance_raw[token] = int(balance_of(w3, token, wallet))
                            last_bal_ts = now

                        bnb_balance = cached_bnb_balance_wei / 1e18
                        token_balance = cached_token_balance_raw.get(token, 0) / (10 ** dec)
                        current_alloc_bnb = token_balance * price_token_in_wbnb
                        allocatable_bnb = max(0.0, bnb_balance - float(cfg.min_bnb_for_gas))

                        pos = tdb.get_position(t.symbol)
                        profit_pct = None
                        if pos and float(pos.cost_bnb or 0.0) > 0 and current_alloc_bnb > 0:
                            profit_pct = (current_alloc_bnb / float(pos.cost_bnb) - 1.0) * 100.0

                        lots = tdb.list_open_lots(t.symbol)
                        best_lot, qty_to_sell, lot_profit_pct = pick_first_sellable_lot(
                            lots,
                            token_dec=dec,
                            wallet_qty=token_balance,
                            price_wbnb_per_token=price_token_in_wbnb,
                        )
                        trade_profit_pct = lot_profit_pct if lot_profit_pct is not None else profit_pct

                        log.info(
                            "[%s] 余额：bnb=%.6f token=%.6f value≈%.6f BNB price=%.12g fee=%sbps",
                            t.symbol,
                            bnb_balance,
                            token_balance,
                            current_alloc_bnb,
                            price_token_in_wbnb,
                            meta.get("totalFeeBps", "?"),
                        )

                        if fast_downtrend_last_n(hist, cfg.fast_down_candles, cfg.fast_down_min_drop_pct, cfg.fast_down_min_steps):
                            if best_lot and qty_to_sell > 0 and trade_profit_pct is not None and trade_profit_pct >= float(cfg.min_profit_pct):
                                force_sell = True
                                intended = "SELL"
                                trade_lot_id = best_lot.get("id")
                                approx_token_out = qty_to_sell
                                trade_bnb = qty_to_sell * price_token_in_wbnb
                                note = f"快速下跌覆盖：lot_id={trade_lot_id} lot_profit={trade_profit_pct:.2f}%"

                        if not force_sell:
                            if cfg.test_mode:
                                action = str(cfg.test_action).upper()
                                if action == "BUY":
                                    intended = "BUY"
                                    trade_bnb = min(float(cfg.test_amount_bnb), max(0.0, allocatable_bnb))
                                    approx_token_out = trade_bnb / price_token_in_wbnb if price_token_in_wbnb > 0 else 0.0
                                    note = "TEST_MODE 买入"
                                elif action == "SELL":
                                    intended = "SELL"
                                    trade_bnb = min(float(cfg.test_amount_bnb), float(current_alloc_bnb))
                                    approx_token_out = trade_bnb / price_token_in_wbnb if price_token_in_wbnb > 0 else 0.0
                                    note = "TEST_MODE 卖出"
                            elif sig.score >= 0.6:
                                target_alloc = min(float(t.max_alloc_bnb), allocatable_bnb + current_alloc_bnb)
                                delta = target_alloc - current_alloc_bnb
                                if delta > 0 and allocatable_bnb > 0:
                                    intended = "BUY"
                                    trade_bnb = clamp(delta, 0.0, min(float(t.add_step_bnb), float(cfg.max_trade_bnb), allocatable_bnb))
                                    if 0 < trade_bnb < float(cfg.min_trade_bnb):
                                        trade_bnb = allocatable_bnb
                                        note = f"低余额买入：可用={allocatable_bnb:.6f}"
                                    approx_token_out = trade_bnb / price_token_in_wbnb if trade_bnb > 0 else 0.0
                            elif sig.score <= -0.6 and current_alloc_bnb > 0 and token_balance > 0:
                                if best_lot and qty_to_sell > 0:
                                    intended = "SELL"
                                    trade_lot_id = best_lot.get("id")
                                    approx_token_out = qty_to_sell
                                    trade_bnb = qty_to_sell * price_token_in_wbnb
                                    note = f"卖出 LOT lot_id={trade_lot_id} qty≈{approx_token_out:.6f}"
                                    if trade_profit_pct is not None:
                                        note += f" lot_pnl≈{trade_profit_pct:.2f}%"
                                elif token_balance > 0:
                                    intended = "SELL"
                                    approx_token_out = token_balance
                                    trade_bnb = current_alloc_bnb
                                    note = "卖出钱包余额（没有开放 lot）"

                        if intended == "SELL" and trade_bnb > 0 and not cfg.test_mode:
                            stop_loss = trade_profit_pct is not None and trade_profit_pct <= -float(cfg.max_loss_pct)
                            if cfg.profit_gate_enabled and not stop_loss:
                                if trade_profit_pct is None or trade_profit_pct < float(cfg.min_profit_pct):
                                    note = f"卖出被阻止：利润 {trade_profit_pct if trade_profit_pct is not None else 'n/a'} < {cfg.min_profit_pct:.2f}%"
                                    intended = "HOLD"
                                    trade_bnb = 0.0
                                    approx_token_out = 0.0

                        range_info = f" 来源={scan_source} 范围={new_from}-{new_to}" if new_from and new_to else f" 来源={scan_source}"
                        log.info(
                            "[%s] new_events=%d hist=%d real=%d%s | score=%.2f trend=%s rsi=%.2f price=%.12g intended=%s trade_bnb=%.6f note=%s",
                            t.symbol,
                            len(trades),
                            len(hist),
                            real_candles,
                            range_info,
                            sig.score,
                            sig.trend,
                            sig.rsi,
                            price_token_in_wbnb,
                            intended,
                            trade_bnb,
                            note or sig.reason,
                        )

                        if not trader or intended not in ("BUY", "SELL") or trade_bnb <= 0:
                            continue
                        if not bool(meta.get("canLiveTrade")):
                            log.warning("[%s] signal=%s，但已跳过真实交易：token hook 中的 pool 尚未初始化", t.symbol, intended)
                            continue

                        pending_key = f"pending_tx:{token.lower()}"
                        cd_key = f"cooldown_until:{token.lower()}"
                        pending = kv.get_str(pending_key) or ""
                        if pending:
                            log.info("[%s] 存在待确认 tx：%s", t.symbol, pending)
                            continue
                        cd_until = kv.get_int(cd_key, default=0)
                        if now_ts < cd_until and not cfg.test_mode:
                            log.info("[%s] 冷却中，剩余 %ds", t.symbol, cd_until - now_ts)
                            continue

                        bnb_before = int(w3.eth.get_balance(wallet))
                        tok_before = int(balance_of(w3, token, wallet))
                        res = None

                        if intended == "BUY":
                            res = trader.buy_token_with_bnb(token, trade_bnb, dec, price_token_in_wbnb, cfg.dry_run)
                        elif intended == "SELL":
                            amount_raw = int((approx_token_out or (trade_bnb / price_token_in_wbnb)) * (10 ** dec))
                            appr = trader.ensure_allowance_or_approve_max(token, amount_raw, cfg.dry_run)
                            if appr.action == "APPROVE" and appr.status in ("SENT", "DRY_RUN"):
                                log.info("[%s] 执行 %s %s tx=%s note=%s", t.symbol, appr.status, appr.action, appr.tx_hash, appr.note)
                                if appr.status == "SENT" and appr.tx_hash:
                                    txh = norm_tx_hash(appr.tx_hash)
                                    kv.set_str(pending_key, txh)
                                    if _wait_mined_ok(txh, int(cfg.approve_wait_sec)):
                                        kv.set_str(pending_key, "")
                                        res = trader.sell_token_to_bnb(token, trade_bnb, dec, price_token_in_wbnb, cfg.dry_run)
                                    else:
                                        res = None
                            else:
                                res = trader.sell_token_to_bnb(token, trade_bnb, dec, price_token_in_wbnb, cfg.dry_run)

                        if res is not None:
                            log.info("[%s] 执行 %s %s tx=%s note=%s", t.symbol, res.status, res.action, res.tx_hash, res.note)
                            if res.status == "SENT" and res.tx_hash:
                                db_note = note
                                if res.note:
                                    db_note = f"{note} | {res.note}" if note else str(res.note)
                                tdb.insert_sent(
                                    symbol=t.symbol,
                                    token=token,
                                    side=res.action,
                                    tx_hash=norm_tx_hash(res.tx_hash),
                                    note=db_note,
                                    bnb_before_wei=bnb_before,
                                    tok_before_raw=tok_before,
                                    token_dec=dec,
                                )
                                if res.action == "BUY":
                                    kv.set_int(f"last_buy_ts:{token.lower()}", int(time.time()))
                                kv.set_str(pending_key, norm_tx_hash(res.tx_hash))
                                kv.set_int(cd_key, now_ts + int(cfg.trade_cooldown_sec))

                        if cfg.test_mode and cfg.test_once:
                            log.info("TEST_MODE 已完成一次执行尝试；退出。")
                            return

                except Exception as e:
                    log.warning("[%s] 循环错误（将继续）：%s", getattr(t, "symbol", "?"), e)
                    continue

            time.sleep(int(cfg.polling_interval_sec))

    except KeyboardInterrupt:
        log.info("正在优雅关闭（Ctrl-C）。")


if __name__ == "__main__":
    main()
