"""Users API: Broker Admin creates Customer Users within their own tenant."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_broker_admin
from app.core.security import hash_password
from app.db.database import get_db
from app.db.models import User, UserRole

router = APIRouter(prefix="/users", tags=["users"])


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
    current_admin: User = Depends(get_current_broker_admin),
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Create a Customer User inside the authenticated admin's broker.

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
