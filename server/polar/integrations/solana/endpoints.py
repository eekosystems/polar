import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from polar.auth.dependencies import WebUserOrAnonymous
from polar.config import settings
from polar.exceptions import ResourceNotFound
from polar.integrations.solana import solana_pay_service
from polar.integrations.solana.schemas import (
    SolanaPaymentStatus,
    TokenInfo,
    list_supported_tokens,
    get_token_info,
)
from polar.integrations.solana.price import (
    get_token_price_usd,
    get_multiple_token_prices,
    get_payment_amount_in_token,
)
from polar.kit.db.postgres import AsyncSession, get_db_session
from polar.worker import enqueue_job

log = structlog.get_logger()

router = APIRouter(prefix="/integrations/solana", tags=["integrations:solana"])


class PaymentStatusResponse(BaseModel):
    """Response for payment status check."""

    status: SolanaPaymentStatus
    signature: str | None = None
    amount_cents: int | None = None
    message: str | None = None


class CreatePaymentRequest(BaseModel):
    """Request to create a Solana payment for a checkout."""

    checkout_id: uuid.UUID


class CreatePaymentResponse(BaseModel):
    """Response with Solana Pay URL and reference."""

    solana_pay_url: str
    reference: str
    amount: float
    token: str
    recipient: str
    expires_at: str


@router.get("/payment-status")
async def get_payment_status(
    reference: Annotated[str, Query(description="Payment reference")],
    expected_amount: Annotated[int, Query(description="Expected amount in cents")],
    auth_subject: WebUserOrAnonymous,
    session: AsyncSession = Depends(get_db_session),
) -> PaymentStatusResponse:
    """
    Check the status of a Solana payment.

    Returns the current status (pending, confirming, confirmed, failed, expired).
    """
    result = await solana_pay_service.verify_payment(
        reference=reference,
        expected_amount_cents=expected_amount,
    )

    if result.success:
        return PaymentStatusResponse(
            status=SolanaPaymentStatus.confirmed,
            signature=result.signature,
            amount_cents=result.amount,
            message="Payment confirmed",
        )
    elif result.signature:
        return PaymentStatusResponse(
            status=SolanaPaymentStatus.failed,
            signature=result.signature,
            message=result.error,
        )
    else:
        return PaymentStatusResponse(
            status=SolanaPaymentStatus.pending,
            message="Waiting for payment",
        )


@router.post("/create-payment")
async def create_solana_payment(
    request: CreatePaymentRequest,
    auth_subject: WebUserOrAnonymous,
    session: AsyncSession = Depends(get_db_session),
) -> CreatePaymentResponse:
    """
    Create a Solana Pay payment request for a checkout.

    Returns a Solana Pay URL that can be displayed as a QR code
    or used as a deep link for wallet apps.
    """
    from polar.checkout.repository import CheckoutRepository
    from polar.models.checkout import CheckoutStatus

    checkout_repo = CheckoutRepository.from_session(session)
    checkout = await checkout_repo.get_by_id(request.checkout_id)

    if not checkout:
        raise ResourceNotFound("Checkout not found")

    if checkout.status not in (CheckoutStatus.open, CheckoutStatus.confirmed):
        raise HTTPException(
            status_code=400,
            detail=f"Checkout cannot be paid: status is {checkout.status}",
        )

    # Get the organization name for the label
    label = "Payment"
    if checkout.organization:
        label = checkout.organization.name

    # Create Solana Pay request
    payment_request = solana_pay_service.create_payment_request(
        amount_cents=checkout.total_amount or 0,
        checkout_id=checkout.id,
        label=label,
        message=f"Order from {label}",
    )

    # Store reference in checkout metadata
    metadata = checkout.payment_processor_metadata or {}
    metadata["solana_reference"] = payment_request.reference
    metadata["solana_pay_url"] = payment_request.solana_pay_url
    checkout.payment_processor_metadata = metadata

    await session.commit()

    # Start background job to poll for payment
    enqueue_job(
        "solana.check_payment",
        checkout_id=checkout.id,
        reference=payment_request.reference,
        expected_amount_cents=checkout.total_amount or 0,
    )

    log.info(
        "Created Solana payment request",
        checkout_id=str(checkout.id),
        reference=payment_request.reference,
    )

    return CreatePaymentResponse(
        solana_pay_url=payment_request.solana_pay_url,
        reference=payment_request.reference,
        amount=payment_request.amount,
        token="USDC",
        recipient=payment_request.recipient,
        expires_at=payment_request.expires_at.isoformat(),
    )


