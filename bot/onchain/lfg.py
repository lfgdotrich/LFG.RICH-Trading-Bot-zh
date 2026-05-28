from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

try:
    from eth_abi import encode as abi_encode
except Exception:  # eth-abi<3
    from eth_abi import encode_abi as abi_encode
from eth_abi import decode as abi_decode
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

WEI_SCALE = 10 ** 18
V3_PRICE_SCALE = 10 ** 18
V5_PRICE_SCALE = 10 ** 22
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ZERO_BYTES32 = "0x" + "0" * 64

# 机器人是独立的：不得调用网站来发现代币元数据。
# 每个代币都在链上解析。解析器会先向代币询问它的
# FACTORY()、hook() 和 poolId()（如果这些方法存在），然后回退到
# 解析出的 Factory/Hook 映射。这同时适用于较新的 V5 代币和较旧的
# 代币，而不需要硬编码单独的旧版合约列表。

FACTORY_ABI = [
    {"type": "function", "name": "hook", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "poolManager", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "POOL_FEE", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint24"}]},
    {"type": "function", "name": "TICK_SPACING", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "int24"}]},
    {
        "type": "function",
        "name": "getTokenInfo",
        "stateMutability": "view",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "tokenAddress", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "creator", "type": "address"},
                    {"name": "name", "type": "string"},
                    {"name": "symbol", "type": "string"},
                    {"name": "poolId", "type": "bytes32"},
                ],
            }
        ],
    },
    {
        "type": "function",
        "name": "tokenInfoMap",
        "stateMutability": "view",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [
            {"name": "tokenAddress", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "creator", "type": "address"},
            {"name": "name", "type": "string"},
            {"name": "symbol", "type": "string"},
            {"name": "poolId", "type": "bytes32"},
        ],
    },
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

HOOK_COMMON_ABI = [
    {"type": "function", "name": "FACTORY", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "TOTAL_FEE_BPS", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "POOL_FEE", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint24"}]},
    {"type": "function", "name": "tokenToPoolId", "stateMutability": "view", "inputs": [{"name": "token", "type": "address"}], "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "getEffectivePrice", "stateMutability": "view", "inputs": [{"name": "poolId", "type": "bytes32"}], "outputs": [{"name": "", "type": "uint256"}]},
    {
        "type": "function",
        "name": "estimateBuy",
        "stateMutability": "view",
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "ethIn", "type": "uint256"},
            {"name": "buyer", "type": "address"},
        ],
        "outputs": [
            {"name": "tokensOut", "type": "uint256"},
            {"name": "platformFee", "type": "uint256"},
            {"name": "inviterFee", "type": "uint256"},
        ],
    },
]

HOOK_V5_STATE_ABI = HOOK_COMMON_ABI + [
    {
        "type": "function",
        "name": "tokenStates",
        "stateMutability": "view",
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "outputs": [
            {"name": "token", "type": "address"},
            {"name": "creator", "type": "address"},
            {"name": "floorPrice", "type": "uint256"},
            {"name": "realETH", "type": "uint256"},
            {"name": "virtualETH", "type": "uint256"},
            {"name": "totalBorrowedETH", "type": "uint256"},
            {"name": "collateralSupply", "type": "uint256"},
            {"name": "allTimeHighPrice", "type": "uint256"},
            {"name": "k", "type": "uint256"},
            {"name": "autoFloorPctBps", "type": "uint256"},
            {"name": "initialized", "type": "bool"},
        ],
    },
    {
        "type": "function",
        "name": "estimateSell",
        "stateMutability": "view",
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "tokenAmount", "type": "uint256"},
            {"name": "seller", "type": "address"},
        ],
        "outputs": [
            {"name": "ethOut", "type": "uint256"},
            {"name": "platformFee", "type": "uint256"},
            {"name": "inviterFee", "type": "uint256"},
        ],
    },
]

