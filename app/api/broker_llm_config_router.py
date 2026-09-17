"""Broker LLM configuration API (super_admin only).

This deployment serves ONE broker, whose own LLM credentials are managed
here. The configuration always belongs to that single brokers row: there is no
broker_id path segment, query parameter or body field, so a client can never
select a different one — and never reach another broker's credential.

The API key is write-only. It may be sent in a PUT body, is stored as
authenticated ciphertext, and is structurally absent from every response
schema — responses expose only safe metadata (provider, model, endpoint,
enabled/configured state).
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.blocking import run_llm_call
from app.core.dependencies import get_current_super_admin, get_llm_connection_tester
from app.core.encryption import EncryptionError, decrypt_secret, encrypt_secret
from app.core.url_security import UrlSecurityError, validate_llm_base_url
from app.db.database import get_db
from app.db.models import BrokerLLMConfig, User
from app.providers.llm import LLMProviderKind
from app.services.broker_llm_config import LLMConnectionStatus, LLMConnectionTester

router = APIRouter(prefix="/broker/llm-config", tags=["broker"])

# An absolute http(s) endpoint is required by the OpenAI-compatible adapter.
# No URL library is introduced for this structural check.
_BASE_URL_PATTERN = re.compile(r"^https?://\S+$")


# Only the fields a broker may set. Deliberately excluded: id, broker_id,
# timestamps, and anything derived. extra="forbid" turns a supplied broker_id
# (or any unknown field) into a 422 rather than silently ignoring it, so a
# broker-forging attempt fails loudly.
class BrokerLLMConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: LLMProviderKind = LLMProviderKind.OPENAI_COMPATIBLE
    # Length caps match the database columns so oversized input is rejected as
    # 422 instead of surfacing as a database error.
    model: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=255)
    # Write-only: encrypted before persistence, never returned.
    api_key: str = Field(min_length=8, max_length=512)
    # Optional: omitted on create means enabled; omitted on update keeps the
    # current enabled state.
    is_active: bool | None = None

    @field_validator("model", "api_key")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not _BASE_URL_PATTERN.match(value.strip()):
            raise ValueError("base_url must be an absolute http(s) URL")
        return value.strip()


# Safe projection of the configuration row. Explicitly constructed (not
# from_attributes) so the ciphertext column can never be reached by name even
# if the model gains fields later.
class BrokerLLMConfigResponse(BaseModel):
    provider: LLMProviderKind
    model: str
    base_url: str
    is_active: bool
    # Configured state only; the key itself is never exposed.
    api_key_set: bool
    updated_at: datetime


class LLMConnectionTestResponse(BaseModel):
    status: LLMConnectionStatus
    provider: LLMProviderKind
    model: str
    # Generic, credential-free explanation when status is FAILED.
    detail: str | None = None


def _to_response(config: BrokerLLMConfig) -> BrokerLLMConfigResponse:
    return BrokerLLMConfigResponse(
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        is_active=config.is_active,
        api_key_set=bool(config.api_key_encrypted),
        updated_at=config.updated_at,
    )


async def _load_config(session: AsyncSession, broker_id: int) -> BrokerLLMConfig | None:
    """Load the authenticated broker's configuration (at most one row)."""
    result = await session.execute(select(BrokerLLMConfig).where(BrokerLLMConfig.broker_id == broker_id))
    return result.scalar_one_or_none()


def _not_configured() -> HTTPException:
    # Generic: says nothing about internal state.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LLM configuration is not set for this broker")


def _endpoint_not_permitted(reason: str) -> HTTPException:
    # The reason names the policy failure (scheme, or the kind of blocked
    # address); it never contains a credential.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"base_url is not an allowed outbound endpoint: {reason}",
    )


def _credentials_unavailable() -> HTTPException:
    # Encryption is misconfigured or the stored ciphertext cannot be opened.
    # The reason is never disclosed and no secret is included.
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="LLM credential storage is temporarily unavailable",
    )


