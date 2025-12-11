from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SolanaPaymentStatus(StrEnum):
    pending = "pending"
    confirming = "confirming"
    confirmed = "confirmed"
    failed = "failed"
    expired = "expired"


class PaymentRequestResult(BaseModel):
    """Result of creating a Solana Pay payment request."""

    reference: str = Field(description="Unique reference pubkey for tracking this payment")
    recipient: str = Field(description="Wallet address receiving the payment")
    amount: float = Field(description="Amount in token units (e.g., USDC)")
    token_mint: str = Field(description="SPL token mint address")
    label: str = Field(description="Label for the payment")
    message: str = Field(description="Message/memo for the payment")
    solana_pay_url: str = Field(description="Solana Pay URL for QR code or deep link")
    expires_at: datetime = Field(description="When this payment request expires")


class PaymentConfirmationResult(BaseModel):
    """Result of confirming a Solana payment."""

    success: bool
    signature: str | None = None
    amount: int | None = None  # Amount in cents
    token_amount: float | None = None  # Amount in token units
    error: str | None = None
    block_time: datetime | None = None


class TransferResult(BaseModel):
    """Result of a Solana transfer (for payouts)."""

    success: bool
    signature: str | None = None
    error: str | None = None


class SolanaWebhookPayload(BaseModel):
    """Payload for Solana payment webhook callbacks."""

    reference: str
    signature: str
    checkout_id: str
    amount: float
    token_mint: str
    timestamp: datetime


class TokenInfo(BaseModel):
    """Information about an SPL token."""

    mint: str
    symbol: str
    decimals: int
    name: str


# Supported tokens
SUPPORTED_TOKENS: dict[str, TokenInfo] = {
    "USDC": TokenInfo(
        mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        symbol="USDC",
        decimals=6,
        name="USD Coin",
    ),
    "USDC_DEVNET": TokenInfo(
        mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        symbol="USDC",
        decimals=6,
        name="USD Coin (Devnet)",
    ),
    "SOL": TokenInfo(
        mint="So11111111111111111111111111111111111111112",  # Native SOL wrapped
        symbol="SOL",
        decimals=9,
        name="Solana",
    ),
}