HOOK_V3_STATE_ABI = [
    {"type": "function", "name": "tokenToPoolId", "stateMutability": "view", "inputs": [{"name": "token", "type": "address"}], "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "getEffectivePrice", "stateMutability": "view", "inputs": [{"name": "poolId", "type": "bytes32"}], "outputs": [{"name": "", "type": "uint256"}]},
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
        "name": "estimateBuy",
        "stateMutability": "view",
        "inputs": [{"name": "poolId", "type": "bytes32"}, {"name": "ethIn", "type": "uint256"}],
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
        "inputs": [{"name": "poolId", "type": "bytes32"}, {"name": "tokenAmount", "type": "uint256"}],
        "outputs": [
            {"name": "ethOut", "type": "uint256"},
            {"name": "platformFee", "type": "uint256"},
            {"name": "floorBoostFee", "type": "uint256"},
        ],
    },
]

TOKEN_ABI = [
    {"type": "function", "name": "FACTORY", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "factory", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "hook", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "HOOK", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "poolId", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "poolID", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "POOL_ID", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "pool_id", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "bytes32"}]},
]


def _hex0x(value: Any) -> str:
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
    side: str
    pool_id: str
    trader: str
    eth_amount: int
    token_amount: int
    new_price: int
    price_scale: int = V5_PRICE_SCALE


@dataclass(frozen=True)
class TokenContext:
    token: str
    factory: str
    hook: str
    pool_id: str
    pool_key: Tuple[str, str, int, int, str]
    protocol_version: str
    price_scale: int
    initialized: bool
    metadata_source: str
    state: Dict[str, Any]


def _topic_to_address(topic: Any) -> str:
    b = HexBytes(topic)
    return Web3.to_checksum_address("0x" + b[-20:].hex())


def _pool_topic(pool_id: Union[str, bytes, HexBytes]) -> str:
    if isinstance(pool_id, str):
        v = pool_id.strip()
        if not v.startswith("0x"):
            v = "0x" + v
    else:
        v = _hex0x(pool_id)
    if len(v) != 66:
        raise ValueError("pool_id 必须是 bytes32 / 32 字节，收到 %r" % v)
    return v.lower()


def normalize_pool_id(pool_id: Union[str, bytes, HexBytes, None]) -> str:
    if pool_id is None:
        return ""
    try:
        v = _pool_topic(pool_id)
        return "" if v == ZERO_BYTES32 else v
    except Exception:
        return ""


def _is_zero_address(addr: Any) -> bool:
    try:
        return Web3.to_checksum_address(addr) == Web3.to_checksum_address(ZERO_ADDRESS)
    except Exception:
        return True


def _is_zero_pool_id(pool_id: Union[str, bytes, HexBytes, None]) -> bool:
    return normalize_pool_id(pool_id) == ""


def _checksum_or_none(addr: Any) -> Optional[str]:
    try:
        if _is_zero_address(addr):
            return None
        return Web3.to_checksum_address(addr)
    except Exception:
        return None


def _call_optional(contract: Contract, fn_name: str, *args: Any) -> Any:
    try:
        fn = getattr(contract.functions, fn_name)
        return fn(*args).call()
    except Exception:
        return None


def factory_contract(w3: Web3, factory_addr: str) -> Contract:
    return w3.eth.contract(address=Web3.to_checksum_address(factory_addr), abi=FACTORY_ABI)


def hook_contract(w3: Web3, hook_addr: str, version: str = "v5") -> Contract:
    abi = HOOK_V3_STATE_ABI if str(version).lower().startswith("v3") else HOOK_V5_STATE_ABI
    return w3.eth.contract(address=Web3.to_checksum_address(hook_addr), abi=abi)


def token_contract(w3: Web3, token_addr: str) -> Contract:
    return w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=TOKEN_ABI)


def get_token_declared_factory(w3: Web3, token: str) -> Optional[str]:
    c = token_contract(w3, token)
    for name in ("FACTORY", "factory"):
        addr = _checksum_or_none(_call_optional(c, name))
        if addr:
            return addr
    return None


def get_token_declared_hook(w3: Web3, token: str) -> Optional[str]:
    c = token_contract(w3, token)
    for name in ("hook", "HOOK"):
        addr = _checksum_or_none(_call_optional(c, name))
        if addr:
            return addr
    return None


