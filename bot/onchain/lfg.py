from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from eth_abi import decode as abi_decode
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

# 机器人使用的 LFG.RICH 协议最小 ABI。
# 完整 ABI 请参考 LFG.RICH 文档仓库。
FACTORY_ABI = [
    {
        "type": "function",
        "name": "getPoolKey",
        "stateMutability": "view",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            }
        ],
    },
]

HOOK_ABI = [
    {
        "type": "function",
        "name": "tokenToPoolId",
        "stateMutability": "view",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "tokenStates",
        "stateMutability": "view",
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "outputs": [
            {"name": "token", "type": "address"},
            {"name": "totalFeeBps", "type": "uint256"},
            {"name": "floorPrice", "type": "uint256"},
            {"name": "realETH", "type": "uint256"},
            {"name": "virtualETH", "type": "uint256"},
            {"name": "totalBorrowedETH", "type": "uint256"},
            {"name": "collateralSupply", "type": "uint256"},
            {"name": "floorBoostPool", "type": "uint256"},
            {"name": "totalReserveAccumulated", "type": "uint256"},
            {"name": "allTimeHighPrice", "type": "uint256"},
            {"name": "initialized", "type": "bool"},
        ],
    },
    {
        "type": "function",
        "name": "getEffectivePrice",
        "stateMutability": "view",
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "estimateBuy",
        "stateMutability": "view",
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "ethIn", "type": "uint256"},
        ],
        "outputs": [
            {"name": "tokensOut", "type": "uint256"},
            {"name": "platformFee", "type": "uint256"},
            {"name": "floorBoostFee", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "estimateSell",
        "stateMutability": "view",
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "tokenAmount", "type": "uint256"},
        ],
        "outputs": [
            {"name": "ethOut", "type": "uint256"},
            {"name": "platformFee", "type": "uint256"},
            {"name": "floorBoostFee", "type": "uint256"},
        ],
    },
]

def _hex0x(value: Any) -> str:
    """返回小写、带 0x 前缀的十六进制字符串。

    某些 web3/hexbytes 版本的 HexBytes.hex() 不带 0x 前缀。
    RPC 过滤器很严格，会拒绝 ``abcd...`` 这种 topic，必须使用
    ``0xabcd...``。
    """
    if isinstance(value, str):
        v = value.strip()
    else:
        v = HexBytes(value).hex()
    if not v.startswith("0x"):
        v = "0x" + v
    return v.lower()


BUY_TOPIC = _hex0x(Web3.keccak(text="Buy(bytes32,address,uint256,uint256,uint256)"))
SELL_TOPIC = _hex0x(Web3.keccak(text="Sell(bytes32,address,uint256,uint256,uint256)"))


@dataclass(frozen=True)
class LFGTradeLog:
    block_number: int
    tx_hash: str
    log_index: int
    side: str  # "BUY" 或 "SELL"，不要翻译这些值
    pool_id: str
    trader: str
    eth_amount: int       # BUY: ethIn，SELL: ethOut
    token_amount: int     # BUY: tokensOut，SELL: tokensIn
    new_price: int        # 事件中的 18 位小数 BNB/token 价格


def _topic_to_address(topic: Any) -> str:
    b = HexBytes(topic)
    return Web3.to_checksum_address("0x" + b[-20:].hex())


def _pool_topic(pool_id: str | bytes | HexBytes) -> str:
    """返回带 0x 前缀的 bytes32 十六进制字符串，用于日志和 ABI 调用。

    web3.py 对 bytes32 参数很严格。像 ``7c22...`` 这种不带前缀的
    64 字符十六进制字符串不会被接受；必须是 ``0x7c22...`` 或原始 bytes。
    统一保留为 0x 前缀格式，也方便直接作为 indexed 事件 topic 使用。
    """
    if isinstance(pool_id, str):
        v = pool_id.strip()
        if not v.startswith("0x"):
            v = "0x" + v
    else:
        v = _hex0x(pool_id)
    if len(v) != 66:
        raise ValueError(f"pool_id 必须是 bytes32 / 32 字节，当前为 {v!r}")
    return v.lower()


def normalize_pool_id(pool_id: str | bytes | HexBytes) -> str:
    return _pool_topic(pool_id)


def factory_contract(w3: Web3, factory_addr: str) -> Contract:
    return w3.eth.contract(address=Web3.to_checksum_address(factory_addr), abi=FACTORY_ABI)


def hook_contract(w3: Web3, hook_addr: str) -> Contract:
    return w3.eth.contract(address=Web3.to_checksum_address(hook_addr), abi=HOOK_ABI)


