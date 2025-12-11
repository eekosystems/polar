"""
Crypto subscription billing for Solana payments.

Since crypto payments don't have built-in recurring billing like Stripe,
we implement a pull-based subscription model:

1. On subscription start, save customer's preferred payment token
2. At each billing cycle, generate a payment request
3. Send email/notification to customer with payment link
4. Customer pays within grace period
5. If not paid, subscription goes past_due, then revoked

This is similar to invoice-based billing in traditional systems.
"""

import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog

from polar.config import settings
from polar.enums import PaymentProcessor, SubscriptionRecurringInterval
from polar.integrations.solana import solana_pay_service
from polar.integrations.solana.schemas import get_token_info
from polar.kit.db.postgres import AsyncSession, AsyncSessionMaker
from polar.worker import TaskPriority, actor, enqueue_job

log = structlog.get_logger()


class CryptoSubscriptionStatus(StrEnum):
    """Status of a crypto subscription payment cycle."""

    pending = "pending"  # Payment request created, waiting for payment
    paid = "paid"  # Payment received
    past_due = "past_due"  # Payment overdue, grace period active
    failed = "failed"  # Payment failed after grace period


# Grace period before marking subscription as past_due
PAYMENT_GRACE_PERIOD = timedelta(days=3)

# Maximum time to wait before revoking subscription
PAYMENT_REVOCATION_PERIOD = timedelta(days=7)


async def create_subscription_payment_request(
    session: AsyncSession,
    subscription_id: uuid.UUID,
    amount_cents: int,
    token: str = "USDC",
) -> dict[str, Any]:
    """
    Create a payment request for a subscription renewal.

    Args:
        session: Database session
        subscription_id: The subscription to bill
        amount_cents: Amount in USD cents
        token: Token to accept (default: USDC)

    Returns:
        Payment request details including Solana Pay URL
    """
    from polar.models import Subscription
    from polar.subscription.repository import SubscriptionRepository

    repo = SubscriptionRepository.from_session(session)
    subscription = await repo.get_by_id(subscription_id)

    if not subscription:
        raise ValueError(f"Subscription {subscription_id} not found")

    # Get organization name for payment label
    label = "Subscription Renewal"
    if subscription.organization:
        label = f"{subscription.organization.name} Subscription"

    # Create Solana Pay request
    # We use subscription_id as part of the checkout_id for tracking
    payment_request = solana_pay_service.create_payment_request(
        amount_cents=amount_cents,
        checkout_id=subscription_id,  # Use subscription ID for tracking
        label=label,
        message=f"Renewal for {label}",
        token=token,
    )

    # Store payment request in subscription metadata
    metadata = subscription.metadata or {}
    metadata["pending_payment"] = {
        "reference": payment_request.reference,
        "amount_cents": amount_cents,
        "token": token,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": payment_request.expires_at.isoformat(),
        "solana_pay_url": payment_request.solana_pay_url,
    }
    subscription.metadata = metadata

    await session.flush()

    log.info(
        "Created subscription payment request",
        subscription_id=str(subscription_id),
        reference=payment_request.reference,
        amount_cents=amount_cents,
    )

    return {
        "subscription_id": str(subscription_id),
        "reference": payment_request.reference,
        "solana_pay_url": payment_request.solana_pay_url,
        "amount_cents": amount_cents,
        "token": token,
        "expires_at": payment_request.expires_at.isoformat(),
    }


async def check_subscription_payment(
    session: AsyncSession,
    subscription_id: uuid.UUID,
) -> CryptoSubscriptionStatus:
    """
    Check if a subscription payment has been received.

    Returns the current status of the payment.
    """
    from polar.models import Subscription
    from polar.subscription.repository import SubscriptionRepository

    repo = SubscriptionRepository.from_session(session)
    subscription = await repo.get_by_id(subscription_id)

    if not subscription:
        raise ValueError(f"Subscription {subscription_id} not found")

    metadata = subscription.metadata or {}
    pending_payment = metadata.get("pending_payment")

    if not pending_payment:
        return CryptoSubscriptionStatus.paid  # No pending payment

    reference = pending_payment.get("reference")
    amount_cents = pending_payment.get("amount_cents", 0)

    if not reference:
        return CryptoSubscriptionStatus.failed

    # Check payment status on-chain
    result = await solana_pay_service.verify_payment(
        reference=reference,
        expected_amount_cents=amount_cents,
    )

    if result.success:
        # Payment received! Clear pending payment and update subscription
        del metadata["pending_payment"]
        metadata["last_payment"] = {
            "signature": result.signature,
            "amount_cents": result.amount,
            "paid_at": datetime.utcnow().isoformat(),
        }
        subscription.metadata = metadata

        log.info(
            "Subscription payment confirmed",
            subscription_id=str(subscription_id),
            signature=result.signature,
        )

        return CryptoSubscriptionStatus.paid

    # Check if payment is overdue
    created_at = datetime.fromisoformat(pending_payment.get("created_at", ""))
    now = datetime.utcnow()

    if now - created_at > PAYMENT_REVOCATION_PERIOD:
        return CryptoSubscriptionStatus.failed
    elif now - created_at > PAYMENT_GRACE_PERIOD:
        return CryptoSubscriptionStatus.past_due
    else:
        return CryptoSubscriptionStatus.pending


