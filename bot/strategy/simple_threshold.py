from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Decision:
    action: str  # "BUY", "SELL", "HOLD"
    reason: str

def decide(price_token_in_wbnb: float, buy_below: float | None, sell_above: float | None) -> Decision:
    if buy_below is not None and price_token_in_wbnb < buy_below:
        return Decision("BUY", f"价格 {price_token_in_wbnb:.10f} < buy_below {buy_below}")
    if sell_above is not None and price_token_in_wbnb > sell_above:
        return Decision("SELL", f"价格 {price_token_in_wbnb:.10f} > sell_above {sell_above}")
    return Decision("HOLD", "未触发阈值")
