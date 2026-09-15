"""add broker llm configuration

Revision ID: b1f7c9d24e08
Revises: 7c41e2d9a5b0
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1f7c9d24e08'
down_revision: Union[str, Sequence[str], None] = '7c41e2d9a5b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Constraint names are pinned here so upgrade and downgrade reference the
# identical names deterministically (same convention as ck_users_role).
_TABLE = "broker_llm_configs"
_UNIQUE_BROKER = "uq_broker_llm_configs_broker"
_PROVIDER_CHECK = "ck_broker_llm_configs_provider"
_BROKER_INDEX = "ix_broker_llm_configs_broker_id"

# Provider kinds accepted by the CHECK constraint; mirrors LLMProviderKind.
_PROVIDER_VALUES = "provider IN ('openai_compatible')"


def upgrade() -> None:
    """Upgrade schema: add the broker-scoped LLM configuration table."""
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('broker_id', sa.Integer(), nullable=False),
        # Non-native enum: plain VARCHAR (portable, evolvable) with the domain
        # pinned by the CHECK constraint below, matching the model.
        sa.Column('provider', sa.String(length=30), server_default='openai_compatible', nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('base_url', sa.String(length=255), nullable=False),
        # Fernet ciphertext only: a plaintext API key is never stored.
        sa.Column('api_key_encrypted', sa.String(length=1000), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['broker_id'], ['brokers.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # One configuration per broker, enforced by the database itself.
        sa.UniqueConstraint('broker_id', name=_UNIQUE_BROKER),
        sa.CheckConstraint(_PROVIDER_VALUES, name=_PROVIDER_CHECK),
    )
    op.create_index(op.f(_BROKER_INDEX), _TABLE, ['broker_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema: drop the LLM configuration table."""
    op.drop_index(op.f(_BROKER_INDEX), table_name=_TABLE)
    op.drop_table(_TABLE)
