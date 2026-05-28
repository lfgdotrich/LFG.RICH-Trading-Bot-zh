from __future__ import annotations

import os
import sys
from web3 import Web3

from bot.config import load_config
from bot.onchain import lfg
from bot.rpc import make_web3


def main() -> None:
    cfg = load_config()
    w3 = make_web3(cfg.rpc_url, cfg.request_timeout_sec)
    token_cfg = cfg.watch_tokens[0]
    token = Web3.to_checksum_address(token_cfg.address)
    wallet = os.environ.get("WALLET_ADDRESS") or lfg.ZERO_ADDRESS

    print("token:", token)
    print("configured_factory_fallback:", cfg.lfg_factory)
    print("configured_hook_fallback:", cfg.lfg_hook)
    print("swap_router:", cfg.lfg_swap_router)

    try:
        ctx = lfg.resolve_token_context(
            w3,
            token,
            default_factory=cfg.lfg_factory,
            default_hook=cfg.lfg_hook,
            configured_pool_id=getattr(token_cfg, "pool_id", ""),
        )
    except Exception as e:
        print("错误：无法解析代币链上上下文：", e)
        sys.exit(2)

    print("resolved_factory:", ctx.factory)
    print("resolved_hook:", ctx.hook)
    print("resolved_pool_id:", ctx.pool_id)
    print("resolved_pool_key:", ctx.pool_key)
    print("metadata_source:", ctx.metadata_source or "链上回退")
    print("protocol_version:", ctx.protocol_version)
    print("price_scale:", ctx.price_scale)
    print("initialized:", ctx.initialized)

    if not ctx.pool_id:
        print("错误：无法从 token、factory、hook 或 PoolKey 推导找到 poolId。")
        sys.exit(2)

    if not ctx.initialized:
        print("live_trade: false")
        print("诊断：poolId 已在链上解析，但 tokenStates(poolId) 尚未初始化或与该代币不匹配。")
        sys.exit(0)

    state = lfg.get_token_state(w3, ctx.hook, ctx.pool_id, expected_token=token)
    price = lfg.get_effective_price_bnb_per_token(w3, ctx.hook, ctx.pool_id, price_scale=ctx.price_scale)

    print("live_trade: true")
    print("state_token:", state.get("token"))
    print("totalFeeBps:", state.get("totalFeeBps"))
    print("有效价格 BNB/token:", price)

    est = lfg.estimate_buy(w3, ctx.hook, ctx.pool_id, int(0.001 * 1e18), wallet, protocol_version=ctx.protocol_version)
    tokens_out, platform_fee, third_fee = est
    print("估算买入 0.001 BNB:")
    print(f"  tokensOut:    {tokens_out / 1e18:.18f}")
    print(f"  platformFee:  {platform_fee / 1e18:.18f} BNB")
    if str(ctx.protocol_version).lower().startswith("v3"):
        print(f"  floorBoostFee:{third_fee / 1e18:.18f} BNB")
    else:
        print(f"  inviterFee:   {third_fee / 1e18:.18f} BNB")
    print(f"  totalFee:     {(platform_fee + third_fee) / 1e18:.18f} BNB")


if __name__ == "__main__":
    main()
