import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from polar.auth.dependencies import WebUserOrAnonymous
from polar.exceptions import ResourceNotFound
from polar.integrations.solana import solana_pay_service
from polar.integrations.solana.schemas import SolanaPaymentStatus
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
