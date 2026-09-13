from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    # Composite unique constraints ensure tenant-scoped uniqueness;
    # username/email/phone may repeat across different brokers.
    __table_args__ = (
        UniqueConstraint("broker_id", "username", name="uq_users_broker_username"),
        UniqueConstraint("broker_id", "email", name="uq_users_broker_email"),
        UniqueConstraint("broker_id", "phone", name="uq_users_broker_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker_id: Mapped[int] = mapped_column(Integer, ForeignKey("brokers.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Stored as a hash; must never be recoverable in plaintext.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted ciphertext; backend needs it to connect to MT5 later.
    mt5_password_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
