from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import os

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class TokenRule:
    symbol: str
    address: str
    max_alloc_bnb: float
    add_step_bnb: float
    timeframe_sec: int
    ema_fast: int
    ema_slow: int
    rsi_period: int
    dust_size: float
    dex: str = "lfg"
    pool_id: str = ""


@dataclass(frozen=True)
class AppConfig:
    chain_id: int
    rpc_url: str
    request_timeout_sec: int
    max_retries: int
    backoff_sec: float

    lfg_factory: str
    lfg_hook: str
    lfg_swap_router: str
    lfg_pool_manager: str
    polling_interval_sec: int
    trade_cooldown_sec: int
    min_hold_minutes: int
    profit_gate_enabled: bool
    min_profit_pct: float
    max_loss_pct: float
    max_hold_minutes: int
    slippage_bps: int
    gas_limit: int
    min_bnb_for_gas: float
    min_trade_bnb: float
    max_trade_bnb: float
    blocks_per_candle: int
    confirmations: int
    log_chunk_blocks: int
    warmup_lookback_blocks: int
    max_history_candles: int
    fast_down_enabled: bool
    fast_down_candles: int
    fast_down_min_drop_pct: float
    fast_down_min_steps: int
    warmup_approve: bool
    approve_wait_sec: int
    dry_run: bool

    watch_tokens: List[TokenRule]

    trend_confirm_candles: int
    ema_deadband_pct: float
    dump_lookback: int
    dump_drop_pct: float
    pump_lookback: int
    pump_rise_pct: float
    bleed_lookback: int
    bleed_drop_pct: float
    bleed_rise_pct: float
    bleed_min_steps: int

    test_mode: bool = False
    test_action: str = "BUY"
    test_amount_bnb: float = 0.005
    test_once: bool = True


def _bot(raw: Dict[str, Any], key: str, default: Any) -> Any:
    return (raw.get("bot", {}) or {}).get(key, default)


def load_config(path: str = "config.yaml") -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = yaml.safe_load(f)

    rpc_url = os.environ.get("BSC_RPC_URL", "").strip()
    if not rpc_url:
        raise RuntimeError("缺少 .env 中的 BSC_RPC_URL")

    lfg_raw = raw.get("lfg", {}) or {}
    if not lfg_raw:
        raise RuntimeError("config.yaml 缺少 lfg: section")

    watch_tokens: List[TokenRule] = []
    for t in raw.get("watchlist", {}).get("tokens", []):
        watch_tokens.append(
            TokenRule(
                symbol=str(t["symbol"]),
                address=str(t["address"]),
                max_alloc_bnb=float(t["max_alloc_bnb"]),
                add_step_bnb=float(t["add_step_bnb"]),
                timeframe_sec=int(t.get("timeframe_sec", _bot(raw, "polling_interval_sec", 120))),
                ema_fast=int(t.get("ema_fast", 12)),
                ema_slow=int(t.get("ema_slow", 26)),
                rsi_period=int(t.get("rsi_period", 14)),
                dust_size=float(t.get("dust_size", 0.0001)),
                dex=str(t.get("dex", "lfg")).lower().strip(),
                pool_id=str(t.get("pool_id") or t.get("poolId") or "").strip(),
            )
        )

    bot_section = raw.get("bot", {}) or {}

    return AppConfig(
        chain_id=int(raw["chain"]["chain_id"]),
        rpc_url=rpc_url,
        request_timeout_sec=int(raw["rpc"].get("request_timeout_sec", 20)),
        max_retries=int(raw["rpc"].get("max_retries", 3)),
        backoff_sec=float(raw["rpc"].get("backoff_sec", 0.5)),
        lfg_factory=str(lfg_raw["factory"]),
        lfg_hook=str(lfg_raw["hook"]),
        lfg_swap_router=str(lfg_raw["swap_router"]),
        lfg_pool_manager=str(lfg_raw.get("pool_manager", "0x28e2Ea090877bF75740558f6BFB36A5ffeE9e9dF")),
        polling_interval_sec=int(bot_section.get("polling_interval_sec", 120)),
        trade_cooldown_sec=int(bot_section.get("trade_cooldown_sec", 300)),
        min_hold_minutes=int(bot_section.get("min_hold_minutes", 45)),
        profit_gate_enabled=bool(bot_section.get("profit_gate_enabled", True)),
        min_profit_pct=float(bot_section.get("min_profit_pct", 8.0)),
        max_loss_pct=float(bot_section.get("max_loss_pct", 20.0)),
        max_hold_minutes=int(bot_section.get("max_hold_minutes", 0)),
        slippage_bps=int(bot_section.get("slippage_bps", 1200)),
        gas_limit=int(bot_section.get("gas_limit", 650000)),
        min_bnb_for_gas=float(bot_section.get("min_bnb_for_gas", 0.005)),
        min_trade_bnb=float(bot_section.get("min_trade_bnb", 0.005)),
        max_trade_bnb=float(bot_section.get("max_trade_bnb", 0.7)),
        blocks_per_candle=int(bot_section.get("blocks_per_candle", 20)),
        confirmations=int(bot_section.get("confirmations", 5)),
        log_chunk_blocks=int(bot_section.get("log_chunk_blocks", 2000)),
        warmup_lookback_blocks=int(bot_section.get("warmup_lookback_blocks", 20000)),
        max_history_candles=int(bot_section.get("max_history_candles", 500)),
        fast_down_enabled=bool(bot_section.get("fast_down_enabled", True)),
        fast_down_candles=int(bot_section.get("fast_down_candles", 5)),
        fast_down_min_drop_pct=float(bot_section.get("fast_down_min_drop_pct", 0.25)),
        fast_down_min_steps=int(bot_section.get("fast_down_min_steps", 3)),
        warmup_approve=bool(bot_section.get("warmup_approve", True)),
        approve_wait_sec=int(bot_section.get("approve_wait_sec", 45)),
        dry_run=bool(bot_section.get("dry_run", True)),
        watch_tokens=watch_tokens,
        trend_confirm_candles=int(bot_section.get("trend_confirm_candles", 3)),
        ema_deadband_pct=float(bot_section.get("ema_deadband_pct", 0.10)),
        dump_lookback=int(bot_section.get("dump_lookback", 2)),
        dump_drop_pct=float(bot_section.get("dump_drop_pct", 0.30)),
        pump_lookback=int(bot_section.get("pump_lookback", 2)),
        pump_rise_pct=float(bot_section.get("pump_rise_pct", 0.30)),
        bleed_lookback=int(bot_section.get("bleed_lookback", 5)),
        bleed_drop_pct=float(bot_section.get("bleed_drop_pct", 0.50)),
        bleed_rise_pct=float(bot_section.get("bleed_rise_pct", 0.50)),
        bleed_min_steps=int(bot_section.get("bleed_min_steps", 5)),
        test_mode=bool(bot_section.get("test_mode", False)),
        test_action=str(bot_section.get("test_action", "BUY")).upper(),
        test_amount_bnb=float(bot_section.get("test_amount_bnb", 0.005)),
        test_once=bool(bot_section.get("test_once", True)),
    )