@router.get("", response_model=BrokerLLMConfigResponse)
async def get_llm_config(
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
) -> BrokerLLMConfigResponse:
    """Return the authenticated broker's LLM configuration metadata.

    super_admin-only (get_current_super_admin); admins and customers are
    rejected with 403. The configuration is this deployment's own (the caller's
    own broker row), so no request input selects it.
    """
    config = await _load_config(session, current_super_admin.broker_id)
    if config is None:
        raise _not_configured()
    return _to_response(config)


@router.put("", response_model=BrokerLLMConfigResponse)
async def upsert_llm_config(
    request: BrokerLLMConfigRequest,
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
) -> BrokerLLMConfigResponse:
    """Create or replace the authenticated broker's LLM configuration.

    The API key is encrypted before it reaches the database; on any encryption
    failure the request fails closed (503) and nothing is written. broker_id is
    always this deployment's broker, never request input, so a caller cannot
    configure a different broker.
    """
    # Security policy first: the broker chooses this endpoint and the server
    # will POST to it with the broker's key, so it is validated (scheme, host,
    # embedded credentials, and every address the hostname resolves to) before
    # anything is encrypted or stored. A rejected endpoint writes nothing.
    try:
        validate_llm_base_url(request.base_url)
    except UrlSecurityError as exc:
        raise _endpoint_not_permitted(str(exc))

    try:
        api_key_encrypted = encrypt_secret(request.api_key)
    except EncryptionError:
        raise _credentials_unavailable()

    config = await _load_config(session, current_super_admin.broker_id)
    if config is None:
        config = BrokerLLMConfig(
            broker_id=current_super_admin.broker_id,
            provider=request.provider,
            model=request.model,
            base_url=request.base_url,
            api_key_encrypted=api_key_encrypted,
            # A new configuration is enabled unless explicitly disabled.
            is_active=True if request.is_active is None else request.is_active,
        )
        session.add(config)
    else:
        config.provider = request.provider
        config.model = request.model
        config.base_url = request.base_url
        config.api_key_encrypted = api_key_encrypted
        if request.is_active is not None:
            config.is_active = request.is_active

    try:
        await session.commit()
    except IntegrityError:
        # Expected at the commit boundary: one-configuration-per-broker is a
        # unique constraint, so a concurrent create is a conflict, not a 500.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LLM configuration was modified concurrently; please retry",
        )

    await session.refresh(config)
    return _to_response(config)


@router.post("/test", response_model=LLMConnectionTestResponse)
async def test_llm_connection(
    current_super_admin: User = Depends(get_current_super_admin),
    session: AsyncSession = Depends(get_db),
    tester: LLMConnectionTester = Depends(get_llm_connection_tester),
) -> LLMConnectionTestResponse:
    """Exercise the stored credentials through the provider boundary.

    The blocking provider call is offloaded through the outbound-LLM boundary
    (run_llm_call, the same boundary /agent uses), so the event loop is never
    blocked and no MT5 worker thread is held for the model round trip. A
    provider failure is reported
    as a safe FAILED result (200) that carries no key, no endpoint detail and no
    provider message; configuration problems (missing or disabled config,
    undecryptable credentials) are explicit errors.
    """
    config = await _load_config(session, current_super_admin.broker_id)
    if config is None:
        raise _not_configured()
    if not config.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LLM configuration is disabled")

    # Defense in depth before the outbound probe: the stored endpoint was
    # validated on write, but a row edited outside the API must not turn this
    # endpoint into a request-forgery primitive either.
    try:
        validate_llm_base_url(config.base_url, resolve_host=False)
    except UrlSecurityError as exc:
        raise _endpoint_not_permitted(str(exc))

    try:
        api_key = decrypt_secret(config.api_key_encrypted)
    except EncryptionError:
        raise _credentials_unavailable()

    try:
        await run_llm_call(tester.check, api_key, config.base_url, config.model)
    except RuntimeError:
        # Deliberately fixed text: the provider's own message, the URL, the
        # request payload and the key never reach the client.
        return LLMConnectionTestResponse(
            status=LLMConnectionStatus.FAILED,
            provider=config.provider,
            model=config.model,
            detail="The configured LLM provider could not be reached or rejected the credentials",
        )

    return LLMConnectionTestResponse(
        status=LLMConnectionStatus.OK,
        provider=config.provider,
        model=config.model,
    )