def get_token_declared_pool_id(w3: Web3, token: str) -> str:
    c = token_contract(w3, token)
    for name in ("poolId", "poolID", "POOL_ID", "pool_id"):
        pool_id = normalize_pool_id(_call_optional(c, name))
        if pool_id:
            return pool_id
    return ""


def get_factory_declared_hook(w3: Web3, factory_addr: str) -> Optional[str]:
    try:
        return _checksum_or_none(factory_contract(w3, factory_addr).functions.hook().call())
    except Exception:
        return None


def get_pool_constants(w3: Web3, factory_addr: Optional[str] = None, hook_addr: Optional[str] = None) -> Tuple[int, int]:
    fee = 3000
    tick_spacing = 60
    if factory_addr:
        f = factory_contract(w3, factory_addr)
        try:
            fee = int(f.functions.POOL_FEE().call())
        except Exception:
            pass
        try:
            tick_spacing = int(f.functions.TICK_SPACING().call())
        except Exception:
            pass
    if hook_addr:
        h = hook_contract(w3, hook_addr, "v5")
        try:
            fee = int(h.functions.POOL_FEE().call())
        except Exception:
            pass
    return fee, tick_spacing


def make_pool_key(token: str, hook_addr: str, pool_fee: int = 3000, tick_spacing: int = 60) -> Tuple[str, str, int, int, str]:
    return (
        Web3.to_checksum_address(ZERO_ADDRESS),
        Web3.to_checksum_address(token),
        int(pool_fee),
        int(tick_spacing),
        Web3.to_checksum_address(hook_addr),
    )


def _normalize_pool_key(key: Any) -> Tuple[str, str, int, int, str]:
    return (
        Web3.to_checksum_address(key[0]),
        Web3.to_checksum_address(key[1]),
        int(key[2]),
        int(key[3]),
        Web3.to_checksum_address(key[4]),
    )


def get_pool_key(w3: Web3, factory_addr: str, token: str, hook_addr: Optional[str] = None) -> Tuple[str, str, int, int, str]:
    token = Web3.to_checksum_address(token)
    if factory_addr:
        try:
            key = factory_contract(w3, factory_addr).functions.getPoolKey(token).call()
            return _normalize_pool_key(key)
        except Exception:
            pass
    if not hook_addr:
        hook_addr = get_token_declared_hook(w3, token)
    if not hook_addr:
        raise RuntimeError("无法解析 PoolKey：代币 %s 缺少 hook" % token)
    fee, tick_spacing = get_pool_constants(w3, factory_addr, hook_addr)
    return make_pool_key(token, hook_addr, fee, tick_spacing)


def derive_pool_id_from_pool_key(pool_key: Tuple[str, str, int, int, str]) -> str:
    encoded = abi_encode(
        ["address", "address", "uint24", "int24", "address"],
        [
            Web3.to_checksum_address(pool_key[0]),
            Web3.to_checksum_address(pool_key[1]),
            int(pool_key[2]),
            int(pool_key[3]),
            Web3.to_checksum_address(pool_key[4]),
        ],
    )
    return _hex0x(Web3.keccak(encoded))


def get_token_info_pool_id(w3: Web3, factory_addr: str, token: str) -> str:
    token = Web3.to_checksum_address(token)
    f = factory_contract(w3, factory_addr)
    try:
        info = f.functions.getTokenInfo(token).call()
        pool_id = normalize_pool_id(info[5])
        if pool_id:
            return pool_id
    except Exception:
        pass
    try:
        info = f.functions.tokenInfoMap(token).call()
        pool_id = normalize_pool_id(info[5])
        if pool_id:
            return pool_id
    except Exception:
        pass
    return ""


def get_hook_token_to_pool_id(w3: Web3, hook_addr: str, token: str) -> str:
    try:
        pool_id = hook_contract(w3, hook_addr, "v5").functions.tokenToPoolId(Web3.to_checksum_address(token)).call()
        return normalize_pool_id(pool_id)
    except Exception:
        return ""


