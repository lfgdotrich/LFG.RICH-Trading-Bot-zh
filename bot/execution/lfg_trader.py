from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from web3 import Web3

from bot.onchain import erc20
from bot.onchain import lfg

log = logging.getLogger("bot")

SWAP_ROUTER_ABI = [
    {
        "type": "function",
        "name": "buy",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "key",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "minTokensOut", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "sell",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "key",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "tokenAmount", "type": "uint256"},
            {"name": "minEthOut", "type": "uint256"},
        ],
        "outputs": [],
    },
]


@dataclass
class ExecResult:
    status: str               # "DRY_RUN" | "SENT" | "SKIPPED" | "ERROR"
    action: str               # "BUY" | "SELL" | "APPROVE"
    tx_hash: Optional[str]
    note: str


def _fmt_amount(raw: int, decimals: int, places: int = 6) -> str:
    if decimals <= 0:
        return f"{raw:,d}"
    val = raw / (10 ** int(decimals))
    return f"{val:,.{places}f}"


class TraderLFG:
    """使用官方 LFG SwapRouter 执行 LFG.RICH 买入/卖出。"""

    def __init__(
        self,
        w3: Web3,
        *,
        factory: str,
        hook: str,
        swap_router: str,
        wallet: str,
        private_key: str,
        gas_limit: int,
        slippage_bps: int,
    ) -> None:
        self.w3 = w3
        self.factory = Web3.to_checksum_address(factory)
        self.hook = Web3.to_checksum_address(hook)
        self.router = Web3.to_checksum_address(swap_router)
        self.wallet = Web3.to_checksum_address(wallet)
        self.private_key = private_key.strip()
        self.gas_limit = int(gas_limit)
        self.slippage_bps = int(slippage_bps)
        self.router_contract = self.w3.eth.contract(address=self.router, abi=SWAP_ROUTER_ABI)
        self._chain_id: Optional[int] = None
        self._context_cache: dict[str, lfg.TokenContext] = {}

    def set_token_context(self, token: str, context: Optional[lfg.TokenContext] = None, **kwargs) -> None:
        """注入由 bot.onchain.lfg 解析出的每个代币链上上下文。

        上下文首先来自代币自身：FACTORY()、hook() 和 poolId()。
        这样可以避免硬编码旧版路由，也避免任何网站依赖。
        """
        token = Web3.to_checksum_address(token)
        if context is not None:
            self._context_cache[token] = context
            return
        # 面向旧调用方/测试的向后兼容路径。
        pool_id = kwargs.get("pool_id")
        pool_key = kwargs.get("pool_key")
        factory = Web3.to_checksum_address(kwargs.get("factory") or self.factory)
        hook = Web3.to_checksum_address(kwargs.get("hook") or self.hook)
        if pool_id and pool_key:
            self._context_cache[token] = lfg.TokenContext(
                token=token,
                factory=factory,
                hook=hook,
                pool_id=lfg.normalize_pool_id(pool_id),
                pool_key=pool_key,
                protocol_version=str(kwargs.get("protocol_version") or "unknown"),
                price_scale=int(kwargs.get("price_scale") or lfg.V5_PRICE_SCALE),
                initialized=bool(kwargs.get("initialized", True)),
                metadata_source="caller-provided",
                state={},
            )

    def _get_chain_id(self) -> int:
        if self._chain_id is None:
            self._chain_id = int(self.w3.eth.chain_id)
        return self._chain_id

    def _sign_and_send(self, tx: dict) -> str:
        signed = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            raw = getattr(signed, "rawTransaction", None)
        if raw is None:
            raise RuntimeError("SignedTransaction 没有原始交易字节属性")
        return self.w3.eth.send_raw_transaction(raw).hex()

    def _base_tx_fields(self, nonce: int) -> dict:
        return {
            "from": self.wallet,
            "nonce": int(nonce),
            "gas": self.gas_limit,
            "gasPrice": int(self.w3.eth.gas_price),
            "chainId": self._get_chain_id(),
        }

    def _slippage_mult(self) -> float:
        return max(0.0, 1.0 - (self.slippage_bps / 10_000.0))

    def context(self, token: str) -> lfg.TokenContext:
        token = Web3.to_checksum_address(token)
        if token not in self._context_cache:
            self._context_cache[token] = lfg.resolve_token_context(
                self.w3,
                token,
                default_factory=self.factory,
                default_hook=self.hook,
            )
        return self._context_cache[token]

    def pool_key(self, token: str) -> tuple[str, str, int, int, str]:
        return self.context(token).pool_key

    def pool_id(self, token: str) -> str:
        return self.context(token).pool_id

    def ensure_allowance_or_approve_max(self, token: str, amount_in_raw: int, dry_run: bool) -> ExecResult:
        token = Web3.to_checksum_address(token)
        current = int(erc20.allowance(self.w3, token, self.wallet, self.router))
        if current >= int(amount_in_raw):
            return ExecResult("SKIPPED", "APPROVE", None, "allowance 正常")

        max_uint = (1 << 256) - 1
        if dry_run:
            return ExecResult("DRY_RUN", "APPROVE", None, f"将授权 LFG SwapRouter 最大额度（当前={current})")

        try:
            nonce = self.w3.eth.get_transaction_count(self.wallet)
            tx = erc20.build_approve_tx(
                w3=self.w3,
                token_address=token,
                owner=self.wallet,
                spender=self.router,
                amount_wei=max_uint,
                nonce=nonce,
                gas_limit=self.gas_limit,
                gas_price_wei=int(self.w3.eth.gas_price),
            )
            tx.setdefault("chainId", self._get_chain_id())
            tx_hash = self._sign_and_send(tx)
            return ExecResult("SENT", "APPROVE", tx_hash, f"已授权 LFG SwapRouter 最大额度（之前={current})")
        except Exception as e:
            return ExecResult("ERROR", "APPROVE", None, f"授权失败：{e}")

    def buy_token_with_bnb(
        self,
        token: str,
        trade_bnb: float,
        token_decimals: int,
        price_token_in_wbnb: float,
        dry_run: bool,
    ) -> ExecResult:
        token = Web3.to_checksum_address(token)
        amount_in_wei = int(float(trade_bnb) * 1e18)
        if amount_in_wei <= 0:
            return ExecResult("SKIPPED", "BUY", None, "trade_bnb 太小")

        try:
            ctx = self.context(token)
            pool_id = ctx.pool_id
            tokens_out, platform_fee, inviter_fee = lfg.estimate_buy(
                self.w3, ctx.hook, pool_id, amount_in_wei, self.wallet, protocol_version=ctx.protocol_version
            )
            min_tokens_out = int(tokens_out * self._slippage_mult())
            note = (
                f"LFG 买入 in={trade_bnb:.6f} BNB "
                f"min_out≈{_fmt_amount(min_tokens_out, token_decimals)} token "
                f"fee_platform={platform_fee / 1e18:.8f} inviter_fee={inviter_fee / 1e18:.8f}"
            )
            if min_tokens_out <= 0:
                return ExecResult("SKIPPED", "BUY", None, f"estimateBuy 返回零；{note}")
            if dry_run:
                return ExecResult("DRY_RUN", "BUY", None, note)

            nonce = self.w3.eth.get_transaction_count(self.wallet)
            fn = self.router_contract.functions.buy(self.pool_key(token), int(min_tokens_out))
            tx = fn.build_transaction({**self._base_tx_fields(nonce), "value": int(amount_in_wei)})
            tx_hash = self._sign_and_send(tx)
            return ExecResult("SENT", "BUY", tx_hash, note)
        except Exception as e:
            return ExecResult("ERROR", "BUY", None, f"LFG 买入失败：{e}")

    def sell_token_to_bnb(
        self,
        token: str,
        sell_bnb_value: float,
        token_decimals: int,
        price_token_in_wbnb: float,
        dry_run: bool,
    ) -> ExecResult:
        token = Web3.to_checksum_address(token)
        if sell_bnb_value <= 0:
            return ExecResult("SKIPPED", "SELL", None, "sell_bnb_value 太小")
        if price_token_in_wbnb <= 0:
            return ExecResult("SKIPPED", "SELL", None, "缺少价格")

        tokens_to_sell = float(sell_bnb_value) / float(price_token_in_wbnb)
        amount_in_raw = int(tokens_to_sell * (10 ** int(token_decimals)))

        wallet_bal_raw = int(erc20.balance_of(self.w3, token, self.wallet))
        capped = False
        if amount_in_raw > wallet_bal_raw:
            amount_in_raw = wallet_bal_raw
            capped = True
        if amount_in_raw <= 0:
            return ExecResult("SKIPPED", "SELL", None, "amount_in_raw 为零")

        try:
            ctx = self.context(token)
            pool_id = ctx.pool_id
            eth_out, platform_fee, inviter_fee = lfg.estimate_sell(
                self.w3, ctx.hook, pool_id, amount_in_raw, self.wallet, protocol_version=ctx.protocol_version
            )
            min_eth_out = int(eth_out * self._slippage_mult())
            note = (
                f"LFG 卖出 in≈{_fmt_amount(amount_in_raw, token_decimals)} token "
                f"min_out≈{min_eth_out / 1e18:.8f} BNB "
                f"fee_platform={platform_fee / 1e18:.8f} inviter_fee={inviter_fee / 1e18:.8f}"
            )
            if capped:
                note += " [已限制为钱包余额]"
            if min_eth_out <= 0:
                return ExecResult("SKIPPED", "SELL", None, f"estimateSell 返回零；{note}")
            if dry_run:
                return ExecResult("DRY_RUN", "SELL", None, note)

            nonce = self.w3.eth.get_transaction_count(self.wallet)
            fn = self.router_contract.functions.sell(self.pool_key(token), int(amount_in_raw), int(min_eth_out))
            tx = fn.build_transaction(self._base_tx_fields(nonce))
            tx_hash = self._sign_and_send(tx)
            return ExecResult("SENT", "SELL", tx_hash, note)
        except Exception as e:
            return ExecResult("ERROR", "SELL", None, f"LFG 卖出失败：{e}")

    def try_get_receipt_status(self, tx_hash_hex: str) -> Optional[int]:
        try:
            r = self.w3.eth.get_transaction_receipt(tx_hash_hex)
            if r is None:
                return None
            return int(r.get("status", 1))
        except Exception:
            return None
