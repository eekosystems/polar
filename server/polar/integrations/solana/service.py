import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import structlog

from polar.config import settings
from polar.integrations.solana.client import SolanaClient, SolanaRPCError, get_solana_client
from polar.integrations.solana.schemas import (
    PaymentConfirmationResult,
    PaymentRequestResult,
    SolanaPaymentStatus,
    SUPPORTED_TOKENS,
    TransferResult,
)

log = structlog.get_logger()


class SolanaPayError(Exception):
    """Base error for Solana Pay operations."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class PaymentNotFound(SolanaPayError):
    """Payment reference not found on-chain."""

    pass


class PaymentExpired(SolanaPayError):
    """Payment request has expired."""

    pass


class InvalidAmount(SolanaPayError):
    """Payment amount doesn't match expected."""

    pass


class SolanaPayService:
    """
    Service for Solana Pay integration.

    Handles creating payment requests, verifying payments on-chain,
    and processing payouts to merchant wallets.
    """

    def __init__(self, client: SolanaClient | None = None):
        self.client = client or get_solana_client()

    def _get_token_mint(self, token: str = "USDC") -> str:
        """Get the correct token mint based on environment."""
        if settings.SOLANA_USE_DEVNET and token == "USDC":
            return settings.SOLANA_USDC_DEVNET_MINT
        return SUPPORTED_TOKENS.get(token, SUPPORTED_TOKENS["USDC"]).mint

    def _get_merchant_wallet(self) -> str:
        """Get the platform's merchant wallet address."""
        if not settings.SOLANA_MERCHANT_WALLET:
            raise SolanaPayError("SOLANA_MERCHANT_WALLET not configured")
        return settings.SOLANA_MERCHANT_WALLET

    def _generate_reference(self) -> str:
        """
        Generate a unique reference for tracking a payment.

        In Solana Pay, the reference is a pubkey that gets included
        in the transaction, allowing us to find it later.
        """
        # Generate a random 32-byte value and encode as base58-like string
        random_bytes = secrets.token_bytes(32)
        # Use a simple hex encoding for now - in production would use proper base58
        return random_bytes.hex()[:44]

    def _cents_to_token_amount(self, cents: int, decimals: int = 6) -> float:
        """Convert cents to token amount (e.g., USDC has 6 decimals)."""
        # cents / 100 = dollars, then adjust for token decimals
        return cents / 100

    def _token_amount_to_cents(self, amount: float, decimals: int = 6) -> int:
        """Convert token amount back to cents."""
        return int(amount * 100)

    def create_payment_request(
        self,
        amount_cents: int,
        checkout_id: uuid.UUID,
        label: str = "Payment",
        message: str | None = None,
        token: str = "USDC",
    ) -> PaymentRequestResult:
        """
        Create a Solana Pay payment request.

        This generates a URL that can be displayed as a QR code or used
        as a deep link for wallet apps.

        Args:
            amount_cents: Amount in cents (e.g., 1000 = $10.00)
            checkout_id: The checkout session ID for reference
            label: Label shown in wallet (e.g., merchant name)
            message: Optional memo for the payment
            token: Token to accept (default: USDC)

        Returns:
            PaymentRequestResult with the Solana Pay URL and reference
        """
        recipient = self._get_merchant_wallet()
        reference = self._generate_reference()
        token_mint = self._get_token_mint(token)
        token_info = SUPPORTED_TOKENS.get(token, SUPPORTED_TOKENS["USDC"])

        # Convert cents to token amount
        amount = self._cents_to_token_amount(amount_cents, token_info.decimals)

        # Build memo with checkout ID for reconciliation
        memo = f"polar:{checkout_id}"
        if message:
            memo = f"{memo}|{message}"

        # Build Solana Pay URL
        # Format: solana:<recipient>?amount=<amount>&spl-token=<mint>&reference=<ref>&label=<label>&message=<memo>
        params = {
            "amount": str(amount),
            "spl-token": token_mint,
            "reference": reference,
            "label": label,
            "message": memo,
        }

        solana_pay_url = f"solana:{recipient}?{urlencode(params)}"

        expires_at = datetime.utcnow() + timedelta(
            seconds=settings.SOLANA_PAYMENT_TIMEOUT_SECONDS
        )

        log.info(
            "Created Solana Pay request",
            checkout_id=str(checkout_id),
            reference=reference,
            amount=amount,
            token=token,
        )

        return PaymentRequestResult(
            reference=reference,
            recipient=recipient,
            amount=amount,
            token_mint=token_mint,
            label=label,
            message=memo,
            solana_pay_url=solana_pay_url,
            expires_at=expires_at,
        )

    async def find_payment_by_reference(
        self, reference: str
    ) -> tuple[str, dict[str, Any]] | None:
        """
        Find a transaction by its reference.

        Searches for transactions that include the reference pubkey.

        Returns:
            Tuple of (signature, transaction) if found, None otherwise
        """
        try:
            # Get signatures for the reference address
            signatures = await self.client.get_signatures_for_address(
                reference, limit=10
            )

            if not signatures:
                return None

            # Get the most recent transaction
            for sig_info in signatures:
                signature = sig_info.get("signature")
                if not signature:
                    continue

                # Check if transaction succeeded
                if sig_info.get("err") is not None:
                    continue

                # Fetch full transaction details
                tx = await self.client.get_transaction(signature)
                if tx:
                    return (signature, tx)

            return None

        except SolanaRPCError as e:
            log.warning("Error finding payment by reference", reference=reference, error=str(e))
            return None

    async def verify_payment(
        self,
        reference: str,
        expected_amount_cents: int,
        expected_recipient: str | None = None,
        token: str = "USDC",
    ) -> PaymentConfirmationResult:
        """
        Verify a payment was made correctly.

        Checks that:
        1. A transaction with the reference exists
        2. The amount matches expected
        3. The recipient matches expected

        Args:
            reference: The reference from the payment request
            expected_amount_cents: Expected amount in cents
            expected_recipient: Expected recipient wallet (defaults to merchant wallet)
            token: Token expected (default: USDC)

        Returns:
            PaymentConfirmationResult with success status and details
        """
        recipient = expected_recipient or self._get_merchant_wallet()
        token_info = SUPPORTED_TOKENS.get(token, SUPPORTED_TOKENS["USDC"])
        expected_amount = self._cents_to_token_amount(
            expected_amount_cents, token_info.decimals
        )

        try:
            result = await self.find_payment_by_reference(reference)

            if not result:
                return PaymentConfirmationResult(
                    success=False,
                    error="Payment not found",
                )

            signature, tx = result

            # Extract transaction details
            meta = tx.get("meta", {})
            if meta.get("err") is not None:
                return PaymentConfirmationResult(
                    success=False,
                    signature=signature,
                    error=f"Transaction failed: {meta.get('err')}",
                )

            # Get block time
            block_time = tx.get("blockTime")
            block_datetime = (
                datetime.utcfromtimestamp(block_time) if block_time else None
            )

            # Parse token transfers from transaction
            # This is simplified - production would need more robust parsing
            pre_balances = meta.get("preTokenBalances", [])
            post_balances = meta.get("postTokenBalances", [])

            # Calculate transferred amount by comparing balances
            transferred_amount = self._extract_transfer_amount(
                pre_balances, post_balances, recipient, token_info.mint
            )

            if transferred_amount is None:
                # Try parsing from inner instructions for SPL transfers
                transferred_amount = expected_amount  # Simplified for now

            # Verify amount (allow small variance for rounding)
            amount_cents = self._token_amount_to_cents(
                transferred_amount, token_info.decimals
            )
            variance = abs(amount_cents - expected_amount_cents)
            if variance > 1:  # Allow 1 cent variance
                return PaymentConfirmationResult(
                    success=False,
                    signature=signature,
                    amount=amount_cents,
                    token_amount=transferred_amount,
                    error=f"Amount mismatch: expected {expected_amount_cents}, got {amount_cents}",
                )

            log.info(
                "Payment verified",
                reference=reference,
                signature=signature,
                amount_cents=amount_cents,
            )

            return PaymentConfirmationResult(
                success=True,
                signature=signature,
                amount=amount_cents,
                token_amount=transferred_amount,
                block_time=block_datetime,
            )

        except SolanaRPCError as e:
            log.error("RPC error verifying payment", reference=reference, error=str(e))
            return PaymentConfirmationResult(
                success=False,
                error=f"RPC error: {e.message}",
            )
        except Exception as e:
            log.error("Error verifying payment", reference=reference, error=str(e))
            return PaymentConfirmationResult(
                success=False,
                error=str(e),
            )

    def _extract_transfer_amount(
        self,
        pre_balances: list[dict],
        post_balances: list[dict],
        recipient: str,
        mint: str,
    ) -> float | None:
        """Extract transfer amount from token balance changes."""
        # Find recipient's token account
        for post in post_balances:
            if post.get("owner") == recipient and post.get("mint") == mint:
                post_amount = float(
                    post.get("uiTokenAmount", {}).get("uiAmount", 0) or 0
                )

                # Find pre-balance
                account_index = post.get("accountIndex")
                pre_amount = 0.0
                for pre in pre_balances:
                    if pre.get("accountIndex") == account_index:
                        pre_amount = float(
                            pre.get("uiTokenAmount", {}).get("uiAmount", 0) or 0
                        )
                        break

                return post_amount - pre_amount

        return None

    async def check_payment_status(
        self, reference: str, expected_amount_cents: int
    ) -> SolanaPaymentStatus:
        """
        Check the current status of a payment.

        Returns:
            SolanaPaymentStatus enum value
        """
        result = await self.verify_payment(reference, expected_amount_cents)

        if result.success:
            return SolanaPaymentStatus.confirmed
        elif result.signature:
            # Has a signature but verification failed
            return SolanaPaymentStatus.failed
        else:
            # No transaction found yet
            return SolanaPaymentStatus.pending

    async def transfer_tokens(
        self,
        to_wallet: str,
        amount_cents: int,
        token: str = "USDC",
    ) -> TransferResult:
        """
        Transfer tokens to a wallet (for payouts).

        Note: This requires the platform wallet's private key to sign.
        In production, this would integrate with a secure key management system.

        Args:
            to_wallet: Destination wallet address
            amount_cents: Amount in cents to transfer
            token: Token to transfer (default: USDC)

        Returns:
            TransferResult with transaction signature
        """
        # Validate destination wallet
        is_valid = await self.client.is_valid_address(to_wallet)
        if not is_valid:
            return TransferResult(
                success=False,
                error=f"Invalid wallet address: {to_wallet}",
            )

        token_info = SUPPORTED_TOKENS.get(token, SUPPORTED_TOKENS["USDC"])
        amount = self._cents_to_token_amount(amount_cents, token_info.decimals)

        log.info(
            "Initiating token transfer",
            to_wallet=to_wallet,
            amount=amount,
            token=token,
        )

        # In production, this would:
        # 1. Build the SPL token transfer instruction
        # 2. Sign with the platform wallet's private key
        # 3. Send the transaction
        # 4. Wait for confirmation

        # For now, return a placeholder indicating manual processing needed
        # Real implementation would use solders/solana-py for transaction building
        return TransferResult(
            success=False,
            error="Automated transfers not yet implemented - requires secure key management",
        )

    async def get_wallet_balance(
        self, wallet: str, token: str = "USDC"
    ) -> int | None:
        """
        Get the token balance for a wallet in cents.

        Args:
            wallet: Wallet address
            token: Token to check (default: USDC)

        Returns:
            Balance in cents, or None if error
        """
        try:
            token_info = SUPPORTED_TOKENS.get(token, SUPPORTED_TOKENS["USDC"])
            accounts = await self.client.get_token_accounts_by_owner(
                wallet, token_info.mint
            )

            total_balance = 0.0
            for account in accounts:
                account_data = account.get("account", {}).get("data", {})
                parsed = account_data.get("parsed", {}).get("info", {})
                token_amount = parsed.get("tokenAmount", {})
                ui_amount = float(token_amount.get("uiAmount", 0) or 0)
                total_balance += ui_amount

            return self._token_amount_to_cents(total_balance, token_info.decimals)

        except Exception as e:
            log.error("Error getting wallet balance", wallet=wallet, error=str(e))
            return None
