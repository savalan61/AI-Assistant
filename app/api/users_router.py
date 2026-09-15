"""Users API: Broker Admin creates Customer Users within their own tenant."""
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_broker_manager
from app.core.security import hash_password
from app.db.database import get_db
from app.db.models import User, UserRole

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
