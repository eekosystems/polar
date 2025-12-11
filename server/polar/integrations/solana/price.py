"""
Token price service for converting between USD and crypto tokens.

Uses CoinGecko API for price feeds. In production, consider:
- Pyth Network for on-chain prices
- Jupiter aggregator for real-time swap rates
- Caching with Redis for rate limiting
"""

import httpx
import structlog
from datetime import datetime, timedelta
from typing import Any

from polar.integrations.solana.schemas import (
    TokenInfo,
    SUPPORTED_TOKENS,
    get_token_info,
)

log = structlog.get_logger()

# Simple in-memory cache for prices
_price_cache: dict[str, tuple[float, datetime]] = {}
CACHE_TTL = timedelta(minutes=5)


class PriceServiceError(Exception):
    """Error fetching token prices."""
    pass


async def get_token_price_usd(token_symbol: str) -> float | None:
    """
    Get the current USD price for a token.

    For stablecoins, returns 1.0.
    For other tokens, fetches from CoinGecko.

    Returns:
        Price in USD, or None if not available
    """
    token = get_token_info(token_symbol)
    if not token:
        return None

    # Stablecoins are always 1:1 with USD
    if token.is_stablecoin:
        return 1.0

    # Check cache
    cache_key = token_symbol.upper()
    if cache_key in _price_cache:
        price, cached_at = _price_cache[cache_key]
        if datetime.utcnow() - cached_at < CACHE_TTL:
            return price

    # Fetch from CoinGecko
    if not token.coingecko_id:
        log.warning("No CoinGecko ID for token", token=token_symbol)
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": token.coingecko_id,
                    "vs_currencies": "usd",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            price = data.get(token.coingecko_id, {}).get("usd")
            if price is not None:
                _price_cache[cache_key] = (price, datetime.utcnow())
                return price

    except httpx.HTTPError as e:
        log.error("Error fetching token price", token=token_symbol, error=str(e))

    return None


async def get_multiple_token_prices(token_symbols: list[str]) -> dict[str, float]:
    """
    Get USD prices for multiple tokens.

    Returns:
        Dict mapping token symbol to USD price
    """
    prices = {}
    coingecko_ids = []
    symbol_to_id = {}

    for symbol in token_symbols:
        token = get_token_info(symbol)
        if not token:
            continue

        if token.is_stablecoin:
            prices[symbol.upper()] = 1.0
        elif token.coingecko_id:
            coingecko_ids.append(token.coingecko_id)
            symbol_to_id[token.coingecko_id] = symbol.upper()

    if coingecko_ids:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": ",".join(coingecko_ids),
                        "vs_currencies": "usd",
                    },
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()

                for cg_id, symbol in symbol_to_id.items():
                    price = data.get(cg_id, {}).get("usd")
                    if price is not None:
                        prices[symbol] = price
                        _price_cache[symbol] = (price, datetime.utcnow())

        except httpx.HTTPError as e:
            log.error("Error fetching token prices", error=str(e))

    return prices


def convert_usd_to_token(usd_amount: float, token_price_usd: float) -> float:
    """Convert USD amount to token amount."""
    if token_price_usd <= 0:
        raise ValueError("Token price must be positive")
    return usd_amount / token_price_usd


def convert_token_to_usd(token_amount: float, token_price_usd: float) -> float:
    """Convert token amount to USD."""
    return token_amount * token_price_usd


async def get_payment_amount_in_token(
    usd_cents: int,
    token_symbol: str,
    slippage_percent: float = 1.0,
) -> tuple[float, float] | None:
    """
    Calculate the token amount needed for a USD payment.

    Args:
        usd_cents: Amount in USD cents
        token_symbol: Token to pay with
        slippage_percent: Extra percentage to account for price movement

    Returns:
        Tuple of (token_amount, usd_price) or None if price unavailable
    """
    token = get_token_info(token_symbol)
    if not token:
        return None

    price = await get_token_price_usd(token_symbol)
    if price is None:
        return None

    usd_amount = usd_cents / 100
    token_amount = convert_usd_to_token(usd_amount, price)

    # Add slippage for non-stablecoins
    if not token.is_stablecoin and slippage_percent > 0:
        token_amount *= (1 + slippage_percent / 100)

    return (token_amount, price)


async def validate_payment_amount(
    received_token_amount: float,
    expected_usd_cents: int,
    token_symbol: str,
    tolerance_percent: float = 2.0,
) -> tuple[bool, str]:
    """
    Validate that a received payment matches the expected USD amount.

    Args:
        received_token_amount: Amount of tokens received
        expected_usd_cents: Expected USD amount in cents
        token_symbol: Token that was received
        tolerance_percent: Allowed variance percentage

    Returns:
        Tuple of (is_valid, message)
    """
    price = await get_token_price_usd(token_symbol)
    if price is None:
        return (False, f"Unable to get price for {token_symbol}")

    received_usd = convert_token_to_usd(received_token_amount, price) * 100
    expected = expected_usd_cents

    variance = abs(received_usd - expected) / expected * 100

    if variance <= tolerance_percent:
        return (True, f"Payment validated: received ${received_usd/100:.2f}")
    else:
        return (
            False,
            f"Payment amount mismatch: expected ${expected/100:.2f}, "
            f"received ${received_usd/100:.2f} ({variance:.1f}% variance)",
        )
