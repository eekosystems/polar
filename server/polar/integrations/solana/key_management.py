"""
Secure key management for Solana automated payouts.

This module handles:
- Secure storage and retrieval of signing keys
- Key encryption/decryption using Fernet
- Environment-based key configuration
- Transaction signing

SECURITY NOTES:
- Private keys should NEVER be stored in plain text
- Use environment variables or a secrets manager (AWS KMS, HashiCorp Vault)
- In production, consider using hardware security modules (HSM)
- The platform wallet key is used for automated fee collection payouts
"""

import base64
import os
from typing import Any

import structlog
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from polar.config import settings

log = structlog.get_logger()


class KeyManagementError(Exception):
    """Error in key management operations."""
    pass


class SecureKeyManager:
    """
    Manages secure storage and retrieval of Solana signing keys.

    Keys are encrypted at rest using Fernet symmetric encryption.
    The encryption key is derived from a master password using PBKDF2.
    """

    def __init__(self):
        self._fernet: Fernet | None = None
        self._initialize_encryption()

    def _initialize_encryption(self) -> None:
        """Initialize Fernet encryption using master password from environment."""
        master_password = os.environ.get("SOLANA_KEY_ENCRYPTION_PASSWORD")

        if not master_password:
            log.warning(
                "SOLANA_KEY_ENCRYPTION_PASSWORD not set, "
                "key encryption will not be available"
            )
            return

        # Use a fixed salt for deterministic key derivation
        # In production, use a unique salt per key and store it
        salt = os.environ.get(
            "SOLANA_KEY_ENCRYPTION_SALT",
            "polar_solana_key_salt_v1"
        ).encode()

        # Derive encryption key from master password
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,  # High iteration count for security
        )

        key = base64.urlsafe_b64encode(
            kdf.derive(master_password.encode())
        )
        self._fernet = Fernet(key)

        log.info("Key encryption initialized")

    def encrypt_key(self, private_key_bytes: bytes) -> str:
        """
        Encrypt a private key for secure storage.

        Args:
            private_key_bytes: The raw private key bytes (32 or 64 bytes)

        Returns:
            Base64-encoded encrypted key string
        """
        if not self._fernet:
            raise KeyManagementError(
                "Encryption not initialized. Set SOLANA_KEY_ENCRYPTION_PASSWORD."
            )

        encrypted = self._fernet.encrypt(private_key_bytes)
        return base64.b64encode(encrypted).decode()

    def decrypt_key(self, encrypted_key: str) -> bytes:
        """
        Decrypt a stored private key.

        Args:
            encrypted_key: Base64-encoded encrypted key string

        Returns:
            Decrypted private key bytes
        """
        if not self._fernet:
            raise KeyManagementError(
                "Encryption not initialized. Set SOLANA_KEY_ENCRYPTION_PASSWORD."
            )

        try:
            encrypted_bytes = base64.b64decode(encrypted_key)
            return self._fernet.decrypt(encrypted_bytes)
        except Exception as e:
            raise KeyManagementError(f"Failed to decrypt key: {e}")

    def get_platform_keypair(self) -> Any:
        """
        Get the platform's signing keypair for automated payouts.

        The platform keypair is used to sign transactions for:
        - Collecting platform fees from escrow
        - Automated refunds
        - Subscription payment processing

        Returns:
            Solana Keypair object for signing transactions
        """
        try:
            from solders.keypair import Keypair
        except ImportError:
            raise KeyManagementError(
                "solders package not installed. Run: pip install solders"
            )

        # Check for encrypted key first
        encrypted_key = os.environ.get("SOLANA_PLATFORM_PRIVATE_KEY_ENCRYPTED")
        if encrypted_key:
            key_bytes = self.decrypt_key(encrypted_key)
            return Keypair.from_bytes(key_bytes)

        # Fall back to direct key (for development only!)
        private_key = os.environ.get("SOLANA_PLATFORM_PRIVATE_KEY")
        if private_key:
            log.warning(
                "Using unencrypted SOLANA_PLATFORM_PRIVATE_KEY. "
                "This is insecure and should only be used in development!"
            )
            # Support both base58 and byte array formats
            if private_key.startswith("["):
                # Byte array format: [1,2,3,...]
                import json
                key_bytes = bytes(json.loads(private_key))
            else:
                # Base58 format
                from solders.keypair import Keypair
                return Keypair.from_base58_string(private_key)

            return Keypair.from_bytes(key_bytes)

        raise KeyManagementError(
            "No platform private key configured. Set either "
            "SOLANA_PLATFORM_PRIVATE_KEY_ENCRYPTED or "
            "SOLANA_PLATFORM_PRIVATE_KEY (dev only)."
        )

    def get_platform_public_key(self) -> str:
        """
        Get the platform's public key (wallet address).

        This is safe to expose and is used for:
        - Receiving payments
        - Verifying transaction recipients
        """
        # First check if we have a direct public key configured
        public_key = os.environ.get("SOLANA_PLATFORM_WALLET")
        if public_key:
            return public_key

        # Otherwise derive from private key
        keypair = self.get_platform_keypair()
        return str(keypair.pubkey())

    def sign_transaction(self, transaction_bytes: bytes) -> bytes:
        """
        Sign a transaction with the platform keypair.

        Args:
            transaction_bytes: The serialized transaction to sign

        Returns:
            Signature bytes
        """
        keypair = self.get_platform_keypair()

        try:
            from solders.signature import Signature
            from solders.transaction import Transaction
        except ImportError:
            raise KeyManagementError(
                "solders package not installed. Run: pip install solders"
            )

        # Sign the transaction
        signature = keypair.sign_message(transaction_bytes)
        return bytes(signature)

    def has_signing_capability(self) -> bool:
        """Check if the platform has signing capability configured."""
        encrypted_key = os.environ.get("SOLANA_PLATFORM_PRIVATE_KEY_ENCRYPTED")
        private_key = os.environ.get("SOLANA_PLATFORM_PRIVATE_KEY")
        return bool(encrypted_key or private_key)


# Global instance
key_manager = SecureKeyManager()


def generate_new_keypair() -> dict[str, str]:
    """
    Generate a new Solana keypair for development/testing.

    Returns dict with:
    - public_key: The wallet address
    - private_key_base58: Base58 encoded private key
    - private_key_encrypted: Encrypted private key (if encryption available)

    WARNING: Store the private key securely! It cannot be recovered.
    """
    try:
        from solders.keypair import Keypair
    except ImportError:
        raise KeyManagementError(
            "solders package not installed. Run: pip install solders"
        )

    keypair = Keypair()

    result = {
        "public_key": str(keypair.pubkey()),
        "private_key_base58": str(keypair),
    }

    # Add encrypted version if encryption is available
    if key_manager._fernet:
        result["private_key_encrypted"] = key_manager.encrypt_key(
            bytes(keypair)
        )

    return result


def validate_wallet_address(address: str) -> bool:
    """
    Validate a Solana wallet address.

    Args:
        address: The wallet address to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        from solders.pubkey import Pubkey
        Pubkey.from_string(address)
        return True
    except Exception:
        return False
