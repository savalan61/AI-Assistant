from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRole(StrEnum):
    """User roles within a broker tenant.

    A StrEnum (not plain strings) so the role is a typed value everywhere and
    new roles can only be introduced deliberately. The database stores the
    plain value ("super_admin" / "admin" / "customer"), matching StrEnum
    semantics.

    Semantics:
    - SUPER_ADMIN: exactly one per Broker (enforced by a partial unique
      index in the database, not just application logic); manages admins and
      customers; the broker-level owner/manager.
    - ADMIN: multiple per Broker; manages customers; cannot manage admins or
      broker-level settings.
    - CUSTOMER: cannot manage users; uses the normal financial/AI features.
    """

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    CUSTOMER = "customer"


class User(Base):
    __tablename__ = "users"
    # Composite unique constraints ensure tenant-scoped uniqueness;
    # login/email/phone may repeat across different brokers.
    __table_args__ = (
        UniqueConstraint("broker_id", "login", name="uq_users_broker_login"),
        UniqueConstraint("broker_id", "email", name="uq_users_broker_email"),
        UniqueConstraint("broker_id", "phone", name="uq_users_broker_phone"),
        # Non-native enum: a plain VARCHAR plus a CHECK constraint keeps the
        # schema portable (same shape on PostgreSQL and SQLite) and evolvable
        # without native enum-type alterations when roles change.
        CheckConstraint("role IN ('super_admin', 'admin', 'customer')", name="ck_users_role"),
        # Exactly one super_admin per Broker, enforced at the database layer:
        # a partial unique index admits at most one row per broker_id among
        # super_admin rows. SQLite supports partial unique indexes, so the
        # application tests exercise the real constraint, not a re-implementation.
        Index(
            "uq_users_broker_super_admin",
            "broker_id",
            unique=True,
            sqlite_where=text("role = 'super_admin'"),
            postgresql_where=text("role = 'super_admin'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker_id: Mapped[int] = mapped_column(Integer, ForeignKey("brokers.id"), nullable=False, index=True)
    # THE single user identity, and the only one the system has: it is both the
    # application login and the MT5 account/login number. Kept as a string so a
    # leading zero survives (never converted to int). Replaces the former
    # username column, which was migrated into this one, and the former
    # mt5_login column, which duplicated it.
    login: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Stored as a hash; must never be recoverable in plaintext.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # The user's MT5 server, stored per user rather than derived from
    # Broker.mt5_server, so one broker can host customers on different MT5
    # servers and an administrator can provision it explicitly. Stays NULL on
    # rows provisioned the older way: credential resolution then falls back to
    # Broker.mt5_server, so existing behaviour is unchanged. The MT5 account
    # number is `login` — there is deliberately no second account column.
    mt5_server: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Fernet ciphertext of the user's MT5 INVESTOR (read-only) password. The
    # trading/master password is never requested, stored or used: only a
    # read-only credential can be provisioned, and no API ever returns it.
    mt5_password_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Role defaults to customer (least privilege); existing rows are backfilled
    # to it by migration. Authorization must read this from the database User,
    # never from a token claim.
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=UserRole.CUSTOMER,
        server_default=UserRole.CUSTOMER.value,
    )
