from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.providers.llm import LLMProviderKind


class BrokerLLMConfig(Base):
    """Broker-scoped LLM configuration for the assistant.

    Holds only non-secret metadata in the clear (which provider, which model,
    which endpoint, whether it is enabled). The API key is persisted as
    authenticated ciphertext in ``api_key_encrypted`` (see app.core.encryption)
    and is never returned by any endpoint.

    Exactly one configuration per broker is enforced at the database layer by a
    unique constraint on ``broker_id`` — not merely by application logic — so a
    concurrent create cannot silently produce a second active configuration.
    """

    __tablename__ = "broker_llm_configs"
    __table_args__ = (
        # One configuration per broker (the tenant boundary).
        UniqueConstraint("broker_id", name="uq_broker_llm_configs_broker"),
        # Non-native enum: plain VARCHAR plus a CHECK keeps the schema portable
        # (same shape on PostgreSQL and SQLite) and evolvable without native
        # enum-type alterations when provider kinds change.
        CheckConstraint("provider IN ('openai_compatible')", name="ck_broker_llm_configs_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker_id: Mapped[int] = mapped_column(Integer, ForeignKey("brokers.id"), nullable=False, index=True)
    provider: Mapped[LLMProviderKind] = mapped_column(
        Enum(LLMProviderKind, native_enum=False, length=30, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=LLMProviderKind.OPENAI_COMPATIBLE,
        server_default=LLMProviderKind.OPENAI_COMPATIBLE.value,
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Non-secret endpoint metadata (hosted vendor or self-hosted runtime).
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fernet ciphertext; the plaintext key is never persisted or logged.
    api_key_encrypted: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Enabled state: a disabled configuration is kept but cannot be tested and
    # is intended not to be selected once provider routing exists.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
