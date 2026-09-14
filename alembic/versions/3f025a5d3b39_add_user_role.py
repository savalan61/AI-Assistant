"""add user role

Revision ID: 3f025a5d3b39
Revises: 4e37cc12c811
Create Date: 2026-09-14 13:07:32.635616
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f025a5d3b39'
down_revision: Union[str, Sequence[str], None] = '4e37cc12c811'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The CHECK name is pinned here (instead of inside sa.Enum) so upgrade and
# downgrade reference the identical constraint name deterministically.
_ROLE_CHECK = "ck_users_role"


def upgrade() -> None:
    """Upgrade schema: add users.role, backfill existing rows, enforce domain."""
    # Non-native enum: a plain VARCHAR(20) keeps the schema portable and
    # evolvable without native enum-type alterations when roles change.
    op.add_column(
        'users',
        sa.Column('role', sa.String(length=20), nullable=False, server_default='customer'),
    )
    # Compatibility backfill: every pre-existing user becomes 'customer'
    # (least privilege). The existing development user keeps working because
    # nothing in authentication reads role yet; the dev row is promoted to
    # broker_admin explicitly by the development seed step.
    op.execute("UPDATE users SET role = 'customer'")
    # Domain integrity at the database layer, matching the model's constraint.
    op.create_check_constraint(_ROLE_CHECK, 'users', "role IN ('broker_admin', 'customer')")


def downgrade() -> None:
    """Downgrade schema: drop the constraint and the column."""
    op.drop_constraint(_ROLE_CHECK, 'users', type_='check')
    op.drop_column('users', 'role')
