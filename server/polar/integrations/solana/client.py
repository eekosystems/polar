import base64
import struct
from datetime import datetime
from typing import Any

import httpx
import structlog

from polar.config import settings

log = structlog.get_logger()


class SolanaRPCError(Exception):
    """Error from Solana RPC."""

    def __init__(self, message: str, code: int | None = None):
        self.message = message
        self.code = code
        super().__init__(message)


class SolanaClient:
    """
    Async client for Solana RPC.

    Uses httpx for async HTTP requests to the Solana JSON-RPC API.
    """

    def __init__(self, rpc_url: str | None = None):
        if rpc_url:
            self.rpc_url = rpc_url
        elif settings.SOLANA_USE_DEVNET:
            self.rpc_url = settings.SOLANA_DEVNET_RPC_URL
        else:
            self.rpc_url = settings.SOLANA_RPC_URL

    async def _request(
        self, method: str, params: list[Any] | None = None
    ) -> dict[str, Any]:
        """Make a JSON-RPC request to Solana."""
        async with httpx.AsyncClient() as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or [],
            }

            response = await client.post(
                self.rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()

            if "error" in result:
                error = result["error"]
                raise SolanaRPCError(
                    message=error.get("message", "Unknown RPC error"),
                    code=error.get("code"),
                )

            return result.get("result", {})

    async def get_latest_blockhash(self) -> dict[str, Any]:
        """Get the latest blockhash for transaction building."""
        result = await self._request(
            "getLatestBlockhash", [{"commitment": "finalized"}]
        )
        return result.get("value", {})

    async def get_signatures_for_address(
        self,
        address: str,
        limit: int = 10,
        before: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get signatures for transactions involving an address.

        Used to find payments made with a specific reference.
        """
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before
        if until:
            params["until"] = until

        result = await self._request("getSignaturesForAddress", [address, params])
        return result if isinstance(result, list) else []

    async def get_transaction(
        self, signature: str, max_supported_version: int = 0
    ) -> dict[str, Any] | None:
        """Get a transaction by signature."""
        result = await self._request(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": max_supported_version,
                    "commitment": "confirmed",
                },
            ],
        )
        return result

    async def get_balance(self, address: str) -> int:
        """Get SOL balance for an address in lamports."""
        result = await self._request(
            "getBalance", [address, {"commitment": "confirmed"}]
        )
        return result.get("value", 0)

    async def get_token_account_balance(self, token_account: str) -> dict[str, Any]:
        """Get SPL token balance for a token account."""
        result = await self._request(
            "getTokenAccountBalance", [token_account, {"commitment": "confirmed"}]
        )
        return result.get("value", {})

    async def get_token_accounts_by_owner(
        self, owner: str, mint: str
    ) -> list[dict[str, Any]]:
        """Get all token accounts for an owner and mint."""
        result = await self._request(
            "getTokenAccountsByOwner",
            [
                owner,
                {"mint": mint},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )
        return result.get("value", [])

    async def send_transaction(
        self, signed_transaction: str, skip_preflight: bool = False
    ) -> str:
        """
        Send a signed transaction.

        Returns the transaction signature.
        """
        result = await self._request(
            "sendTransaction",
            [
                signed_transaction,
                {
                    "encoding": "base64",
                    "skipPreflight": skip_preflight,
                    "preflightCommitment": "confirmed",
                },
            ],
        )
        return result

    async def confirm_transaction(
        self, signature: str, commitment: str = "confirmed"
    ) -> dict[str, Any]:
        """Check if a transaction is confirmed."""
        result = await self._request(
            "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
        )
        statuses = result.get("value", [])
        if statuses and statuses[0]:
            return statuses[0]
        return {}

    async def get_account_info(self, address: str) -> dict[str, Any] | None:
        """Get account info for an address."""
        result = await self._request(
            "getAccountInfo",
            [address, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
        return result.get("value")

    async def is_valid_address(self, address: str) -> bool:
        """Check if an address is valid by querying account info."""
        try:
            # A valid address should be 32-44 characters base58
            if not address or len(address) < 32 or len(address) > 44:
                return False
            # Try to get account info - will fail for invalid addresses
            await self.get_account_info(address)
            return True
        except SolanaRPCError:
            return False
        except Exception:
            return False


# Global client instance
def get_solana_client() -> SolanaClient:
    """Get a Solana client configured for the current environment."""
    return SolanaClient()