def get_pool_id(
    w3: Web3,
    hook_addr: Optional[str],
    token: str,
    factory_addr: Optional[str] = None,
    strict: bool = True,
    configured_pool_id: Optional[str] = None,
) -> str:
    token = Web3.to_checksum_address(token)

    pool_id = normalize_pool_id(configured_pool_id)
    if pool_id:
        return pool_id

    pool_id = get_token_declared_pool_id(w3, token)
    if pool_id:
        return pool_id

    resolved_factory = get_token_declared_factory(w3, token) or (Web3.to_checksum_address(factory_addr) if factory_addr else None)
    resolved_hook = get_token_declared_hook(w3, token) or None
    if not resolved_hook and resolved_factory:
        resolved_hook = get_factory_declared_hook(w3, resolved_factory)
    if not resolved_hook and hook_addr:
        resolved_hook = Web3.to_checksum_address(hook_addr)

    if resolved_factory:
        pool_id = get_token_info_pool_id(w3, resolved_factory, token)
        if pool_id:
            return pool_id

    if resolved_hook:
        pool_id = get_hook_token_to_pool_id(w3, resolved_hook, token)
        if pool_id:
            return pool_id

    if resolved_factory and resolved_hook:
        try:
            return derive_pool_id_from_pool_key(get_pool_key(w3, resolved_factory, token, resolved_hook))
        except Exception:
            pass

    if strict:
        raise RuntimeError("未能在链上找到代币 poolId：%s" % token)
    return ""


def get_total_fee_bps(w3: Web3, hook_addr: str) -> int:
    try:
        return int(hook_contract(w3, hook_addr, "v5").functions.TOTAL_FEE_BPS().call())
    except Exception:
        return 125


def _state_v5(w3: Web3, hook_addr: str, pool_id: Union[str, bytes, HexBytes]) -> Optional[Dict[str, Any]]:
    pool_id = _pool_topic(pool_id)
    try:
        s = hook_contract(w3, hook_addr, "v5").functions.tokenStates(pool_id).call()
        token_addr = Web3.to_checksum_address(s[0]) if not _is_zero_address(s[0]) else ZERO_ADDRESS
        creator_addr = Web3.to_checksum_address(s[1]) if not _is_zero_address(s[1]) else ZERO_ADDRESS
        creator_int = int(creator_addr, 16) if creator_addr != ZERO_ADDRESS else 0
        auto_floor = int(s[9])
        initialized = bool(s[10])
        # V3 tokenStates 字段数量相同，但索引 1 是
        # totalFeeBps，索引 9 是 ATH。用 V5 ABI 解码会产生
        # 一个很小的假 creator 地址（例如 0x...007d）以及巨大的
        # autoFloorPctBps。拒绝这些结果，避免对 V3 使用 1e22 缩放。
        if initialized and (auto_floor > 10000 or (0 < creator_int < 10_000_000)):
            return None
        real_eth = int(s[3])
        return {
            "token": token_addr,
            "creator": creator_addr,
            "floorPrice": int(s[2]),
            "realETH": real_eth,
            "virtualETH": int(s[4]),
            "totalBorrowedETH": int(s[5]),
            "collateralSupply": int(s[6]),
            "allTimeHighPrice": int(s[7]),
            "k": int(s[8]),
            "autoFloorPctBps": auto_floor,
            "initialized": initialized,
            "totalFeeBps": get_total_fee_bps(w3, hook_addr),
            "floorBoostPool": 0,
            "totalReserveAccumulated": real_eth,
            "priceScale": V5_PRICE_SCALE,
            "protocolVersion": "v5",
        }
    except Exception:
        return None


def _state_v3(w3: Web3, hook_addr: str, pool_id: Union[str, bytes, HexBytes]) -> Optional[Dict[str, Any]]:
    pool_id = _pool_topic(pool_id)
    try:
        s = hook_contract(w3, hook_addr, "v3").functions.tokenStates(pool_id).call()
        token_addr = Web3.to_checksum_address(s[0]) if not _is_zero_address(s[0]) else ZERO_ADDRESS
        total_fee = int(s[1])
        initialized = bool(s[10])
        if total_fee > 10000:
            return None
        return {
            "token": token_addr,
            "creator": ZERO_ADDRESS,
            "totalFeeBps": total_fee,
            "floorPrice": int(s[2]),
            "realETH": int(s[3]),
            "virtualETH": int(s[4]),
            "totalBorrowedETH": int(s[5]),
            "collateralSupply": int(s[6]),
            "floorBoostPool": int(s[7]),
            "totalReserveAccumulated": int(s[8]),
            "allTimeHighPrice": int(s[9]),
            "k": 0,
            "autoFloorPctBps": 0,
            "initialized": initialized,
            "priceScale": V3_PRICE_SCALE,
            "protocolVersion": "v3",
        }
    except Exception:
        return None


