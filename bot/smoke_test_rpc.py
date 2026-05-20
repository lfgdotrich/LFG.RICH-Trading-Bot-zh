from __future__ import annotations
import os
from dotenv import load_dotenv

from bot.logging_setup import setup_logging
from bot.rpc import make_web3

def main() -> None:
    setup_logging()
    load_dotenv()

    rpc_url = os.environ.get("BSC_RPC_URL", "").strip()
    if not rpc_url:
        raise RuntimeError(".env 中缺少 BSC_RPC_URL")

    w3 = make_web3(rpc_url, request_timeout_sec=20)

    chain_id = w3.eth.chain_id
    latest = w3.eth.block_number
    print(f"已连接 ✅  chain_id={chain_id}  latest_block={latest}")

if __name__ == "__main__":
    main()
