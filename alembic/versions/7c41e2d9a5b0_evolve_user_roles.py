"""evolve user roles to super_admin/admin/customer

Revision ID: 7c41e2d9a5b0
Revises: 3f025a5d3b39
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c41e2d9a5b0'
down_revision: Union[str, Sequence[str], None] = '3f025a5d3b39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The CHECK name is pinned here (instead of inside sa.Enum) so upgrade and
# downgrade reference the identical constraint name deterministically.
_ROLE_CHECK = "ck_users_role"
# Partial unique index enforcing "exactly one super_admin per broker" at the
# database layer. Built with a driver-agnostic Index and dialect-specific
# where clauses so the same migration serves PostgreSQL (production) and the
# SQLite-based test/dev paths without a dialect branch.
_SUPER_ADMIN_INDEX = "uq_users_broker_super_admin"


def upgrade() -> None:
    """Upgrade schema: migrate roles, then swap the domain constraints."""
    # Data migration FIRST: every existing broker-level administrator becomes
    # super_admin before the new CHECK forbids 'broker_admin'. broker_admin is
    # the current broker-level owner/manager role; nothing is lost or
    # duplicated — one row becomes one row with the new role value.
    op.execute("UPDATE users SET role = 'super_admin' WHERE role = 'broker_admin'")

    # Domain swap using the same pinned constraint name as the model.
    op.drop_constraint(_ROLE_CHECK, 'users', type_='check')
    op.create_check_constraint(_ROLE_CHECK, 'users', "role IN ('super_admin', 'admin', 'customer')")

    # One super_admin per broker: database-level enforcement, not just
    # application logic. The index is partial (only super_admin rows), so
    # multiple admins and customers per broker remain unrestricted.
    op.create_index(
        _SUPER_ADMIN_INDEX,
        'users',
        ['broker_id'],
        unique=True,
        postgresql_where=sa.text("role = 'super_admin'"),
        sqlite_where=sa.text("role = 'super_admin'"),
    )


def downgrade() -> None:
    """Downgrade schema: collapse roles back to the two-role model."""
    # Reverse of upgrade: remove the partial index, restore the two-role
    # domain. Data downgrade maps the hierarchy back conservatively:
    # super_admin and admin both become broker_admin (the pre-evolution
    # broker-level administrator role); customer rows are untouched.
    op.drop_index(_SUPER_ADMIN_INDEX, table_name='users')
    op.drop_constraint(_ROLE_CHECK, 'users', type_='check')
    # Data mapping back BEFORE the two-role CHECK is re-created.
    op.execute("UPDATE users SET role = 'broker_admin' WHERE role = 'super_admin'")
    op.execute("UPDATE users SET role = 'broker_admin' WHERE role = 'admin'")
    op.create_check_constraint(_ROLE_CHECK, 'users', "role IN ('broker_admin', 'customer')")
