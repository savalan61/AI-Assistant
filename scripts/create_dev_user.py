"""Development-only seed: create the deployment's Broker and first User.

This project is a ONE-BROKER product: the Broker row created here is the broker
the deployment serves (the application resolves the single row and refuses to
serve a database with none or several), while its mt5_server is the single MT5
server every customer's account lives on.

This script is intentionally kept OUTSIDE the app package so it can never be
part of the production runtime or API surface. It never registers a public
endpoint and never handles the MT5 password (left NULL for this step).

Safety properties:
- refuses to run unless APP_ENV=development
- idempotent: existing dev Broker/User rows are reused, not duplicated
- the password comes from --password and is NEVER printed or logged
- only touches the dev Broker/User rows; unrelated data is untouched

Usage:
    .venv/Scripts/python.exe scripts/create_dev_user.py \
        --password "<dev application password>" [--update-password]
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Running as a plain script puts scripts/ on sys.path, not the project root;
# prepend the root so the app package imports work from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.database import async_session
from app.db.models import Broker, User, UserRole

# Clearly development-only broker identity, so no real broker code can collide.
# This deployment serves exactly ONE broker, and that row is what the application
# resolves (app/core/dependencies.py load_deployment_broker); this is the code the
# script uses when it has to create that single row.
DEV_BROKER_NAME = "Development Broker (local)"
DEV_BROKER_CODE = "DEV-LOCAL"
# Development MT5 account/login number: the user's single identity, which is
# both the application login and the MT5 account number.
DEV_LOGIN = "10001"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the development Broker/User for local Swagger testing.")
    parser.add_argument("--password", required=True, help="Development application password (never printed).")
    parser.add_argument(
        "--update-password",
        action="store_true",
        help="If the dev user already exists, replace its password hash instead of leaving it unchanged.",
    )
    parser.add_argument(
        "--set-role",
        choices=[r.value for r in UserRole],
        default=None,
        metavar="ROLE",
        help="If the dev user already exists, set its role (e.g. super_admin).",
    )
    parser.add_argument(
        "--mt5-server",
        default=None,
        metavar="NAME",
        help=(
            "Set the broker's MT5 server (the one server every customer account lives on). "
            "Required before MT5 reads or credential provisioning can work; omitted leaves "
            "the stored value unchanged."
        ),
    )
    return parser.parse_args()


async def _create_dev_data(
    session: AsyncSession,
    password: str,
    update_password: bool,
    set_role: str | None,
    mt5_server: str | None,
) -> int:
    # Reuse the application's own hashing so the stored format matches login.
    password_hash = hash_password(password)

    # The ONE broker this deployment serves: the single brokers row that
    # app/core/dependencies.py load_deployment_broker resolves. Idempotent — an
    # existing row is reused whatever its code is, and a database with several
    # rows is refused rather than silently picking one.
    existing = (await session.execute(select(Broker).order_by(Broker.id.asc()))).scalars().all()
    if len(existing) > 1:
        print(
            f"refusing to run: {len(existing)} brokers rows exist, but this product serves "
            "exactly one broker. Keep that row and remove the others.",
            file=sys.stderr,
        )
        return 3
    if existing:
        broker = existing[0]
        print(f"broker already exists: {broker.code!r} (id={broker.id})")
    else:
        broker = Broker(name=DEV_BROKER_NAME, code=DEV_BROKER_CODE, mt5_server=None, is_active=True)
        session.add(broker)
        await session.flush()  # assign broker.id before creating the user
        print(f"created broker: {DEV_BROKER_CODE} (id={broker.id})")

    if mt5_server is not None:
        # The deployment's single MT5 server: every customer's identity is
        # (this server, that customer's login), and nothing per-user can change
        # it. Printed because it is deployment configuration, not a secret.
        broker.mt5_server = mt5_server
        print(f"broker MT5 server set to: {mt5_server}")
    elif not (broker.mt5_server or "").strip():
        print(
            "warning: this broker has no MT5 server, so MT5 reads and credential "
            "provisioning will fail closed until --mt5-server is supplied"
        )

    # User identity is unique (there is one broker): look up by login.
    user = (
        await session.execute(select(User).where(User.broker_id == broker.id, User.login == DEV_LOGIN))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            broker_id=broker.id,
            login=DEV_LOGIN,
            password_hash=password_hash,
            is_active=True,
            # The development seed creates the operator account; the broker
            # super_admin role matches that intent (exactly one per broker).
            role=UserRole.SUPER_ADMIN,
            # email/phone stay NULL (the model allows it); mt5_password_encrypted
            # deliberately stays NULL for this step.
        )
        session.add(user)
        print(f"created user: {DEV_LOGIN} (broker_id={broker.id}, role={user.role.value})")
    else:
        if update_password:
            user.password_hash = password_hash
            print(f"updated password hash for user: {DEV_LOGIN} (id={user.id}, broker_id={broker.id})")
        if set_role is not None:
            user.role = UserRole(set_role)
            print(f"set role for user {DEV_LOGIN} (id={user.id}): {set_role}")
        if not update_password and set_role is None:
            print(f"user already exists: {DEV_LOGIN} (id={user.id}, broker_id={broker.id}); password left unchanged")

    try:
        # Single atomic commit for broker + user; expire_on_commit=False keeps
        # ids readable for the output lines below.
        await session.commit()
    except IntegrityError:
        # Expected only in a concurrent race with the unique constraints.
        # Roll back so nothing partial is persisted; report without leaking data.
        await session.rollback()
        print("unique constraint conflict while seeding (row likely already exists); nothing persisted", file=sys.stderr)
        return 1

    if user.id is not None:
        print(f"dev user ready: {DEV_LOGIN} (id={user.id}, broker_id={broker.id}, active={user.is_active})")
    return 0


def main() -> int:
    args = _parse_args()

    # Guardrail: this mechanism must never run against a non-development setup.
    if settings.APP_ENV != "development":
        print(f"refusing to run: APP_ENV is {settings.APP_ENV!r}, not 'development'", file=sys.stderr)
        return 2

    return asyncio.run(
        _create_dev_data(async_session(), args.password, args.update_password, args.set_role, args.mt5_server)
    )


if __name__ == "__main__":
    sys.exit(main())
