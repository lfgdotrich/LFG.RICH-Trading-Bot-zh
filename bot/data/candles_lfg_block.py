from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from bot.onchain.lfg import LFGTradeLog, V5_PRICE_SCALE


@dataclass
class Candle:
    bucket: int
    open: float
    high: float
    low: float
    close: float
    volume_token: float


def trades_to_block_candles(
    *,
    trades: List[LFGTradeLog],
    blocks_per_candle: int,
    token_decimals: int = 18,
    price_scale: int = V5_PRICE_SCALE,
) -> List[Candle]:
    """Convert Hook Buy/Sell events into block-bucket candles.

    V5 trade events emit newPrice scaled by 1e22. Older bot builds divided by
    1e18, which made V5 candles 10,000x too large. The event object carries the
    scale, but the function also accepts an explicit fallback for safety.
    """
    candles: Dict[int, Candle] = {}
    scale = 10 ** int(token_decimals)

    for tr in trades:
        if int(tr.new_price) <= 0:
            continue
        tr_price_scale = int(getattr(tr, "price_scale", 0) or price_scale or V5_PRICE_SCALE)
        price = int(tr.new_price) / float(tr_price_scale)
        vol_token = int(tr.token_amount) / scale
        if price <= 0:
            continue

        bucket = int(tr.block_number) // int(blocks_per_candle)
        c = candles.get(bucket)
        if c is None:
            candles[bucket] = Candle(
                bucket=bucket,
                open=price,
                high=price,
                low=price,
                close=price,
                volume_token=vol_token,
            )
        else:
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            c.volume_token += vol_token

    return [candles[k] for k in sorted(candles.keys())]