def get_token_state(w3: Web3, hook_addr: str, pool_id: Union[str, bytes, HexBytes], expected_token: Optional[str] = None) -> Dict[str, Any]:
    expected = Web3.to_checksum_address(expected_token).lower() if expected_token else None
    for getter in (_state_v5, _state_v3):
        state = getter(w3, hook_addr, pool_id)
        if not state:
            continue
        if expected and str(state.get("token") or "").lower() != expected:
            continue
        return state
    raise RuntimeError("tokenStates(%s) 尚未初始化或与预期代币不匹配" % normalize_pool_id(pool_id))


def is_pool_initialized(w3: Web3, hook_addr: str, pool_id: Union[str, bytes, HexBytes], expected_token: Optional[str] = None) -> bool:
    try:
        state = get_token_state(w3, hook_addr, pool_id, expected_token=expected_token)
        return bool(state.get("initialized"))
    except Exception:
        return False


def resolve_token_context(
    w3: Web3,
    token: str,
    default_factory: Optional[str] = None,
    default_hook: Optional[str] = None,
    configured_pool_id: Optional[str] = None,
) -> TokenContext:
    token = Web3.to_checksum_address(token)

    factory = get_token_declared_factory(w3, token)
    factory_source = "token.FACTORY()" if factory else ""
    if not factory and default_factory:
        factory = Web3.to_checksum_address(default_factory)
        factory_source = "config.lfg.factory 回退"

    hook = get_token_declared_hook(w3, token)
    hook_source = "token.hook()" if hook else ""
    if not hook and factory:
        hook = get_factory_declared_hook(w3, factory)
        hook_source = "factory.hook()" if hook else ""
    if not hook and default_hook:
        hook = Web3.to_checksum_address(default_hook)
        hook_source = "config.lfg.hook 回退"

    if not factory:
        raise RuntimeError("无法解析代币 %s 的 FACTORY()" % token)
    if not hook:
        raise RuntimeError("无法解析代币 %s 的 hook()" % token)

    pool_id = normalize_pool_id(configured_pool_id)
    pool_source = "config.watchlist.pool_id" if pool_id else ""
    if not pool_id:
        pool_id = get_token_declared_pool_id(w3, token)
        pool_source = "token.poolId()" if pool_id else ""
    if not pool_id:
        pool_id = get_token_info_pool_id(w3, factory, token)
        pool_source = "factory 代币信息" if pool_id else ""
    if not pool_id:
        pool_id = get_hook_token_to_pool_id(w3, hook, token)
        pool_source = "hook.tokenToPoolId(token)" if pool_id else ""

    pool_key = get_pool_key(w3, factory, token, hook)
    if not pool_id:
        pool_id = derive_pool_id_from_pool_key(pool_key)
        pool_source = "由 PoolKey 推导"

    state: Dict[str, Any] = {}
    initialized = False
    protocol_version = "unknown"
    price_scale = V5_PRICE_SCALE
    try:
        state = get_token_state(w3, hook, pool_id, expected_token=token)
        initialized = bool(state.get("initialized"))
        protocol_version = str(state.get("protocolVersion") or "unknown")
        price_scale = int(state.get("priceScale") or V5_PRICE_SCALE)
    except Exception:
        # 即使状态尚未初始化，仍保持上下文可用于诊断，
        # 或者代币/池子暂时不可交易。
        state = {"initialized": False, "priceScale": V5_PRICE_SCALE, "protocolVersion": "unknown"}

    return TokenContext(
        token=token,
        factory=factory,
        hook=hook,
        pool_id=normalize_pool_id(pool_id),
        pool_key=pool_key,
        protocol_version=protocol_version,
        price_scale=price_scale,
        initialized=initialized,
        metadata_source=", ".join([x for x in (factory_source, hook_source, pool_source) if x]),
        state=state,
    )