@actor(actor_name="solana.subscription.cycle", priority=TaskPriority.MEDIUM)
async def process_subscription_cycle(
    subscription_id: uuid.UUID,
) -> None:
    """
    Process a subscription billing cycle.

    Creates a payment request and starts monitoring for payment.
    """
    from polar.models import Subscription
    from polar.subscription.repository import SubscriptionRepository

    log.info("Processing crypto subscription cycle", subscription_id=str(subscription_id))

    async with AsyncSessionMaker() as session:
        repo = SubscriptionRepository.from_session(session)
        subscription = await repo.get_by_id(subscription_id)

        if not subscription:
            log.error("Subscription not found", subscription_id=str(subscription_id))
            return

        # Check if this is a crypto subscription
        if subscription.payment_processor != PaymentProcessor.solana:
            log.warning(
                "Not a Solana subscription",
                subscription_id=str(subscription_id),
            )
            return

        # Get subscription price
        if not subscription.subscription_product_prices:
            log.error("No prices for subscription", subscription_id=str(subscription_id))
            return

        price = subscription.subscription_product_prices[0].product_price
        amount_cents = price.price_amount or 0

        # Get preferred token from subscription metadata
        metadata = subscription.metadata or {}
        token = metadata.get("payment_token", "USDC")

        # Create payment request
        payment_info = await create_subscription_payment_request(
            session=session,
            subscription_id=subscription_id,
            amount_cents=amount_cents,
            token=token,
        )

        await session.commit()

        # Schedule payment check job
        enqueue_job(
            "solana.subscription.check_payment",
            subscription_id=subscription_id,
            attempt=1,
        )

        # TODO: Send email/notification to customer with payment link
        log.info(
            "Subscription payment request created",
            subscription_id=str(subscription_id),
            payment_url=payment_info["solana_pay_url"],
        )


@actor(actor_name="solana.subscription.check_payment", priority=TaskPriority.MEDIUM)
async def check_subscription_payment_task(
    subscription_id: uuid.UUID,
    attempt: int = 1,
    max_attempts: int = 336,  # 7 days at 30 minute intervals
) -> None:
    """
    Background task to check if subscription payment was received.

    Runs periodically until payment is received or subscription is revoked.
    """
    from polar.subscription.service import subscription_service

    log.debug(
        "Checking subscription payment",
        subscription_id=str(subscription_id),
        attempt=attempt,
    )

    async with AsyncSessionMaker() as session:
        status = await check_subscription_payment(session, subscription_id)

        if status == CryptoSubscriptionStatus.paid:
            log.info(
                "Subscription payment confirmed",
                subscription_id=str(subscription_id),
            )
            # Payment received, subscription continues
            # The existing subscription cycle logic will handle the next period
            await session.commit()
            return

        elif status == CryptoSubscriptionStatus.failed:
            log.warning(
                "Subscription payment failed, revoking",
                subscription_id=str(subscription_id),
            )
            # Revoke the subscription
            # await subscription_service.revoke(session, subscription_id)
            await session.commit()
            return

        elif status == CryptoSubscriptionStatus.past_due:
            log.warning(
                "Subscription payment past due",
                subscription_id=str(subscription_id),
            )
            # TODO: Send reminder notification

        # Payment still pending, schedule next check
        if attempt < max_attempts:
            # Check every 30 minutes
            delay_ms = 30 * 60 * 1000
            enqueue_job(
                "solana.subscription.check_payment",
                subscription_id=subscription_id,
                attempt=attempt + 1,
                delay=delay_ms,
            )
        else:
            log.error(
                "Max payment check attempts reached",
                subscription_id=str(subscription_id),
            )


@actor(actor_name="solana.subscription.send_reminder", priority=TaskPriority.LOW)
async def send_payment_reminder(
    subscription_id: uuid.UUID,
    reminder_type: str = "due",  # "due", "past_due", "final"
) -> None:
    """
    Send a payment reminder to the customer.

    Args:
        subscription_id: The subscription to remind about
        reminder_type: Type of reminder (due, past_due, final)
    """
    from polar.subscription.repository import SubscriptionRepository

    log.info(
        "Sending payment reminder",
        subscription_id=str(subscription_id),
        reminder_type=reminder_type,
    )

    async with AsyncSessionMaker() as session:
        repo = SubscriptionRepository.from_session(session)
        subscription = await repo.get_by_id(subscription_id)

        if not subscription:
            return

        metadata = subscription.metadata or {}
        pending_payment = metadata.get("pending_payment")

        if not pending_payment:
            return

        # TODO: Implement email/notification sending
        # This would integrate with the existing notification system
        #
        # await notification_service.send(
        #     recipient=subscription.customer,
        #     template="subscription_payment_reminder",
        #     context={
        #         "subscription": subscription,
        #         "payment_url": pending_payment["solana_pay_url"],
        #         "amount": pending_payment["amount_cents"] / 100,
        #         "reminder_type": reminder_type,
        #     },
        # )

        log.info(
            "Payment reminder sent",
            subscription_id=str(subscription_id),
            customer_id=str(subscription.customer_id) if subscription.customer_id else None,
        )
