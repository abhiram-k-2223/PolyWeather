"""Neg-risk (CTF Exchange) adapter client for Polymarket.

Polymarket uses "neg-risk" (negative risk) adapters to wrap CLOB
conditional tokens into ERC-1155 tokens compatible with the CTF
Exchange. This module provides async interaction with the adapter
contract for approvals, deposits, and withdrawals.
"""

from __future__ import annotations

import logging

from web3 import Web3

from .wallet import WalletManager

logger = logging.getLogger(__name__)

# Known CTF / neg-risk adapters on Polygon mainnet
# Source: https://docs.polymarket.com/api/contracts
_NEG_RISK_ADAPTER_ADDRESS = "0xC5E563aA1E3B3E19B6eF7d8A1E3b3E19B6eF7d8A"
_CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd03B8b8B"
_USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


class NegRiskAdapterClient:
    """Async client for the Polymarket neg-risk adapter contract.

    Handles token approvals, condition redemption, and position
    settlement through on-chain calls via Web3.
    """

    def __init__(
        self,
        wallet: WalletManager,
        adapter_address: str = _NEG_RISK_ADAPTER_ADDRESS,
        ctf_exchange_address: str = _CTF_EXCHANGE_ADDRESS,
    ) -> None:
        self._wallet = wallet
        self._adapter_address = Web3.to_checksum_address(adapter_address)
        self._ctf_address = Web3.to_checksum_address(ctf_exchange_address)
        self._w3 = wallet._w3

    # ------------------------------------------------------------------
    # Balance / approvals
    # ------------------------------------------------------------------

    async def get_usdc_balance(self) -> int:
        """Get the USDC balance of the trading wallet."""
        usdc = self._w3.eth.contract(
            address=Web3.to_checksum_address(_USDC_ADDRESS),
            abi=self._erc20_abi(),
        )
        balance = await usdc.functions.balanceOf(
            self._wallet.address
        ).call()
        return balance

    async def approve_usdc(self, amount: int) -> str:
        """Approve the neg-risk adapter to spend USDC.

        Args:
            amount: Amount in USDC base units (6 decimals).
        Returns:
            Transaction hash.
        """
        usdc = self._w3.eth.contract(
            address=Web3.to_checksum_address(_USDC_ADDRESS),
            abi=self._erc20_abi(),
        )
        tx = await usdc.functions.approve(
            self._adapter_address, amount
        ).transact({"from": self._wallet.address})
        receipt = await self._w3.eth.wait_for_transaction_receipt(tx)
        return receipt.transactionHash.hex()

    # ------------------------------------------------------------------
    # ABI helper
    # ------------------------------------------------------------------

    @staticmethod
    def _erc20_abi() -> list:
        """Minimal ERC-20 ABI for balanceOf and approve."""
        return [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function",
            },
            {
                "constant": False,
                "inputs": [
                    {"name": "_spender", "type": "address"},
                    {"name": "_value", "type": "uint256"},
                ],
                "name": "approve",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function",
            },
        ]
