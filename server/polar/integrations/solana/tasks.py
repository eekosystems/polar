import uuid
from datetime import datetime, timedelta

import structlog

from polar.config import settings
from polar.enums import PaymentProcessor
from polar.integrations.solana import solana_pay_service
from polar.integrations.solana.schemas import SolanaPaymentStatus
from polar.kit.db.postgres import AsyncSessionMaker
from polar.worker import TaskPriority, actor, enqueue_job

log = structlog.get_logger()


class SolanaPaymentCheckError(Exception):
    """Error checking Solana payment."""

    pass


@actor(actor_name="solana.check_payment", priority=TaskPriority.HIGH)
async def check_solana_payment(
    checkout_id: uuid.UUID,
    reference: str,
    expected_amount_cents: int,
    attempt: int = 1,
    max_attempts: int = 360,  # 30 minutes at 5 second intervals
) -> None:
    """
    Background task to check if a Solana payment has been confirmed.

    Polls the Solana network for the payment transaction and triggers
    checkout success when found.

    Args:
        checkout_id: The checkout session ID
        reference: The Solana Pay reference to look for
        expected_amount_cents: Expected payment amount
        attempt: Current attempt number
        max_attempts: Maximum attempts before giving up
    """
    from polar.checkout.repository import CheckoutRepository
    from polar.checkout.service import checkout_service
    from polar.models import Checkout
    from polar.models.checkout import CheckoutStatus
    from polar.payment.service import payment_service

    log.info(
        "Checking Solana payment",
        checkout_id=str(checkout_id),
        reference=reference,
        attempt=attempt,
    )

    async with AsyncSessionMaker() as session:
        # Get checkout
        checkout_repo = CheckoutRepository.from_session(session)
        checkout = await checkout_repo.get_by_id(checkout_id)

        if not checkout:
            log.warning("Checkout not found", checkout_id=str(checkout_id))
            return

        # Check if already processed
        if checkout.status == CheckoutStatus.succeeded:
            log.info("Checkout already succeeded", checkout_id=str(checkout_id))
            return

        if checkout.status not in (CheckoutStatus.open, CheckoutStatus.confirmed):
            log.warning(
                "Checkout in unexpected status",
                checkout_id=str(checkout_id),
                status=checkout.status,
            )
            return

        # Check if expired
        if checkout.expires_at and checkout.expires_at < datetime.utcnow():
            log.info("Checkout expired", checkout_id=str(checkout_id))
            # Could update status to expired here
            return

        # Check payment status on-chain
        result = await solana_pay_service.verify_payment(
            reference=reference,
            expected_amount_cents=expected_amount_cents,
        )

        if result.success:
            log.info(
                "Solana payment confirmed!",
                checkout_id=str(checkout_id),
                signature=result.signature,
                amount=result.amount,
            )

            # Create payment record
            from polar.models import Payment
            from polar.models.payment import PaymentStatus

            payment = Payment(
                processor=PaymentProcessor.solana,
                processor_id=result.signature,
                status=PaymentStatus.succeeded,
                amount=result.amount or expected_amount_cents,
                currency="usd",
                checkout_id=checkout_id,
                organization_id=checkout.organization_id,
                customer_id=checkout.customer_id,
            )

            session.add(payment)
            await session.flush()

            # Trigger checkout success flow
            await checkout_service.handle_success(
                session,
                checkout,
                payment=payment,
                payment_method=None,  # No saved payment method for crypto
            )

            await session.commit()

            log.info(
                "Checkout completed via Solana",
                checkout_id=str(checkout_id),
                payment_id=str(payment.id),
            )
            return

        # Payment not found yet
        if attempt >= max_attempts:
            log.warning(
                "Max attempts reached for Solana payment check",
                checkout_id=str(checkout_id),
                reference=reference,
            )
            # Could mark checkout as failed/expired here
            return

        # Re-enqueue for next check
        delay_ms = settings.SOLANA_CONFIRMATION_POLL_INTERVAL_SECONDS * 1000
        enqueue_job(
            "solana.check_payment",
            checkout_id=checkout_id,
            reference=reference,
            expected_amount_cents=expected_amount_cents,
            attempt=attempt + 1,
            max_attempts=max_attempts,
            delay=delay_ms,
        )

        log.debug(
            "Re-enqueued Solana payment check",
            checkout_id=str(checkout_id),
            next_attempt=attempt + 1,
        )


@actor(actor_name="solana.process_payout", priority=TaskPriority.MEDIUM)
async def process_solana_payout(
    payout_id: uuid.UUID,
    to_wallet: str,
    amount_cents: int,
) -> None:
    """
    Process a payout to a Solana wallet.

    Args:
        payout_id: The payout record ID
        to_wallet: Destination wallet address
        amount_cents: Amount in cents to transfer
    """
    from polar.models.payout import PayoutStatus
    from polar.payout.repository import PayoutRepository

    log.info(
        "Processing Solana payout",
        payout_id=str(payout_id),
        to_wallet=to_wallet,
        amount_cents=amount_cents,
    )

    async with AsyncSessionMaker() as session:
        payout_repo = PayoutRepository.from_session(session)
        payout = await payout_repo.get_by_id(payout_id)

        if not payout:
            log.error("Payout not found", payout_id=str(payout_id))
            return

        if payout.status != PayoutStatus.pending:
            log.warning(
                "Payout not in pending status",
                payout_id=str(payout_id),
                status=payout.status,
            )
            return

        # Attempt transfer
        result = await solana_pay_service.transfer_tokens(
            to_wallet=to_wallet,
            amount_cents=amount_cents,
        )

        if result.success:
            payout.status = PayoutStatus.succeeded
            payout.processor_id = result.signature
            payout.paid_at = datetime.utcnow()

            log.info(
                "Solana payout succeeded",
                payout_id=str(payout_id),
                signature=result.signature,
            )
        else:
            # For now, automated transfers aren't implemented
            # Mark as pending for manual processing
            log.warning(
                "Solana payout requires manual processing",
                payout_id=str(payout_id),
                error=result.error,
            )

        await session.commit()


@actor(actor_name="solana.verify_wallet", priority=TaskPriority.LOW)
async def verify_solana_wallet(
    account_id: uuid.UUID,
    wallet_address: str,
) -> None:
    """
    Verify a Solana wallet address is valid.

    Args:
        account_id: The account ID to update
        wallet_address: Wallet address to verify
    """
    from polar.account.repository import AccountRepository

    log.info(
        "Verifying Solana wallet",
        account_id=str(account_id),
        wallet_address=wallet_address,
    )

    async with AsyncSessionMaker() as session:
        account_repo = AccountRepository.from_session(session)
        account = await account_repo.get_by_id(account_id)

        if not account:
            log.error("Account not found", account_id=str(account_id))
            return

        # Check if wallet is valid
        is_valid = await solana_pay_service.client.is_valid_address(wallet_address)

        if is_valid:
            log.info(
                "Solana wallet verified",
                account_id=str(account_id),
                wallet_address=wallet_address,
            )
            # Could update account status here
        else:
            log.warning(
                "Invalid Solana wallet",
                account_id=str(account_id),
                wallet_address=wallet_address,
            )

        await session.commit()
