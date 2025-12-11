"""add solana_wallet to accounts

Revision ID: a1b2c3d4e5f6
Revises: cf31af43f14a
Create Date: 2025-12-11 00:01:00.000000

"""

import sqlalchemy as sa
from alembic import op

# Polar Custom Imports

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "cf31af43f14a"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    # Add solana_wallet column to accounts table
    op.add_column(
        "accounts",
        sa.Column("solana_wallet", sa.String(44), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "solana_wallet")
