"""Users API: tenant-scoped user management.

Covers creating Customer/Admin users inside the caller's own broker, listing
those users, super-admin user management (get one, update, delete) and
provisioning a user's MT5 INVESTOR (read-only) credential. The tenant is
always the authenticated database user's broker — no endpoint accepts a
broker_id — and no response ever carries a password hash, an MT5 credential,
or any other secret.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
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
# broker_id, password_hash, is_active, mt5_password_encrypted. extra=
# "forbid" turns any supplied field outside this list into a 422 validation
# error instead of a silently ignored value, so tenant/role/credential
# forging attempts fail loudly rather than disappearing.
class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    email: str | None = None
    phone: str | None = None
    # Optional explicit role. Omitted (or null) means customer — the
    # least-privilege default, never a silent admin. "admin" requires a
    # super_admin caller (enforced in the endpoint from the database role).
    # "super_admin" is refused outright for every caller: a second
    # super_admin can never be created through the API (the database partial
    # unique index remains the final backstop).
    role: UserRole | None = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        return _validated_username(value)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validated_password(value)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        return _validated_email(value)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str | None) -> str | None:
        return _validated_phone(value)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: UserRole | None) -> UserRole | None:
        if value is UserRole.SUPER_ADMIN:
            raise ValueError("a super_admin user cannot be created through the API")
        return value


def _validated_username(value: str) -> str:
    if not _USERNAME_PATTERN.fullmatch(value):
        raise ValueError("username must be 4-12 digits")
    return value


def _validated_password(value: str) -> str:
    # Minimum length only for now (no complexity rules yet). The upper
    # bound matches bcrypt's 72-byte input limit so an oversized secret is
    # rejected as 422 here instead of surfacing as a hashing error (500).
    if len(value) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return value


def _validated_email(value: str | None) -> str | None:
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


def _validated_phone(value: str | None) -> str | None:
    if value is not None and not _PHONE_PATTERN.fullmatch(value):
        raise ValueError("phone must be an optional '+' followed by 7-15 digits")
    return value


# Role-less create payload for POST /users/admins: role is a server-side
# constant there (always admin), so any supplied role — even the customer
# value — is a 422 validation error. Deliberately NOT the model used by
# POST /users, which accepts an optional explicit role.
class CreateAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    email: str | None = None
    phone: str | None = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        return _validated_username(value)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validated_password(value)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        return _validated_email(value)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str | None) -> str | None:
        return _validated_phone(value)


class UpdateUserRequest(BaseModel):
    """Partial update of the fields user management supports.

    Every field is optional; an omitted field is left unchanged, and an
    explicitly supplied ``null`` clears the nullable contact fields (email,
    phone) only — username, password, role and is_active may never be set to
    null. Deliberately excluded: id, broker_id, password_hash,
    mt5_password_encrypted, and every MT5 credential field (those are
    managed exclusively by the dedicated provisioning endpoints). extra=
    "forbid" rejects anything else with 422.
    """

    model_config = ConfigDict(extra="forbid")

    username: str | None = None
    password: str | None = None
    email: str | None = None
    phone: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str | None) -> str | None:
        return value if value is None else _validated_username(value)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str | None) -> str | None:
        return value if value is None else _validated_password(value)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        return _validated_email(value)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str | None) -> str | None:
        return _validated_phone(value)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: UserRole | None) -> UserRole | None:
        if value is UserRole.SUPER_ADMIN:
            raise ValueError("a user cannot be promoted to super_admin through the API")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> "UpdateUserRequest":
        provided = self.model_fields_set
        if not provided:
            raise ValueError("at least one field must be provided")
        for name in ("username", "password", "role", "is_active"):
            if name in provided and getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


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
    """Create a User inside the authenticated manager's broker.

    Both super_admin and admin may reach this endpoint
    (get_current_broker_manager). The optional explicit role decides what is
    created: omitted/null means customer (the least-privilege default, never
    a silent admin); "customer" is the same; "admin" requires a super_admin
    caller (403 otherwise); "super_admin" is refused outright (422).

    Tenant isolation is structural: broker_id is always taken from the
    database-backed manager record, never from the request, so a Broker Admin
    cannot create a user in another tenant.
    """
    requested_role = request.role if request.role is not None else UserRole.CUSTOMER
    if requested_role is UserRole.ADMIN and current_admin.role is not UserRole.SUPER_ADMIN:
        # A deliberate authorization refusal (Step 22's matrix), raised before
        # any validation-uniqueness or database work.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a super_admin can create an admin user",
        )
    user = User(
        # The admin's own broker is the tenant boundary for the new user.
        broker_id=current_admin.broker_id,
        username=request.username,
        # Hashed immediately; the plaintext never touches persistence or logs.
        password_hash=hash_password(request.password),
        email=request.email,
        phone=request.phone,
        # Explicit request role (customer when omitted); the schema validator
        # already refused super_admin, and the database partial unique index
        # remains the final backstop.
        role=requested_role,
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
    request: CreateAdminRequest,
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


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Get one user of the authenticated super_admin's broker.

    super_admin-only (admins and customers are rejected with 403 by the
    dependency). Tenant isolation is structural: a user id from another
    broker is reported exactly like a non-existent one (404). The response is
    the same non-sensitive projection as the list endpoint.
    """
    target = await _manageable_target(session, current_super_admin, user_id)
    return UserResponse.model_validate(target)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    request: UpdateUserRequest,
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the supported fields of one user of the super_admin's broker.

    super_admin-only. Partial semantics: an omitted field is unchanged; an
    explicit ``null`` clears email/phone only. Role changes follow the
    invariants: a promotion to super_admin is refused outright (422, schema
    validator), and the broker's only super_admin cannot be demoted (409).
    Username/email/phone changes go through the same tenant-scoped uniqueness
    as creation, so a duplicate is a generic 409. A supplied password is
    hashed immediately; the plaintext never touches persistence or logs.
    """
    target = await _manageable_target(session, current_super_admin, user_id)
    if (
        target.role is UserRole.SUPER_ADMIN
        and request.role is not None
        and request.role is not UserRole.SUPER_ADMIN
    ):
        # The schema validator already refuses promotions to super_admin, so
        # any explicit role here would demote the broker's only super_admin.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot demote the broker's only super_admin",
        )

    provided = request.model_fields_set
    if "username" in provided:
        target.username = request.username
    if "password" in provided:
        target.password_hash = hash_password(request.password)
    if "email" in provided:
        target.email = request.email
    if "phone" in provided:
        target.phone = request.phone
    if "role" in provided:
        target.role = request.role
    if "is_active" in provided:
        target.is_active = request.is_active

    try:
        await session.commit()
    except IntegrityError:
        # Expected failure at the commit boundary: a changed username, email
        # or phone collided with the tenant-scoped unique constraints.
        await session.rollback()
        raise _duplicate_conflict()

    await session.refresh(target)
    return UserResponse.model_validate(target)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete one user of the authenticated super_admin's broker.

    super_admin-only. The broker's only super_admin cannot be deleted — that
    is necessarily the caller itself, since the database partial unique index
    guarantees at most one super_admin per broker (409). Every other
    in-tenant user is deletable; other tenants are unreachable (404).
    """
    target = await _manageable_target(session, current_super_admin, user_id)
    if target.role is UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete the broker's only super_admin",
        )
    await session.delete(target)
    await session.commit()
    # 204 No Content: nothing sensitive to return, by construction.
    return None


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
    """Load the in-tenant user ``caller`` may manage, or 404.

    Tenant isolation is structural: a target outside the caller's broker is
    reported exactly like a non-existent one, so a caller can never learn which
    user ids exist in another tenant. Role hierarchy: an admin manages customer
    records only; a super_admin holds broker-level privileges and may manage
    admins, customers and itself — every super_admin-only endpoint relies on
    that last property, because the admin branch below never fires for it.
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
