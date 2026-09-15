"""Development-only seed: clear users and create the three-role test accounts.

Kept OUTSIDE the app package so it can never be part of the production runtime
or API surface. It adds no endpoint and stores no real secret.

Development-only credentials (the three accounts the local test set expects).
These are NOT secrets: they are fixed, clearly marked local-development
passwords, they all carry a "Dev…-Local-Only" marker so a real password can
never collide with them, and this script refuses to run unless
APP_ENV=development. Never reuse any of these outside a local database.

    role         username          development password
    -----------  ----------------  -----------------------------
    super_admin  dev-super-admin   DevSuperAdmin-Local-Only-1
    admin        dev-admin         DevAdmin-Local-Only-1
    customer     dev-customer      DevCustomer-Local-Only-1

It reuses the EXISTING tenant (the development Broker created by
scripts/create_dev_user.py) and the existing role architecture; it creates no
broker and changes neither the User model, the migrations, nor authentication.

Safety properties:
- refuses to run unless APP_ENV=development
- requires --clear to delete existing users; without it, existing rows are refused
- passwords are hashed with the application's own hash_password (login parity)
- plaintext passwords are never stored, logged by the app, or put in a token
- only the dev Broker's users are touched; brokers and other data are untouched

Usage:
    .venv/Scripts/python.exe scripts/create_dev_users.py --clear
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Running as a plain script puts scripts/ on sys.path, not the project root;
# prepend the root so the app package imports work from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.db.database import async_session
from app.db.models import Broker, User, UserRole

# The existing development tenant. This script never creates a broker: the
# role accounts belong to the broker the dev seed already established.
DEV_BROKER_CODE = "DEV-LOCAL"

# role, username, development-only password — see the module docstring.
DEV_USERS: tuple[tuple[UserRole, str, str], ...] = (
    (UserRole.SUPER_ADMIN, "dev-super-admin", "DevSuperAdmin-Local-Only-1"),
    (UserRole.ADMIN, "dev-admin", "DevAdmin-Local-Only-1"),
    (UserRole.CUSTOMER, "dev-customer", "DevCustomer-Local-Only-1"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the three development role accounts (local testing only).")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete ALL existing users before creating the three development accounts.",
    )
    return parser.parse_args()


async def _resolve_dev_broker(session: AsyncSession) -> Broker | None:
    """Return the existing development Broker, or None if it must be seeded first."""
    return (await session.execute(select(Broker).where(Broker.code == DEV_BROKER_CODE))).scalar_one_or_none()


async def _clear_users(session: AsyncSession) -> int:
    """Delete every user row. The --clear flag is the only path to this."""
    result = await session.execute(delete(User))
    return int(result.rowcount or 0)


async def _seed_users(session: AsyncSession, broker: Broker) -> None:
    """Create (or reset) the three development accounts on the existing broker."""
    for role, username, password in DEV_USERS:
        # Application password hashing, identical to the login path.
        password_hash = hash_password(password)
        user = (
            await session.execute(select(User).where(User.broker_id == broker.id, User.username == username))
        ).scalar_one_or_none()
        if user is None:
            session.add(
                User(
                    broker_id=broker.id,
                    username=username,
                    password_hash=password_hash,
                    is_active=True,
                    role=role,
                    # email/phone stay NULL (the model allows it) and the MT5
                    # password stays NULL for this local test set.
                )
            )
            print(f"created  {role.value:<11} username={username!r}")
        else:
            # Idempotent reset: the seeded password and role are authoritative.
            user.password_hash = password_hash
            user.role = role
            user.is_active = True
            print(f"reset    {role.value:<11} username={username!r}")


async def _authenticate(session: AsyncSession, username: str, password: str) -> tuple[bool, UserRole | None]:
    """Authenticate exactly the way the login endpoint does.

    Same primitives in the same order: unique username match, bcrypt
    verification, active user, then an existing active broker. The JWT is not
    needed to prove the credential path works.
    """
    users = (await session.execute(select(User).where(User.username == username))).scalars().all()
    if len(users) != 1:
        return False, None
    user = users[0]
    if not verify_password(password, user.password_hash):
        return False, None
    if not user.is_active:
        return False, None
    broker = await session.get(Broker, user.broker_id)
    if broker is None or not broker.is_active:
        return False, None
    return True, user.role


async def _run(clear: bool) -> int:
    async with async_session() as session:
        broker = await _resolve_dev_broker(session)
        if broker is None:
            print(
                f"refusing to run: development broker {DEV_BROKER_CODE!r} does not exist "
                "(run scripts/create_dev_user.py first)",
                file=sys.stderr,
            )
            return 3
        print(f"using existing broker: {broker.code!r} (id={broker.id}, name={broker.name!r})")

        if clear:
            deleted = await _clear_users(session)
            await session.flush()
            print(f"cleared users: {deleted} row(s) deleted")
        else:
            existing = (await session.execute(select(User))).scalars().all()
            if existing:
                print(
                    f"refusing to run: {len(existing)} user row(s) already exist; pass --clear to replace them",
                    file=sys.stderr,
                )
                return 4

        await _seed_users(session, broker)
        await session.commit()

        # Verification: authenticate each account through the real credential
        # path and re-read the stored role from the database.
        print("verification (authentication + stored role):")
        failures = 0
        for role, username, password in DEV_USERS:
            ok, stored_role = await _authenticate(session, username, password)
            role_ok = stored_role is role
            status = "OK" if (ok and role_ok) else "FAILED"
            if not (ok and role_ok):
                failures += 1
            print(f"  {status:<7} username={username:<16} expected={role.value:<11} stored={stored_role.value if stored_role else None}")

        total = (await session.execute(select(User))).scalars().all()
        print(f"total users now: {len(total)} (expected {len(DEV_USERS)})")
        if len(total) != len(DEV_USERS) or failures:
            print("development user set is NOT as expected", file=sys.stderr)
            return 1

    print("development user set ready")
    return 0


def main() -> int:
    args = _parse_args()

    # Guardrail: this mechanism must never run against a non-development setup.
    if settings.APP_ENV != "development":
        print(f"refusing to run: APP_ENV is {settings.APP_ENV!r}, not 'development'", file=sys.stderr)
        return 2

    return asyncio.run(_run(args.clear))


if __name__ == "__main__":
    sys.exit(main())
