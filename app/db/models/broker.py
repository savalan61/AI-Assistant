from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# The broker this deployment serves. This is a ONE-BROKER product: exactly one
# brokers row must exist, and every request is bound to it (app/core/
# dependencies.load_deployment_broker refuses to serve a database with none or
# several), so this table is the deployment's canonical broker configuration —
# name, MT5 server, suspended state — rather than a customer registry. Users and
# the broker's LLM configuration reference it by id.
class Broker(Base):
    __tablename__ = "brokers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    mt5_server: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
