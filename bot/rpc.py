from __future__ import annotations
import time
import logging
from web3 import Web3

log = logging.getLogger("rpc")

def make_web3(rpc_url: str, request_timeout_sec: int) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": request_timeout_sec}))

    # web3.py v7 在 BSC / PoA 风格链上需要此中间件：
    # 用于修复调用 get_block() 时可能出现的 ExtraDataLengthError。
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except Exception as e:
        # 如果中间件导入路径发生变化，直接报错，方便发现问题。
        raise RuntimeError(f"应用 PoA 中间件失败: {e}")

    if not w3.is_connected():
        raise RuntimeError("连接 RPC 失败（is_connected() 返回 False）")
    return w3

def with_retries(fn, max_retries: int, backoff_sec: float):
    last = None
    for i in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            wait = backoff_sec * (2 ** i)
            log.warning("RPC 错误: %s | %.2f 秒后重试 %d/%d", e, wait, i + 1, max_retries)
            time.sleep(wait)
    raise last