@router.get("/wallet-balance")
async def get_wallet_balance(
    wallet: Annotated[str, Query(description="Wallet address")],
    token: Annotated[str, Query(description="Token symbol")] = "USDC",
    auth_subject: WebUserOrAnonymous,
) -> dict:
    """
    Get the token balance for a wallet.

    Returns the balance in cents.
    """
    balance = await solana_pay_service.get_wallet_balance(wallet, token)

    if balance is None:
        raise HTTPException(
            status_code=400,
            detail="Could not fetch wallet balance",
        )

    return {
        "wallet": wallet,
        "token": token,
        "balance_cents": balance,
        "balance_formatted": f"${balance / 100:.2f}",
    }


class TokenResponse(BaseModel):
    """Token information."""

    symbol: str
    name: str
    mint: str
    decimals: int
    logo_url: str | None
    is_stablecoin: bool
    price_usd: float | None = None


class TokenListResponse(BaseModel):
    """List of supported tokens."""

    tokens: list[TokenResponse]


class PriceConversionRequest(BaseModel):
    """Request for price conversion."""

    usd_cents: int
    token: str


class PriceConversionResponse(BaseModel):
    """Response for price conversion."""

    usd_cents: int
    usd_formatted: str
    token: str
    token_amount: float
    token_price_usd: float
    slippage_included: bool


@router.get("/tokens")
async def list_tokens(
    auth_subject: WebUserOrAnonymous,
    include_prices: bool = Query(default=False, description="Include current USD prices"),
) -> TokenListResponse:
    """
    List all supported tokens for payments.

    Returns token information including mint addresses and decimals.
    """
    tokens = list_supported_tokens(use_devnet=settings.SOLANA_USE_DEVNET)

    prices = {}
    if include_prices:
        symbols = [t.symbol for t in tokens]
        prices = await get_multiple_token_prices(symbols)

    return TokenListResponse(
        tokens=[
            TokenResponse(
                symbol=t.symbol,
                name=t.name,
                mint=t.mint,
                decimals=t.decimals,
                logo_url=t.logo_url,
                is_stablecoin=t.is_stablecoin,
                price_usd=prices.get(t.symbol),
            )
            for t in tokens
        ]
    )


@router.get("/token/{symbol}")
async def get_token(
    symbol: str,
    auth_subject: WebUserOrAnonymous,
) -> TokenResponse:
    """
    Get information about a specific token.

    Returns token details including current USD price.
    """
    token = get_token_info(symbol, use_devnet=settings.SOLANA_USE_DEVNET)

    if not token:
        raise HTTPException(
            status_code=404,
            detail=f"Token {symbol} not found",
        )

    price = await get_token_price_usd(symbol)

    return TokenResponse(
        symbol=token.symbol,
        name=token.name,
        mint=token.mint,
        decimals=token.decimals,
        logo_url=token.logo_url,
        is_stablecoin=token.is_stablecoin,
        price_usd=price,
    )


@router.post("/convert-price")
async def convert_price(
    request: PriceConversionRequest,
    auth_subject: WebUserOrAnonymous,
) -> PriceConversionResponse:
    """
    Convert a USD amount to token amount.

    Useful for displaying prices in different tokens.
    Includes 1% slippage for non-stablecoins.
    """
    token = get_token_info(request.token, use_devnet=settings.SOLANA_USE_DEVNET)

    if not token:
        raise HTTPException(
            status_code=404,
            detail=f"Token {request.token} not found",
        )

    result = await get_payment_amount_in_token(
        usd_cents=request.usd_cents,
        token_symbol=request.token,
        slippage_percent=1.0 if not token.is_stablecoin else 0.0,
    )

    if result is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to get price for {request.token}",
        )

    token_amount, price_usd = result

    return PriceConversionResponse(
        usd_cents=request.usd_cents,
        usd_formatted=f"${request.usd_cents / 100:.2f}",
        token=request.token,
        token_amount=token_amount,
        token_price_usd=price_usd,
        slippage_included=not token.is_stablecoin,
    )


@router.get("/price/{symbol}")
async def get_token_price(
    symbol: str,
    auth_subject: WebUserOrAnonymous,
) -> dict:
    """
    Get the current USD price for a token.
    """
    price = await get_token_price_usd(symbol)

    if price is None:
        raise HTTPException(
            status_code=404,
            detail=f"Price not available for {symbol}",
        )

    return {
        "symbol": symbol.upper(),
        "price_usd": price,
        "is_stablecoin": get_token_info(symbol, use_devnet=settings.SOLANA_USE_DEVNET).is_stablecoin if get_token_info(symbol) else False,
    }


# --- Payout Endpoints ---

