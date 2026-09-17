"""one-broker architecture: global user uniqueness, one super_admin, no per-user MT5 server

Revision ID: d8e14b7a9c30
Revises: a5d92c41f7be
Create Date: 2026-09-17 00:00:00.000000

This deployment is ONE broker (the single brokers row the application resolves),
so three multi-broker shapes in the users table are no longer meaningful and are
replaced by their deployment-wide equivalents:

1. ``uq_users_broker_login`` / ``uq_users_broker_email`` / ``uq_users_broker_phone``
   become ``uq_users_login`` / ``uq_users_email`` / ``uq_users_phone``. With one
   broker, "unique per broker" and "unique" are the same rule; making it explicit
   also makes a login a single global identity, which is what the login endpoint
   (which no longer takes a tenant selector) and the MT5 account binding assume.
2. ``uq_users_broker_super_admin`` (one super_admin per broker) becomes
   ``uq_users_single_super_admin`` (one super_admin in the deployment).
3. ``users.mt5_server`` is dropped. The MT5 server is now the broker's own
   configuration only, so a credential can no longer be pointed at another
   server: that column was exactly what allowed a customer's reads to be
   redirected to somebody else's MT5 account.

The migration refuses to run against a database that is not a valid one-broker
database (more than one broker row, or duplicate logins/emails/phones that the
new constraints would reject), instead of failing halfway or silently discarding
data. A single-broker database is unaffected by the constraint swap, and every
value it holds is preserved — this revision moves uniqueness, not data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e14b7a9c30'
down_revision: Union[str, Sequence[str], None] = 'a5d92c41f7be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Constraint/index names are pinned identically in the model, so upgrade and
# downgrade reference the very same objects.
_LOGIN_UNIQUE = 'uq_users_login'
_EMAIL_UNIQUE = 'uq_users_email'
_PHONE_UNIQUE = 'uq_users_phone'
_SINGLE_SUPER_ADMIN_INDEX = 'uq_users_single_super_admin'

_LEGACY_LOGIN_UNIQUE = 'uq_users_broker_login'
_LEGACY_EMAIL_UNIQUE = 'uq_users_broker_email'
_LEGACY_PHONE_UNIQUE = 'uq_users_broker_phone'
_LEGACY_SUPER_ADMIN_INDEX = 'uq_users_broker_super_admin'


def _abort_if_not_one_broker() -> None:
    """Refuse to migrate anything that is not a coherent one-broker database.

    Checked BEFORE any DDL, so an aborted run leaves the schema exactly as it
    was. Each message names the fix, because the alternative — letting the new
    constraints fail — would report a generic integrity error instead of the
    actual condition.
    """
    connection = op.get_bind()

    brokers = connection.execute(sa.text("SELECT COUNT(*) FROM brokers")).scalar_one()
    if brokers != 1:
        raise RuntimeError(
            f"aborting: this migration makes the database a ONE-broker database, but "
            f"{brokers} brokers rows exist. This deployment serves exactly one broker "
            f"(the application refuses to serve a database with none or several); keep "
            f"that row, remove or archive the others, and re-run."
        )

    for column, constraint in (("login", _LOGIN_UNIQUE), ("email", _EMAIL_UNIQUE), ("phone", _PHONE_UNIQUE)):
        # NULL is not a value here: email/phone are optional, and any number of
        # rows may leave them unset (that is true of unique constraints on every
        # supported database), so only real duplicates are checked.
        duplicates = connection.execute(
            sa.text(
                f"SELECT {column} FROM users WHERE {column} IS NOT NULL "
                f"GROUP BY {column} HAVING COUNT(*) > 1 LIMIT 5"
            )
        ).fetchall()
        if duplicates:
            values = ", ".join(repr(row[0]) for row in duplicates)
            raise RuntimeError(
                f"aborting: users.{column} has duplicates ({values}) that the "
                f"deployment-wide unique constraint {constraint} would reject. Resolve "
                f"them (deactivate or rename the duplicate accounts) and re-run."
            )


def upgrade() -> None:
    """Upgrade schema: deployment-wide uniqueness, one super_admin, no per-user MT5 server."""
    _abort_if_not_one_broker()

    # Step 1: rotate the uniqueness from per-broker to deployment-wide. The
    # broker_id component is dropped (there is exactly one broker), so the same
    # rule is now expressed once instead of once per tenant.
    op.drop_constraint(_LEGACY_LOGIN_UNIQUE, 'users', type_='unique')
    op.drop_constraint(_LEGACY_EMAIL_UNIQUE, 'users', type_='unique')
    op.drop_constraint(_LEGACY_PHONE_UNIQUE, 'users', type_='unique')
    op.create_unique_constraint(_LOGIN_UNIQUE, 'users', ['login'])
    op.create_unique_constraint(_EMAIL_UNIQUE, 'users', ['email'])
    op.create_unique_constraint(_PHONE_UNIQUE, 'users', ['phone'])

    # Step 2: the same rotation for "exactly one super_admin". Uniqueness now
    # applies to the role value itself (a partial index over super_admin rows),
    # so a second super_admin is impossible even if a second broker row were
    # inserted later.
    op.drop_index(_LEGACY_SUPER_ADMIN_INDEX, table_name='users')
    op.create_index(
        _SINGLE_SUPER_ADMIN_INDEX,
        'users',
        ['role'],
        unique=True,
        postgresql_where=sa.text("role = 'super_admin'"),
        sqlite_where=sa.text("role = 'super_admin'"),
    )

    # Step 3: the per-user MT5 server is gone. The broker's own mt5_server is
    # the only server in this deployment, so this column had exactly one valid
    # value and could redirect a customer's reads anywhere else.
    op.drop_column('users', 'mt5_server')


def downgrade() -> None:
    """Downgrade schema: restore the per-broker constraints and the user MT5 server.

    The server column is restored as a nullable column with no values: the
    deployment now has ONE server (the broker's), and writing that value into
    every user row would be exactly the per-user override this revision removed.
    Credential resolution falls back to the broker's server for rows where it is
    NULL, so the previous behaviour is recovered without inventing per-user data.
    """
    op.add_column('users', sa.Column('mt5_server', sa.String(length=100), nullable=True))

    op.drop_index(_SINGLE_SUPER_ADMIN_INDEX, table_name='users')
    op.create_index(
        _LEGACY_SUPER_ADMIN_INDEX,
        'users',
        ['broker_id'],
        unique=True,
        postgresql_where=sa.text("role = 'super_admin'"),
        sqlite_where=sa.text("role = 'super_admin'"),
    )

    op.drop_constraint(_LOGIN_UNIQUE, 'users', type_='unique')
    op.drop_constraint(_EMAIL_UNIQUE, 'users', type_='unique')
    op.drop_constraint(_PHONE_UNIQUE, 'users', type_='unique')
    op.create_unique_constraint(_LEGACY_LOGIN_UNIQUE, 'users', ['broker_id', 'login'])
    op.create_unique_constraint(_LEGACY_EMAIL_UNIQUE, 'users', ['broker_id', 'email'])
    op.create_unique_constraint(_LEGACY_PHONE_UNIQUE, 'users', ['broker_id', 'phone'])
