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
    status: str               # "DRY_RUN" | "SENT" | "SKIPPED" | "ERROR"，状态值不要翻译
    action: str               # "BUY" | "SELL" | "APPROVE"，动作值不要翻译
    tx_hash: Optional[str]
    note: str


def _fmt_amount(raw: int, decimals: int, places: int = 6) -> str:
    if decimals <= 0:
        return f"{raw:,d}"
    val = raw / (10 ** int(decimals))
    return f"{val:,.{places}f}"


class TraderLFG:
    """LFG.RICH buy/sell execution using the official LFG SwapRouter."""

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
        self._pool_key_cache: dict[str, tuple[str, str, int, int, str]] = {}
        self._pool_id_cache: dict[str, str] = {}

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
            raise RuntimeError("SignedTransaction 缺少原始交易字节属性")
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

    def pool_key(self, token: str) -> tuple[str, str, int, int, str]:
        token = Web3.to_checksum_address(token)
        if token not in self._pool_key_cache:
            self._pool_key_cache[token] = lfg.get_pool_key(self.w3, self.factory, token)
        return self._pool_key_cache[token]

    def pool_id(self, token: str) -> str:
        token = Web3.to_checksum_address(token)
        if token not in self._pool_id_cache:
            self._pool_id_cache[token] = lfg.get_pool_id(self.w3, self.hook, token)
        return self._pool_id_cache[token]

    def ensure_allowance_or_approve_max(self, token: str, amount_in_raw: int, dry_run: bool) -> ExecResult:
        token = Web3.to_checksum_address(token)
        current = int(erc20.allowance(self.w3, token, self.wallet, self.router))
        if current >= int(amount_in_raw):
            return ExecResult("SKIPPED", "APPROVE", None, "allowance ok")

        max_uint = (1 << 256) - 1
        if dry_run:
            return ExecResult("DRY_RUN", "APPROVE", None, f"将授权 LFG SwapRouter 最大额度（当前={current}）")

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
            return ExecResult("SENT", "APPROVE", tx_hash, f"已授权 LFG SwapRouter 最大额度（之前={current}）")
        except Exception as e:
            return ExecResult("ERROR", "APPROVE", None, f"授权失败: {e}")

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
            pool_id = self.pool_id(token)
            tokens_out, platform_fee, floor_boost_fee = lfg.estimate_buy(self.w3, self.hook, pool_id, amount_in_wei)
            min_tokens_out = int(tokens_out * self._slippage_mult())
            note = (
                f"LFG 买入 in={trade_bnb:.6f} BNB "
                f"min_out≈{_fmt_amount(min_tokens_out, token_decimals)} token "
                f"platform_fee={platform_fee / 1e18:.8f} floor_boost={floor_boost_fee / 1e18:.8f}"
            )
            if min_tokens_out <= 0:
                return ExecResult("SKIPPED", "BUY", None, f"estimateBuy 返回 0；{note}")
            if dry_run:
                return ExecResult("DRY_RUN", "BUY", None, note)

            nonce = self.w3.eth.get_transaction_count(self.wallet)
            fn = self.router_contract.functions.buy(self.pool_key(token), int(min_tokens_out))
            tx = fn.build_transaction({**self._base_tx_fields(nonce), "value": int(amount_in_wei)})
            tx_hash = self._sign_and_send(tx)
            return ExecResult("SENT", "BUY", tx_hash, note)
        except Exception as e:
            return ExecResult("ERROR", "BUY", None, f"LFG 买入失败: {e}")

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
            return ExecResult("SKIPPED", "SELL", None, "amount_in_raw 为 0")

        try:
            pool_id = self.pool_id(token)
            eth_out, platform_fee, floor_boost_fee = lfg.estimate_sell(self.w3, self.hook, pool_id, amount_in_raw)
            min_eth_out = int(eth_out * self._slippage_mult())
            note = (
                f"LFG 卖出 in≈{_fmt_amount(amount_in_raw, token_decimals)} token "
                f"min_out≈{min_eth_out / 1e18:.8f} BNB "
                f"platform_fee={platform_fee / 1e18:.8f} floor_boost={floor_boost_fee / 1e18:.8f}"
            )
            if capped:
                note += " [已限制为钱包余额]"
            if min_eth_out <= 0:
                return ExecResult("SKIPPED", "SELL", None, f"estimateSell 返回 0；{note}")
            if dry_run:
                return ExecResult("DRY_RUN", "SELL", None, note)

            nonce = self.w3.eth.get_transaction_count(self.wallet)
            fn = self.router_contract.functions.sell(self.pool_key(token), int(amount_in_raw), int(min_eth_out))
            tx = fn.build_transaction(self._base_tx_fields(nonce))
            tx_hash = self._sign_and_send(tx)
            return ExecResult("SENT", "SELL", tx_hash, note)
        except Exception as e:
            return ExecResult("ERROR", "SELL", None, f"LFG 卖出失败: {e}")

    def try_get_receipt_status(self, tx_hash_hex: str) -> Optional[int]:
        try:
            r = self.w3.eth.get_transaction_receipt(tx_hash_hex)
            if r is None:
                return None
            return int(r.get("status", 1))
        except Exception:
            return None
