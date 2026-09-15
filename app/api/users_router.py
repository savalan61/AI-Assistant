"""Users API: tenant-scoped user management.

Covers creating Customer/Admin users inside the caller's own broker, listing
those users, and provisioning a user's MT5 INVESTOR (read-only) credential.
The tenant is always the authenticated database user's broker — no endpoint
accepts a broker_id — and no response ever carries a password hash, an MT5
credential, or any other secret.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    get_current_broker_manager,
    get_current_super_admin,
    resolve_mt5_account_credentials,
)
from app.core.encryption import EncryptionError, encrypt_secret
from app.core.security import hash_password
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

router = APIRouter(prefix="/users", tags=["users"])


# username is the MT5 login/account number: an ASCII-digit string, kept as
# str so leading zeros survive (never converted to int). Real MT5 account ids
# fit 4-12 digits. [0-9] is deliberate: \d would also match Unicode digits.
_USERNAME_PATTERN = re.compile(r"^[0-9]{4,12}$")
# Basic international phone: optional leading '+', then 7-15 digits (E.164
# range). No phone library is introduced for this simple format.
_PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")


# Only the fields a Broker Admin may choose. Deliberately excluded: id,
# broker_id, role, password_hash, is_active, mt5_password_encrypted. extra=
# "forbid" turns any supplied field outside this list into a 422 validation
# error instead of a silently ignored value, so tenant/role/credential
# forging attempts fail loudly rather than disappearing.
class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    email: str | None = None
    phone: str | None = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if not _USERNAME_PATTERN.fullmatch(value):
            raise ValueError("username must be 4-12 digits")
        return value

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        # Minimum length only for now (no complexity rules yet). The upper
        # bound matches bcrypt's 72-byte input limit so an oversized secret is
        # rejected as 422 here instead of surfacing as a hashing error (500).
        if len(value) < 8:
            raise ValueError("password must be at least 8 characters")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 bytes")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        # Basic structural check: Pydantic's EmailStr would require the
        # external email-validator package, deliberately not added here. The
        # length cap matches the database column so oversized input cannot
        # become a database error.
        if value is None:
            return value
        local, sep, domain = value.partition("@")
        if (
            value.count("@") != 1
            or not local
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
            or " " in value
            or len(value) > 255
        ):
            raise ValueError("invalid email address")
        return value

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str | None) -> str | None:
        if value is not None and not _PHONE_PATTERN.fullmatch(value):
            raise ValueError("phone must be an optional '+' followed by 7-15 digits")
        return value


class UserResponse(BaseModel):
    # Non-sensitive projection of the ORM User; sensitive columns are
    # structurally absent from the response schema.
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    username: str
    email: str | None
    phone: str | None
    role: UserRole
    is_active: bool


# An MT5 account number: the same ASCII-digit shape the username rule enforces,
# because both are MT5 logins. The alias is deliberate — it documents that the
# two rules are meant to agree, without duplicating the pattern.
_MT5_LOGIN_PATTERN = _USERNAME_PATTERN
# MT5 server names are short identifiers (e.g. "BrokerName-Live2"); the cap
# matches the User and Broker columns so oversized input cannot become a
# database error.
_MT5_SERVER_MAX_LENGTH = 100
# Upper bound on a stored credential. MT5 passwords are far shorter; the cap
# only stops an oversized secret being encrypted into the column.
_MT5_PASSWORD_MAX_LENGTH = 128


class MT5CredentialRequest(BaseModel):
    """Write-only payload provisioning a user's MT5 INVESTOR credential.

    Deliberately excludes broker_id, user_id, role and is_active: the target
    comes from the path (scoped to the caller's tenant) and the tenant from the
    authenticated caller, so extra="forbid" turns any such attempt into a 422
    rather than a silently ignored value.

    There is intentionally no field for an MT5 trading (master) password — this
    API only accepts a read-only credential, and the password field is named
    after that fact.
    """

    model_config = ConfigDict(extra="forbid")

    mt5_login: str
    mt5_server: str
    # Write-only: no response schema in this module has a matching field, so a
    # plaintext credential cannot be echoed back by construction.
    mt5_investor_password: str

    @field_validator("mt5_login")
    @classmethod
    def _validate_mt5_login(cls, value: str) -> str:
        if not _MT5_LOGIN_PATTERN.fullmatch(value):
            raise ValueError("mt5_login must be 4-12 digits")
        return value

    @field_validator("mt5_server")
    @classmethod
    def _validate_mt5_server(cls, value: str) -> str:
        # Surrounding whitespace is trimmed (a pasted server name), but a value
        # that is empty or contains control characters is rejected.
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("mt5_server must not be empty")
        if len(trimmed) > _MT5_SERVER_MAX_LENGTH:
            raise ValueError(f"mt5_server must be at most {_MT5_SERVER_MAX_LENGTH} characters")
        if any(character < " " for character in trimmed):
            raise ValueError("mt5_server must not contain control characters")
        return trimmed

    @field_validator("mt5_investor_password")
    @classmethod
    def _validate_mt5_investor_password(cls, value: str) -> str:
        # The password is never trimmed: it is stored exactly as given, since
        # whitespace may be part of a real credential. Only its shape is
        # bounded, and no rule ever echoes it back in an error message.
        if not value.strip():
            raise ValueError("mt5_investor_password must not be empty")
        if len(value) > _MT5_PASSWORD_MAX_LENGTH:
            raise ValueError(f"mt5_investor_password must be at most {_MT5_PASSWORD_MAX_LENGTH} characters")
        return value


class MT5CredentialStatus(BaseModel):
    """Non-secret view of a user's effective MT5 credential configuration.

    Contains no password field of any kind. The login and server are the
    *effective* values the session boundary would authenticate with — the
    user's own provisioning when present, otherwise the legacy fallback of a
    numeric username and the broker's server — so this response answers "what
    would an MT5 read use?" without disclosing anything secret.
    """

    user_id: int
    username: str
    mt5_login: str | None
    mt5_server: str | None
    # All three parts are present. Not a liveness check: whether MT5 actually
    # accepts the credential is only known when a read attempts to authenticate.
    mt5_configured: bool


def _duplicate_conflict() -> HTTPException:
    # Generic 409: must not reveal which unique constraint fired (username,
    # email, or phone), since that would leak information about other rows.
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    current_admin: User = Depends(get_current_broker_manager),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Create a Customer User inside the authenticated manager's broker.

    Both super_admin and admin may create customers (get_current_broker_manager).

    Tenant isolation is structural: broker_id is always taken from the
    database-backed admin record, never from the request, so a Broker Admin
    cannot create a user in another tenant.
    """
    user = User(
        # The admin's own broker is the tenant boundary for the new user.
        broker_id=current_admin.broker_id,
        username=request.username,
        # Hashed immediately; the plaintext never touches persistence or logs.
        password_hash=hash_password(request.password),
        email=request.email,
        phone=request.phone,
        # New users created through this endpoint are always customers.
        role=UserRole.CUSTOMER,
        is_active=True,
        # mt5_password_encrypted stays NULL: MT5 credentials are managed
        # separately and never through user creation.
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Expected failure at the commit boundary: the tenant-scoped unique
        # constraints (username/email/phone per broker) rejected the insert.
        await session.rollback()
        raise _duplicate_conflict()

    await session.refresh(user)
    return UserResponse.model_validate(user)


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_admin: User = Depends(get_current_broker_manager),
    session: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """List the users the authenticated manager may administer in their own tenant.

    The tenant boundary is structural: the filter uses the database-backed
    manager's broker_id and no broker_id parameter exists, so a client can
    never select another broker. Visibility follows the role hierarchy:
    - super_admin: admins and customers of their broker (the users they manage)
    - admin: customers of their broker only — never admins or super_admins
    The requesting manager is always excluded (you cannot list yourself), so
    a tenant with no manageable users is a normal 200 with an empty list.
    """
    # Role-conditional visibility, evaluated at the authorization boundary
    # from the database-backed role — never from request input.
    if current_admin.role is UserRole.SUPER_ADMIN:
        visible_roles = (UserRole.ADMIN, UserRole.CUSTOMER)
    else:  # admin: customers only; customers never reach here (403 upstream)
        visible_roles = (UserRole.CUSTOMER,)
    result = await session.execute(
        select(User)
        .where(
            User.broker_id == current_admin.broker_id,
            User.id != current_admin.id,
            User.role.in_(visible_roles),
        )
        # Deterministic admin listing: stable ascending primary-key order.
        .order_by(User.id.asc())
    )
    users = result.scalars().all()
    # UserResponse is the same non-sensitive projection used by POST /users:
    # credentials (password_hash, mt5_password_encrypted) are structurally
    # absent from the response schema.
    return [UserResponse.model_validate(user) for user in users]


@router.post("/admins", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_admin(
    request: CreateUserRequest,
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Create an Admin User inside the authenticated super_admin's broker.

    super_admin-only authorization (get_current_super_admin); admins and
    customers are rejected with 403 by the dependency. The role is a
    server-side constant — never a request field — so no caller can create
    another super_admin or escalate through this endpoint. Tenant isolation
    is structural: broker_id is always taken from the database-backed
    super_admin record, never from the request.
    """
    admin = User(
        # The super_admin's own broker is the tenant boundary for the new admin.
        broker_id=current_super_admin.broker_id,
        username=request.username,
        # Hashed immediately; the plaintext never touches persistence or logs.
        password_hash=hash_password(request.password),
        email=request.email,
        phone=request.phone,
        # Server-side constant role: this endpoint only ever creates admins.
        role=UserRole.ADMIN,
        is_active=True,
        # mt5_password_encrypted stays NULL: MT5 credentials are managed
        # separately and never through user creation.
    )
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError:
        # Expected failure at the commit boundary: the tenant-scoped unique
        # constraints (username/email/phone per broker) rejected the insert.
        # The one-super-admin partial index cannot fire here — this endpoint
        # only ever writes role='admin' rows.
        await session.rollback()
        raise _duplicate_conflict()

    await session.refresh(admin)
    # UserResponse is the non-sensitive projection: no credential fields.
    return UserResponse.model_validate(admin)


# --- MT5 credential provisioning ---------------------------------------------
#
# A broker administrator provisions the MT5 INVESTOR (read-only) credential of
# a user in its own tenant. Only three facts are ever written — the account
# number, the server, and the encrypted password — and the plaintext exists only
# for the duration of the request that supplied it.


def _credentials_unavailable() -> HTTPException:
    # Encryption is unconfigured or failed. The cause is never disclosed and no
    # secret is included, matching the broker LLM credential boundary.
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MT5 credential storage is temporarily unavailable",
    )


async def _manageable_target(session: AsyncSession, caller: User, user_id: int) -> User:
    """Load the user whose MT5 credential ``caller`` may manage.

    Tenant isolation is structural: a target outside the caller's broker is
    reported exactly like a non-existent one, so a caller can never learn which
    user ids exist in another tenant. Role hierarchy: an admin manages customer
    credentials only; a super_admin holds broker-level privileges and may also
    provision itself or an admin.
    """
    target = await session.get(User, user_id)
    if target is None or target.broker_id != caller.broker_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if caller.role is UserRole.ADMIN and target.role is not UserRole.CUSTOMER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An admin may manage customer credentials only",
        )
    return target


async def _mt5_credential_status(session: AsyncSession, user: User) -> MT5CredentialStatus:
    """Project one user's effective MT5 credential configuration (never the secret)."""
    broker = await session.get(Broker, user.broker_id)
    # Reuse the composition root's resolver so this status reports exactly what
    # an MT5-backed request for this user would use — one definition of
    # "effective", including the legacy fallback.
    credentials = resolve_mt5_account_credentials(user, broker)
    return MT5CredentialStatus(
        user_id=user.id,
        username=user.username,
        mt5_login=str(credentials.login) if credentials.login is not None else None,
        mt5_server=credentials.server,
        mt5_configured=(
            credentials.login is not None
            and bool(credentials.server)
            and bool(credentials.password_encrypted)
        ),
    )


@router.put("/{user_id}/mt5-credentials", response_model=MT5CredentialStatus)
async def set_mt5_credentials(
    user_id: int,
    request: MT5CredentialRequest,
    current_admin: User = Depends(get_current_broker_manager),
    session: AsyncSession = Depends(get_db),
) -> MT5CredentialStatus:
    """Provision or replace a user's MT5 INVESTOR (read-only) credential.

    Both manager roles reach this endpoint (get_current_broker_manager rejects
    customers with 403); the target is resolved inside the caller's tenant and
    the role rule is applied there. The password is encrypted before it reaches
    the database and is never returned, logged or included in an error; on an
    encryption failure the request fails closed (503) and nothing is written.
    """
    target = await _manageable_target(session, current_admin, user_id)
    try:
        # Encrypted immediately: the plaintext lives only inside this call and
        # is never logged. A broker never learns the customer's MT5 password
        # from this API, only that a credential is configured.
        encrypted = encrypt_secret(request.mt5_investor_password)
    except EncryptionError:
        raise _credentials_unavailable()

    target.mt5_login = request.mt5_login
    target.mt5_server = request.mt5_server
    target.mt5_password_encrypted = encrypted
    await session.commit()
    await session.refresh(target)
    # Safe projection: account identity and configured-ness, never the secret.
    return await _mt5_credential_status(session, target)


@router.get("/{user_id}/mt5-credentials", response_model=MT5CredentialStatus)
async def get_mt5_credentials(
    user_id: int,
    current_admin: User = Depends(get_current_broker_manager),
    session: AsyncSession = Depends(get_db),
) -> MT5CredentialStatus:
    """Report a user's MT5 credential configuration without disclosing it.

    Deliberately read-only and password-free: this is what a manager sees when
    checking whether a customer can be served MT5 data, and a customer can
    never reach it (403) or obtain the stored password from any endpoint.
    """
    target = await _manageable_target(session, current_admin, user_id)
    return await _mt5_credential_status(session, target)