class CreatePayoutRequest(BaseModel):
    """Request to create a payout."""

    account_id: uuid.UUID
    amount: int  # Amount in token's smallest unit (e.g., 1000000 = 1 USDC)
    token: str = "USDC"
    priority: str = "normal"  # "high" for immediate, "normal" for batched


class PayoutResponse(BaseModel):
    """Response for a payout request."""

    payout_id: str
    account_id: str
    amount: int
    token: str
    status: str
    priority: str
    signature: str | None = None
    message: str | None = None


class PayoutStatusRequest(BaseModel):
    """Request to check payout status."""

    payout_id: uuid.UUID


@router.post("/payouts/create")
async def create_payout(
    request: CreatePayoutRequest,
    auth_subject: WebUserOrAnonymous,
    session: AsyncSession = Depends(get_db_session),
) -> PayoutResponse:
    """
    Create a new payout request.

    Payouts transfer funds from the platform escrow to a creator's wallet.
    The 1% platform fee is automatically deducted.
    """
    from polar.integrations.solana.payout import create_payout_request, PayoutStatus

    # Validate token
    token = get_token_info(request.token, use_devnet=settings.SOLANA_USE_DEVNET)
    if not token:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported token: {request.token}",
        )

    try:
        result = await create_payout_request(
            session=session,
            account_id=request.account_id,
            amount=request.amount,
            token_symbol=request.token,
            priority=request.priority,
        )

        return PayoutResponse(
            payout_id=result["payout_id"],
            account_id=result["account_id"],
            amount=result["amount"],
            token=result["token"],
            status=result["status"],
            priority=result["priority"],
        )

    except Exception as e:
        log.error("Failed to create payout", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create payout: {str(e)}",
        )


@router.get("/payouts/{payout_id}")
async def get_payout_status(
    payout_id: uuid.UUID,
    auth_subject: WebUserOrAnonymous,
    session: AsyncSession = Depends(get_db_session),
) -> PayoutResponse:
    """
    Get the status of a payout.

    Returns current status and transaction signature if completed.
    """
    # TODO: Implement payout status lookup from database
    # For now, return a placeholder

    return PayoutResponse(
        payout_id=str(payout_id),
        account_id="",
        amount=0,
        token="USDC",
        status="pending",
        priority="normal",
        message="Payout status lookup not yet implemented",
    )


class SetWalletRequest(BaseModel):
    """Request to set a Solana wallet for an account."""

    account_id: uuid.UUID
    wallet_address: str


@router.post("/accounts/set-wallet")
async def set_account_wallet(
    request: SetWalletRequest,
    auth_subject: WebUserOrAnonymous,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Set the Solana wallet address for an account.

    This wallet will be used for receiving payouts.
    """
    from polar.integrations.solana.key_management import validate_wallet_address
    from polar.account.repository import AccountRepository

    # Validate wallet address
    if not validate_wallet_address(request.wallet_address):
        raise HTTPException(
            status_code=400,
            detail="Invalid Solana wallet address",
        )

    repo = AccountRepository.from_session(session)
    account = await repo.get_by_id(request.account_id)

    if not account:
        raise ResourceNotFound("Account not found")

    # Update wallet address
    account.solana_wallet = request.wallet_address
    await session.flush()

    log.info(
        "Updated account Solana wallet",
        account_id=str(request.account_id),
        wallet=request.wallet_address,
    )

    return {
        "account_id": str(request.account_id),
        "wallet_address": request.wallet_address,
        "message": "Wallet address updated successfully",
    }


class PlatformStatusResponse(BaseModel):
    """Response for platform status check."""

    signing_enabled: bool
    platform_wallet: str | None
    supported_tokens: list[str]
    fee_percentage: float


@router.get("/platform/status")
async def get_platform_status(
    auth_subject: WebUserOrAnonymous,
) -> PlatformStatusResponse:
    """
    Get the Solana payment platform status.

    Returns whether automated payouts are enabled and the platform wallet.
    """
    from polar.integrations.solana.key_management import key_manager

    # Check if signing is enabled
    signing_enabled = key_manager.has_signing_capability()

    # Get platform wallet
    platform_wallet = None
    try:
        platform_wallet = key_manager.get_platform_public_key()
    except Exception:
        pass

    # Get supported tokens
    tokens = list_supported_tokens(use_devnet=settings.SOLANA_USE_DEVNET)
    token_symbols = [t.symbol for t in tokens]

    # Platform fee
    fee_percentage = settings.PLATFORM_FEE_BASIS_POINTS / 100

    return PlatformStatusResponse(
        signing_enabled=signing_enabled,
        platform_wallet=platform_wallet,
        supported_tokens=token_symbols,
        fee_percentage=fee_percentage,
    )