def get_effective_price_raw(w3: Web3, hook_addr: str, pool_id: Union[str, bytes, HexBytes]) -> int:
    pool_id = _pool_topic(pool_id)
    return int(hook_contract(w3, hook_addr, "v5").functions.getEffectivePrice(pool_id).call())


def raw_price_to_bnb_per_token(raw: int, price_scale: int = V5_PRICE_SCALE) -> float:
    return int(raw) / float(price_scale) if int(raw) > 0 else 0.0


def get_effective_price_bnb_per_token(w3: Web3, hook_addr: str, pool_id: Union[str, bytes, HexBytes], price_scale: int = V5_PRICE_SCALE) -> float:
    raw = get_effective_price_raw(w3, hook_addr, pool_id)
    return raw_price_to_bnb_per_token(raw, price_scale)


def estimate_buy(
    w3: Web3,
    hook_addr: str,
    pool_id: Union[str, bytes, HexBytes],
    eth_in_wei: int,
    buyer: Optional[str] = None,
    protocol_version: Optional[str] = None,
) -> Tuple[int, int, int]:
    pool_id = _pool_topic(pool_id)
    buyer_addr = Web3.to_checksum_address(buyer) if buyer else ZERO_ADDRESS
    if str(protocol_version or "").lower().startswith("v3"):
        out = hook_contract(w3, hook_addr, "v3").functions.estimateBuy(pool_id, int(eth_in_wei)).call()
        return int(out[0]), int(out[1]), int(out[2])
    try:
        out = hook_contract(w3, hook_addr, "v5").functions.estimateBuy(pool_id, int(eth_in_wei), buyer_addr).call()
        return int(out[0]), int(out[1]), int(out[2])
    except Exception:
        out = hook_contract(w3, hook_addr, "v3").functions.estimateBuy(pool_id, int(eth_in_wei)).call()
        return int(out[0]), int(out[1]), int(out[2])


def estimate_sell(
    w3: Web3,
    hook_addr: str,
    pool_id: Union[str, bytes, HexBytes],
    token_amount_raw: int,
    seller: Optional[str] = None,
    protocol_version: Optional[str] = None,
) -> Tuple[int, int, int]:
    pool_id = _pool_topic(pool_id)
    seller_addr = Web3.to_checksum_address(seller) if seller else ZERO_ADDRESS
    if str(protocol_version or "").lower().startswith("v3"):
        out = hook_contract(w3, hook_addr, "v3").functions.estimateSell(pool_id, int(token_amount_raw)).call()
        return int(out[0]), int(out[1]), int(out[2])
    try:
        out = hook_contract(w3, hook_addr, "v5").functions.estimateSell(pool_id, int(token_amount_raw), seller_addr).call()
        return int(out[0]), int(out[1]), int(out[2])
    except Exception:
        out = hook_contract(w3, hook_addr, "v3").functions.estimateSell(pool_id, int(token_amount_raw)).call()
        return int(out[0]), int(out[1]), int(out[2])


def _decode_trade_log(log: Dict[str, Any], side: str, price_scale: int = V5_PRICE_SCALE) -> LFGTradeLog:
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
        price_scale=int(price_scale),
    )


def fetch_trades(
    w3: Web3,
    hook_addr: str,
    pool_id: str,
    from_block: int,
    to_block: int,
    *,
    chunk_size: int = 2000,
    price_scale: int = V5_PRICE_SCALE,
) -> list[LFGTradeLog]:
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
        base = {"address": hook, "fromBlock": cur, "toBlock": chunk_to}
        for topic, side in ((BUY_TOPIC, "BUY"), (SELL_TOPIC, "SELL")):
            logs = w3.eth.get_logs({**base, "topics": [topic, pool_topic]})
            out.extend(_decode_trade_log(log, side, price_scale=price_scale) for log in logs)
        cur = chunk_to + 1

    out.sort(key=lambda x: (x.block_number, x.log_index))
    return out
