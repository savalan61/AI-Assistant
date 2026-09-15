"""replace users.username with the single users.login identity

Revision ID: a5d92c41f7be
Revises: c4a91f2e6d77
Create Date: 2026-09-15 00:00:00.000000

A user had two identity columns: ``username`` (the application login) and
``mt5_login`` (the MT5 account number, added by c4a91f2e6d77). They describe
the same fact, so this revision collapses them into one column, ``login``,
which is both the application login and the MT5 account number.

It is a genuine RENAME, not an add-and-copy, so that no obsolete column can
survive alongside the new one:

1. pre-flight check that no row's ``mt5_login`` disagrees with ``username``
   before that column is dropped, aborting loudly instead of silently
   discarding a divergent MT5 account number;
2. rename ``username`` to ``login`` — every value is carried over by the
   database itself, so no row is dropped and no identity changes (a user keeps
   logging in with exactly the value it had);
3. move the tenant-scoped uniqueness from the old name to the new one;
4. drop the now-redundant ``mt5_login`` column.

The encrypted MT5 INVESTOR password and ``mt5_server`` are untouched: this
revision moves an identity, never a secret.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5d92c41f7be'
down_revision: Union[str, Sequence[str], None] = 'c4a91f2e6d77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_UNIQUE = 'uq_users_broker_username'
_LOGIN_UNIQUE = 'uq_users_broker_login'


def upgrade() -> None:
    """Upgrade schema: one identity column (login) instead of username + mt5_login."""
    # Step 1: pre-flight. Dropping mt5_login is only lossless when it duplicated
    # the identity. If any row carries a *different* MT5 account number, abort
    # with an actionable message rather than discarding it. The tenant-scoped
    # migration is transactional on PostgreSQL, so an abort leaves the schema
    # and the data exactly as they were.
    divergent = op.get_bind().execute(
        sa.text("SELECT id FROM users WHERE mt5_login IS NOT NULL AND mt5_login <> username LIMIT 5")
    ).fetchall()
    if divergent:
        ids = ", ".join(str(row[0]) for row in divergent)
        raise RuntimeError(
            "aborting: users.mt5_login disagrees with users.username for user id(s) "
            f"{ids}. Reconcile those rows (login is the MT5 account number now) and re-run; "
            "mt5_login will not be dropped while it holds a different value."
        )

    # Step 2: the identity keeps every value it had; only its name changes.
    op.alter_column(
        'users',
        'username',
        new_column_name='login',
        existing_type=sa.String(length=100),
        existing_nullable=False,
    )

    # Step 3: tenant-scoped uniqueness moves with the renamed column. The
    # constraint name itself is unchanged by a column rename, so the legacy
    # name is still the one to drop. Same semantics (unique per broker), new
    # column and new pinned name.
    op.drop_constraint(_LEGACY_UNIQUE, 'users', type_='unique')
    op.create_unique_constraint(_LOGIN_UNIQUE, 'users', ['broker_id', 'login'])

    # Step 4: the duplicated account column is redundant by construction.
    op.drop_column('users', 'mt5_login')


def downgrade() -> None:
    """Downgrade schema: restore username + mt5_login from login.

    Restores the previous shape without losing an identity: the account number
    is written into the recreated mt5_login column as well, which is what the
    pre-Step-42 resolver expected. Credentials are never modified.
    """
    op.add_column('users', sa.Column('mt5_login', sa.String(length=32), nullable=True))
    # Copy the identity back into both columns. A non-numeric login is copied
    # too, exactly as the old resolver would have had to handle it (it accepted
    # a numeric username and ignored anything else).
    op.execute("UPDATE users SET mt5_login = login")

    # Move uniqueness back to the restored column, then rename it into place so
    # the pinned names end up exactly as the pre-Step-42 schema had them.
    op.drop_constraint(_LOGIN_UNIQUE, 'users', type_='unique')
    op.alter_column(
        'users',
        'login',
        new_column_name='username',
        existing_type=sa.String(length=100),
        existing_nullable=False,
    )
    op.create_unique_constraint(_LEGACY_UNIQUE, 'users', ['broker_id', 'username'])
