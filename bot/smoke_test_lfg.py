from __future__ import annotations

from web3 import Web3

from bot.config import load_config
from bot.onchain import lfg
from bot.rpc import make_web3


def main() -> None:
    cfg = load_config()
    w3 = make_web3(cfg.rpc_url, cfg.request_timeout_sec)
    token = Web3.to_checksum_address(cfg.watch_tokens[0].address)
    pool_id = lfg.get_pool_id(w3, cfg.lfg_hook, token)
    state = lfg.get_token_state(w3, cfg.lfg_hook, pool_id)
    price = lfg.get_effective_price_bnb_per_token(w3, cfg.lfg_hook, pool_id)
    print("代币:", token)
    print("pool_id:", pool_id)
    print("是否已初始化:", state["initialized"])
    print("totalFeeBps:", state["totalFeeBps"])
    print("有效价格 BNB/token:", price)
    est = lfg.estimate_buy(w3, cfg.lfg_hook, pool_id, int(0.001 * 1e18))
    tokens_out, platform_fee, floor_boost_fee = est
    print("预估买入 0.001 BNB:")
    print(f"  tokensOut:     {tokens_out / 1e18:.18f}")
    print(f"  platformFee:   {platform_fee / 1e18:.18f} BNB")
    print(f"  floorBoostFee: {floor_boost_fee / 1e18:.18f} BNB")
    print(f"  totalFee:      {(platform_fee + floor_boost_fee) / 1e18:.18f} BNB")


if __name__ == "__main__":
    main()
