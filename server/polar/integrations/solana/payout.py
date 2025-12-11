"""
Automated payout execution for Solana payments.

This module handles:
- Processing pending payouts to creators/organizations
- Building and signing SPL token transfers
- Transaction submission and confirmation
- Retry logic for failed transactions

The payout flow:
1. Payment received -> funds held in platform escrow (minus 1% fee)
2. Creator requests payout or auto-payout triggers
3. Platform signs and sends transfer transaction
4. Transaction confirmed -> payout marked complete
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

import structlog

from polar.config import settings
from polar.integrations.solana.client import solana_client
from polar.integrations.solana.key_management import key_manager, KeyManagementError
from polar.integrations.solana.schemas import get_token_info, TokenInfo
from polar.kit.db.postgres import AsyncSession, AsyncSessionMaker
from polar.worker import TaskPriority, actor, enqueue_job

log = structlog.get_logger()


class PayoutStatus(StrEnum):
    """Status of a payout."""
    pending = "pending"  # Payout requested, not yet processed
    processing = "processing"  # Transaction being built/signed
    submitted = "submitted"  # Transaction submitted to network
    confirmed = "confirmed"  # Transaction confirmed on-chain
    failed = "failed"  # Transaction failed


class PayoutError(Exception):
    """Error during payout processing."""
    pass


async def calculate_payout_amount(
    gross_amount: int,
    token_symbol: str = "USDC",
) -> tuple[int, int]:
    """
    Calculate the payout amount after platform fee.

    Args:
        gross_amount: Gross payment amount in token's smallest unit
        token_symbol: Token being paid out

    Returns:
        Tuple of (payout_amount, platform_fee)
    """
    # Platform fee is 1% (100 basis points)
    fee_basis_points = settings.PLATFORM_FEE_BASIS_POINTS
    platform_fee = (gross_amount * fee_basis_points) // 10000
    payout_amount = gross_amount - platform_fee

    return (payout_amount, platform_fee)


async def build_transfer_instruction(
    from_pubkey: str,
    to_pubkey: str,
    amount: int,
    token_mint: str,
) -> Any:
    """
    Build an SPL token transfer instruction.

    Args:
        from_pubkey: Source wallet (platform escrow)
        to_pubkey: Destination wallet (creator)
        amount: Amount in token's smallest unit
        token_mint: SPL token mint address

    Returns:
        Transfer instruction for transaction
    """
    try:
        from solders.pubkey import Pubkey
        from spl.token.instructions import (
            transfer_checked,
            TransferCheckedParams,
            get_associated_token_address,
        )
    except ImportError:
        raise PayoutError(
            "Required packages not installed. Run: pip install solders spl"
        )

    token_info = get_token_info_by_mint(token_mint)
    if not token_info:
        raise PayoutError(f"Unknown token mint: {token_mint}")

    from_pubkey_obj = Pubkey.from_string(from_pubkey)
    to_pubkey_obj = Pubkey.from_string(to_pubkey)
    mint_pubkey = Pubkey.from_string(token_mint)

    # Get associated token accounts
    from_ata = get_associated_token_address(from_pubkey_obj, mint_pubkey)
    to_ata = get_associated_token_address(to_pubkey_obj, mint_pubkey)

    # Build transfer instruction
    instruction = transfer_checked(
        TransferCheckedParams(
            program_id=Pubkey.from_string(
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            ),
            source=from_ata,
            mint=mint_pubkey,
            dest=to_ata,
            owner=from_pubkey_obj,
            amount=amount,
            decimals=token_info.decimals,
        )
    )

    return instruction


def get_token_info_by_mint(mint: str) -> TokenInfo | None:
    """Get token info by mint address."""
    from polar.integrations.solana.schemas import SUPPORTED_TOKENS

    for token in SUPPORTED_TOKENS.values():
        if token.mint_address == mint or token.devnet_mint == mint:
            return token
    return None


async def execute_payout(
    session: AsyncSession,
    payout_id: uuid.UUID,
    recipient_wallet: str,
    amount: int,
    token_symbol: str = "USDC",
) -> dict[str, Any]:
    """
    Execute a payout to a recipient wallet.

    Args:
        session: Database session
        payout_id: Unique ID for this payout
        recipient_wallet: Recipient's Solana wallet address
        amount: Amount in token's smallest unit (e.g., USDC has 6 decimals)
        token_symbol: Token to pay out

    Returns:
        Dict with transaction details
    """
    from polar.integrations.solana.key_management import validate_wallet_address

    log.info(
        "Executing payout",
        payout_id=str(payout_id),
        recipient=recipient_wallet,
        amount=amount,
        token=token_symbol,
    )

    # Validate recipient wallet
    if not validate_wallet_address(recipient_wallet):
        raise PayoutError(f"Invalid recipient wallet: {recipient_wallet}")

    # Check signing capability
    if not key_manager.has_signing_capability():
        raise PayoutError(
            "Platform signing key not configured. Cannot execute payouts."
        )

    # Get token info
    token_info = get_token_info(token_symbol)
    if not token_info:
        raise PayoutError(f"Unsupported token: {token_symbol}")

    # Determine which mint to use (devnet vs mainnet)
    use_devnet = getattr(settings, "SOLANA_USE_DEVNET", True)
    mint_address = token_info.devnet_mint if use_devnet else token_info.mint_address

    if not mint_address:
        raise PayoutError(f"No mint address for {token_symbol}")

    try:
        from solders.pubkey import Pubkey
        from solders.transaction import Transaction
        from solders.message import Message
        from solders.hash import Hash
    except ImportError:
        raise PayoutError("solders package not installed")

    # Get platform keypair for signing
    platform_keypair = key_manager.get_platform_keypair()
    platform_pubkey = str(platform_keypair.pubkey())

    # Build transfer instruction
    instruction = await build_transfer_instruction(
        from_pubkey=platform_pubkey,
        to_pubkey=recipient_wallet,
        amount=amount,
        token_mint=mint_address,
    )

    # Get recent blockhash
    rpc_client = solana_client.get_client()
    blockhash_response = await rpc_client.get_latest_blockhash()
    recent_blockhash = blockhash_response.value.blockhash

    # Build and sign transaction
    message = Message.new_with_blockhash(
        [instruction],
        Pubkey.from_string(platform_pubkey),
        Hash.from_string(str(recent_blockhash)),
    )

    transaction = Transaction.new_unsigned(message)
    transaction.sign([platform_keypair], Hash.from_string(str(recent_blockhash)))

    # Submit transaction
    try:
        result = await rpc_client.send_transaction(transaction)
        signature = str(result.value)

        log.info(
            "Payout transaction submitted",
            payout_id=str(payout_id),
            signature=signature,
        )

        return {
            "payout_id": str(payout_id),
            "signature": signature,
            "status": PayoutStatus.submitted,
            "recipient": recipient_wallet,
            "amount": amount,
            "token": token_symbol,
        }

    except Exception as e:
        log.error(
            "Failed to submit payout transaction",
            payout_id=str(payout_id),
            error=str(e),
        )
        raise PayoutError(f"Transaction submission failed: {e}")


async def confirm_payout(
    signature: str,
    max_retries: int = 30,
    retry_interval: float = 2.0,
) -> bool:
    """
    Wait for a payout transaction to be confirmed.

    Args:
        signature: Transaction signature
        max_retries: Maximum confirmation attempts
        retry_interval: Seconds between attempts

    Returns:
        True if confirmed, False if failed/expired
    """
    import asyncio

    rpc_client = solana_client.get_client()

    for attempt in range(max_retries):
        try:
            response = await rpc_client.get_signature_statuses([signature])

            if response.value and response.value[0]:
                status = response.value[0]

                if status.err:
                    log.error(
                        "Payout transaction failed",
                        signature=signature,
                        error=status.err,
                    )
                    return False

                if status.confirmation_status and str(status.confirmation_status) in [
                    "confirmed",
                    "finalized",
                ]:
                    log.info(
                        "Payout transaction confirmed",
                        signature=signature,
                        confirmations=status.confirmations,
                    )
                    return True

        except Exception as e:
            log.warning(
                "Error checking payout status",
                signature=signature,
                attempt=attempt,
                error=str(e),
            )

        await asyncio.sleep(retry_interval)

    log.error("Payout confirmation timed out", signature=signature)
    return False


@actor(actor_name="solana.payout.process", priority=TaskPriority.HIGH)
async def process_payout_task(
    payout_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: int,
    token_symbol: str = "USDC",
) -> None:
    """
    Background task to process a payout.

    Args:
        payout_id: Unique payout ID
        account_id: Account to pay out to
        amount: Amount in token's smallest unit
        token_symbol: Token to pay out
    """
    from polar.models import Account
    from polar.account.repository import AccountRepository

    log.info(
        "Processing payout task",
        payout_id=str(payout_id),
        account_id=str(account_id),
    )

    async with AsyncSessionMaker() as session:
        # Get account with wallet address
        repo = AccountRepository.from_session(session)
        account = await repo.get_by_id(account_id)

        if not account:
            log.error("Account not found for payout", account_id=str(account_id))
            return

        if not account.solana_wallet:
            log.error(
                "Account has no Solana wallet configured",
                account_id=str(account_id),
            )
            return

        try:
            # Execute the payout
            result = await execute_payout(
                session=session,
                payout_id=payout_id,
                recipient_wallet=account.solana_wallet,
                amount=amount,
                token_symbol=token_symbol,
            )

            # Wait for confirmation
            confirmed = await confirm_payout(result["signature"])

            if confirmed:
                log.info(
                    "Payout completed successfully",
                    payout_id=str(payout_id),
                    signature=result["signature"],
                )
                # TODO: Update payout record in database
            else:
                log.error(
                    "Payout confirmation failed",
                    payout_id=str(payout_id),
                )
                # TODO: Mark payout as failed, schedule retry

        except PayoutError as e:
            log.error(
                "Payout execution failed",
                payout_id=str(payout_id),
                error=str(e),
            )
            # TODO: Handle failure, potentially retry


@actor(actor_name="solana.payout.batch", priority=TaskPriority.MEDIUM)
async def process_batch_payouts() -> None:
    """
    Process all pending payouts in batch.

    This task runs periodically to process accumulated payouts.
    Batching helps reduce transaction costs.
    """
    log.info("Processing batch payouts")

    # TODO: Query pending payouts from database
    # TODO: Group by token to optimize transactions
    # TODO: Execute payouts with proper ordering

    # For now, this is a placeholder
    pass


@actor(actor_name="solana.payout.retry", priority=TaskPriority.LOW)
async def retry_failed_payouts() -> None:
    """
    Retry payouts that previously failed.

    Failed payouts are retried with exponential backoff.
    """
    log.info("Retrying failed payouts")

    # TODO: Query failed payouts
    # TODO: Check if retry is appropriate (time limit, attempt count)
    # TODO: Re-enqueue for processing

    pass


async def create_payout_request(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    token_symbol: str = "USDC",
    priority: str = "normal",
) -> dict[str, Any]:
    """
    Create a new payout request.

    Args:
        session: Database session
        account_id: Account to pay out to
        amount: Amount in token's smallest unit
        token_symbol: Token to pay out
        priority: "high" for immediate, "normal" for batched

    Returns:
        Payout request details
    """
    payout_id = uuid.uuid4()

    log.info(
        "Creating payout request",
        payout_id=str(payout_id),
        account_id=str(account_id),
        amount=amount,
        token=token_symbol,
    )

    # TODO: Save payout request to database

    if priority == "high":
        # Process immediately
        enqueue_job(
            "solana.payout.process",
            payout_id=payout_id,
            account_id=account_id,
            amount=amount,
            token_symbol=token_symbol,
        )
    else:
        # Will be picked up by batch processor
        pass

    return {
        "payout_id": str(payout_id),
        "account_id": str(account_id),
        "amount": amount,
        "token": token_symbol,
        "status": PayoutStatus.pending,
        "priority": priority,
    }
