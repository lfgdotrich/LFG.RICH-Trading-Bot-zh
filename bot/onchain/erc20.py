from __future__ import annotations
from web3 import Web3
from web3.contract import Contract

ERC20_ABI = [
    {"name":"decimals","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint8"}]},
    {"name":"symbol","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"string"}]},
    {"name":"balanceOf","type":"function","stateMutability":"view","inputs":[{"name":"owner","type":"address"}],"outputs":[{"type":"uint256"}]},
    {"name":"allowance","type":"function","stateMutability":"view","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"type":"uint256"}]},
    {"name":"approve","type":"function","stateMutability":"nonpayable","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"type":"bool"}]},
]

def contract(w3: Web3, token_address: str) -> Contract:
    return w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)

def decimals(w3: Web3, token_address: str) -> int:
    return int(contract(w3, token_address).functions.decimals().call())

def symbol(w3: Web3, token_address: str) -> str:
    return str(contract(w3, token_address).functions.symbol().call())

def balance_of(w3: Web3, token_address: str, owner: str) -> int:
    return int(contract(w3, token_address).functions.balanceOf(Web3.to_checksum_address(owner)).call())

def allowance(w3: Web3, token_address: str, owner: str, spender: str) -> int:
    return int(contract(w3, token_address).functions.allowance(
        Web3.to_checksum_address(owner),
        Web3.to_checksum_address(spender),
    ).call())

def build_approve_tx(
    w3: Web3,
    token_address: str,
    owner: str,
    spender: str,
    amount_wei: int,
    nonce: int,
    gas_limit: int,
    gas_price_wei: int,
) -> dict:
    c = contract(w3, token_address)
    fn = c.functions.approve(Web3.to_checksum_address(spender), int(amount_wei))
    tx = fn.build_transaction({
        "from": Web3.to_checksum_address(owner),
        "nonce": int(nonce),
        "gas": int(gas_limit),
        "gasPrice": int(gas_price_wei),
    })
    return tx
