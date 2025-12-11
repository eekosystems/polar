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
    logo_url: str | None = None
    coingecko_id: str | None = None  # For price feeds
    is_stablecoin: bool = False


# Supported tokens - Mainnet
SUPPORTED_TOKENS: dict[str, TokenInfo] = {
    # Stablecoins (1:1 USD pricing)
    "USDC": TokenInfo(
        mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        symbol="USDC",
        decimals=6,
        name="USD Coin",
        logo_url="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v/logo.png",
        coingecko_id="usd-coin",
        is_stablecoin=True,
    ),
    "USDT": TokenInfo(
        mint="Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        symbol="USDT",
        decimals=6,
        name="Tether USD",
        logo_url="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB/logo.png",
        coingecko_id="tether",
        is_stablecoin=True,
    ),
    "PYUSD": TokenInfo(
        mint="2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",
        symbol="PYUSD",
        decimals=6,
        name="PayPal USD",
        logo_url="https://pyusd.io/images/pyusd-logo.png",
        coingecko_id="paypal-usd",
        is_stablecoin=True,
    ),

    # Native SOL
    "SOL": TokenInfo(
        mint="So11111111111111111111111111111111111111112",
        symbol="SOL",
        decimals=9,
        name="Solana",
        logo_url="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png",
        coingecko_id="solana",
        is_stablecoin=False,
    ),

    # Popular SPL Tokens
    "BONK": TokenInfo(
        mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        symbol="BONK",
        decimals=5,
        name="Bonk",
        logo_url="https://arweave.net/hQiPZOsRZXGXBJd_82PhVdlM_hACsT_q6wqwf5cSY7I",
        coingecko_id="bonk",
        is_stablecoin=False,
    ),
    "JUP": TokenInfo(
        mint="JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        symbol="JUP",
        decimals=6,
        name="Jupiter",
        logo_url="https://static.jup.ag/jup/icon.png",
        coingecko_id="jupiter-exchange-solana",
        is_stablecoin=False,
    ),
    "PYTH": TokenInfo(
        mint="HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
        symbol="PYTH",
        decimals=6,
        name="Pyth Network",
        logo_url="https://pyth.network/token.png",
        coingecko_id="pyth-network",
        is_stablecoin=False,
    ),
    "WIF": TokenInfo(
        mint="EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        symbol="WIF",
        decimals=6,
        name="dogwifhat",
        logo_url="https://bafkreibk3covs5ltyqxa272uodhculbr6kea6betiez7dqxjmkr7s7jljq.ipfs.nftstorage.link/",
        coingecko_id="dogwifcoin",
        is_stablecoin=False,
    ),
    "RENDER": TokenInfo(
        mint="rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
        symbol="RENDER",
        decimals=8,
        name="Render Token",
        logo_url="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof/logo.png",
        coingecko_id="render-token",
        is_stablecoin=False,
    ),
    "RAY": TokenInfo(
        mint="4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        symbol="RAY",
        decimals=6,
        name="Raydium",
        logo_url="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R/logo.png",
        coingecko_id="raydium",
        is_stablecoin=False,
    ),
    "ORCA": TokenInfo(
        mint="orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        symbol="ORCA",
        decimals=6,
        name="Orca",
        logo_url="https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE/logo.png",
        coingecko_id="orca",
        is_stablecoin=False,
    ),
}

# Devnet tokens for testing
DEVNET_TOKENS: dict[str, TokenInfo] = {
    "USDC": TokenInfo(
        mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        symbol="USDC",
        decimals=6,
        name="USD Coin (Devnet)",
        is_stablecoin=True,
    ),
    "SOL": TokenInfo(
        mint="So11111111111111111111111111111111111111112",
        symbol="SOL",
        decimals=9,
        name="Solana (Devnet)",
        is_stablecoin=False,
    ),
}


def get_token_info(symbol: str, use_devnet: bool = False) -> TokenInfo | None:
    """Get token info by symbol."""
    tokens = DEVNET_TOKENS if use_devnet else SUPPORTED_TOKENS
    return tokens.get(symbol.upper())


def get_token_by_mint(mint: str, use_devnet: bool = False) -> TokenInfo | None:
    """Get token info by mint address."""
    tokens = DEVNET_TOKENS if use_devnet else SUPPORTED_TOKENS
    for token in tokens.values():
        if token.mint == mint:
            return token
    return None


def list_supported_tokens(use_devnet: bool = False) -> list[TokenInfo]:
    """List all supported tokens."""
    tokens = DEVNET_TOKENS if use_devnet else SUPPORTED_TOKENS
    return list(tokens.values())
