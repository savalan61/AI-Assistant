"""add per-user MT5 credential fields

Revision ID: c4a91f2e6d77
Revises: b1f7c9d24e08
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a91f2e6d77'
down_revision: Union[str, Sequence[str], None] = 'b1f7c9d24e08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: store a user's MT5 account number and MT5 server.

    Both columns are nullable and purely additive: nothing is rewritten and no
    backfill is needed, because credential resolution falls back to
    (username, Broker.mt5_server) whenever they are NULL. The encrypted
    INVESTOR password column predates this revision and is untouched — this
    revision adds the account identity, not a new class of secret.
    """
    op.add_column('users', sa.Column('mt5_login', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('mt5_server', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema: drop the per-user MT5 account fields.

    Dropping these discards the per-user account mapping only; the stored
    encrypted password column is left exactly as it was, so a downgrade never
    destroys a credential.
    """
    op.drop_column('users', 'mt5_server')
    op.drop_column('users', 'mt5_login')
