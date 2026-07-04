"""Wallet and key management for Polymarket trading.

Handles EIP-712 typed data signing for CLOB API authentication,
message signing for order creation/cancellation, and wallet
configuration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from eth_account import Account
from eth_account.messages import SignableMessage, encode_typed_data
from eth_typing import ChecksumAddress
from web3 import Web3

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# EIP-712 domain for Polymarket CLOB
# ------------------------------------------------------------------

_CLOB_EIP712_DOMAIN = {
    "name": "ClobAuthDomain",
    "version": "1",
    "chainId": 137,  # Polygon mainnet (0x89)
}

# For Polygon Amoy testnet:
_CLOB_EIP712_DOMAIN_AMOY = {
    "name": "ClobAuthDomain",
    "version": "1",
    "chainId": 80002,
}


@dataclass
class PolyWalletConfig:
    """Configuration for a Polymarket trading wallet.

    Attributes:
        private_key: Raw hex private key (with or without 0x prefix).
        chain_id: Polygon chain ID (137 mainnet, 80002 amoy).
        rpc_url: JSON-RPC endpoint for the chain.
    """

    private_key: str
    chain_id: int = 137
    rpc_url: str = "https://polygon-rpc.com"


_CLOB_API_URLS = {
    137: "https://clob.polymarket.com",
    80002: "https://clob-staging.polymarket.com",
}

_DATA_API_URLS = {
    137: "https://data-api.polymarket.com",
    80002: "https://data-api-staging.polymarket.com",
}


class WalletManager:
    """Manages a trading wallet: signing, address derivation, auth tokens.

    Usage:
        config = PolyWalletConfig(private_key="0x...")
        wallet = WalletManager(config)
        signature = wallet.sign_typed_data(message)
    """

    def __init__(self, config: PolyWalletConfig) -> None:
        self._config = config
        self._account = Account.from_key(config.private_key)
        self._w3 = Web3(Web3.HTTPProvider(config.rpc_url))
        self._address: ChecksumAddress = self._account.address  # type: ignore

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    @property
    def config(self) -> PolyWalletConfig:
        return self._config

    @property
    def clob_api_url(self) -> str:
        return _CLOB_API_URLS.get(self._config.chain_id, _CLOB_API_URLS[137])

    @property
    def data_api_url(self) -> str:
        return _DATA_API_URLS.get(self._config.chain_id, _DATA_API_URLS[137])

    # ------------------------------------------------------------------
    # CLOB authentication helpers
    # ------------------------------------------------------------------

    def build_clob_auth_message(self, timestamp: int) -> dict:
        """Build the EIP-712 typed data for CLOB API authentication.

        The CLOB API requires a signed 'register' action proving wallet
        ownership. The timestamp is used as a nonce.
        """
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "ClobAuth": [
                    {"name": "action", "type": "string"},
                    {"name": "timestamp", "type": "string"},
                ],
            },
            "domain": (
                _CLOB_EIP712_DOMAIN_AMOY
                if self._config.chain_id == 80002
                else _CLOB_EIP712_DOMAIN
            ),
            "primaryType": "ClobAuth",
            "message": {
                "action": "register",
                "timestamp": str(timestamp),
            },
        }

    def sign_typed_data(self, data: dict) -> str:
        """Sign an EIP-712 typed data dict and return the hex signature."""
        encoded: SignableMessage = encode_typed_data(
            domain_data=data.get("domain", {}),
            message_types=data.get("types", {}),
            message_data=data.get("message", {}),
        )
        signed = self._account.sign_message(encoded)
        return signed.signature.hex()

    def sign_message(self, message: bytes) -> str:
        """Sign an arbitrary bytes message and return the hex signature."""
        from eth_account.messages import encode_defunct

        enc = encode_defunct(primitive=message)
        signed = self._account.sign_message(enc)
        return signed.signature.hex()

    # ------------------------------------------------------------------
    # CLOB API authentication token
    # ------------------------------------------------------------------

    def get_clob_auth_headers(self) -> dict[str, str]:
        """Return headers dict with ``POLY-*`` auth for the CLOB API.

        The CLOB API's auth scheme:
          - ``POLY_ADDRESS``: 0x-prefixed wallet address
          - ``POLY_SIGNATURE``: EIP-712 signature
          - ``POLY_TIMESTAMP``: current unix time as string
        """
        import time

        timestamp = int(time.time())
        msg = self.build_clob_auth_message(timestamp)
        signature = self.sign_typed_data(msg)
        return {
            "POLY_ADDRESS": self._address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(timestamp),
        }

    # ------------------------------------------------------------------
    # Neg-risk (CTF) adapter helpers
    # ------------------------------------------------------------------

    def build_neg_risk_permit_message(
        self,
        adapter_address: str,
        spender: str,
        token_id: int,
        nonce: int,
        deadline: int,
    ) -> dict:
        """Build an EIP-712 permit message for the neg-risk adapter.

        The neg-risk adapter (CTF Exchange) uses ERC-1155 permits to
        approve the exchange to spend condition tokens on the user's
        behalf.
        """
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Permit": [
                    {"name": "spender", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
            "domain": {
                "name": "NegRiskAdapter",
                "version": "1",
                "chainId": self._config.chain_id,
                "verifyingContract": adapter_address,
            },
            "primaryType": "Permit",
            "message": {
                "spender": spender,
                "tokenId": token_id,
                "nonce": nonce,
                "deadline": deadline,
            },
        }

    # ------------------------------------------------------------------
    # factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> WalletManager:
        """Create a WalletManager from environment variables.

        Expects:
          - ``POLY_TRADING_PRIVATE_KEY`` (required)
          - ``POLY_CHAIN_ID`` (optional, default 137)
          - ``POLY_RPC_URL`` (optional)
        """
        pk = os.environ.get("POLY_TRADING_PRIVATE_KEY")
        if not pk:
            raise ValueError(
                "POLY_TRADING_PRIVATE_KEY environment variable is required"
            )
        chain_id = int(os.environ.get("POLY_CHAIN_ID", "137"))
        rpc_url = os.environ.get(
            "POLY_RPC_URL",
            "https://polygon-rpc.com",
        )
        return cls(PolyWalletConfig(private_key=pk, chain_id=chain_id, rpc_url=rpc_url))