def get_pool_key(w3: Web3, factory_addr: str, token: str) -> tuple[str, str, int, int, str]:
    key = factory_contract(w3, factory_addr).functions.getPoolKey(Web3.to_checksum_address(token)).call()
    # web3.py 可能返回 tuple/list，这里统一为 build_transaction 可接受的 tuple。
    return (
        Web3.to_checksum_address(key[0]),
        Web3.to_checksum_address(key[1]),
        int(key[2]),
        int(key[3]),
        Web3.to_checksum_address(key[4]),
    )


def get_pool_id(w3: Web3, hook_addr: str, token: str) -> str:
    pool_id = hook_contract(w3, hook_addr).functions.tokenToPoolId(Web3.to_checksum_address(token)).call()
    return normalize_pool_id(pool_id)


def get_token_state(w3: Web3, hook_addr: str, pool_id: str | bytes | HexBytes) -> dict[str, Any]:
    pool_id = normalize_pool_id(pool_id)
    s = hook_contract(w3, hook_addr).functions.tokenStates(pool_id).call()
    return {
        "token": Web3.to_checksum_address(s[0]) if int(s[0], 16) != 0 else s[0],
        "totalFeeBps": int(s[1]),
        "floorPrice": int(s[2]),
        "realETH": int(s[3]),
        "virtualETH": int(s[4]),
        "totalBorrowedETH": int(s[5]),
        "collateralSupply": int(s[6]),
        "floorBoostPool": int(s[7]),
        "totalReserveAccumulated": int(s[8]),
        "allTimeHighPrice": int(s[9]),
        "initialized": bool(s[10]),
    }


def get_effective_price_raw(w3: Web3, hook_addr: str, pool_id: str | bytes | HexBytes) -> int:
    pool_id = normalize_pool_id(pool_id)
    return int(hook_contract(w3, hook_addr).functions.getEffectivePrice(pool_id).call())


def get_effective_price_bnb_per_token(w3: Web3, hook_addr: str, pool_id: str) -> float:
    raw = get_effective_price_raw(w3, hook_addr, pool_id)
    return raw / 1e18 if raw > 0 else 0.0


def estimate_buy(w3: Web3, hook_addr: str, pool_id: str | bytes | HexBytes, eth_in_wei: int) -> tuple[int, int, int]:
    pool_id = normalize_pool_id(pool_id)
    out = hook_contract(w3, hook_addr).functions.estimateBuy(pool_id, int(eth_in_wei)).call()
    return int(out[0]), int(out[1]), int(out[2])


def estimate_sell(w3: Web3, hook_addr: str, pool_id: str | bytes | HexBytes, token_amount_raw: int) -> tuple[int, int, int]:
    pool_id = normalize_pool_id(pool_id)
    out = hook_contract(w3, hook_addr).functions.estimateSell(pool_id, int(token_amount_raw)).call()
    return int(out[0]), int(out[1]), int(out[2])


def _decode_trade_log(log: dict[str, Any], side: str) -> LFGTradeLog:
    topics = log["topics"]
    pool_id = _pool_topic(topics[1])
    trader = _topic_to_address(topics[2])
    a, b, price = abi_decode(["uint256", "uint256", "uint256"], HexBytes(log["data"]))
    txh = _hex0x(log["transactionHash"])
    if side == "BUY":
        eth_amount, token_amount = int(a), int(b)
    else:
        token_amount, eth_amount = int(a), int(b)
    return LFGTradeLog(
        block_number=int(log["blockNumber"]),
        tx_hash=txh,
        log_index=int(log["logIndex"]),
        side=side,
        pool_id=pool_id,
        trader=trader,
        eth_amount=eth_amount,
        token_amount=token_amount,
        new_price=int(price),
    )


def fetch_trades(
    w3: Web3,
    hook_addr: str,
    pool_id: str,
    from_block: int,
    to_block: int,
    *,
    chunk_size: int = 2000,
) -> list[LFGTradeLog]:
    """获取某个 poolId 的 LFG Hook Buy/Sell 事件。"""
    out: list[LFGTradeLog] = []
    hook = Web3.to_checksum_address(hook_addr)
    pool_topic = _pool_topic(pool_id)

    start = int(from_block)
    end = int(to_block)
    if end < start:
        return []

    cur = start
    while cur <= end:
        chunk_to = min(end, cur + int(chunk_size) - 1)
        base = {
            "address": hook,
            "fromBlock": cur,
            "toBlock": chunk_to,
        }
        for topic, side in ((BUY_TOPIC, "BUY"), (SELL_TOPIC, "SELL")):
            logs = w3.eth.get_logs({**base, "topics": [topic, pool_topic]})
            out.extend(_decode_trade_log(log, side) for log in logs)
        cur = chunk_to + 1

    out.sort(key=lambda x: (x.block_number, x.log_index))
    return out
