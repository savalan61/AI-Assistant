"""Resolve a broker's active LLM provider from its stored configuration.

Resolution is by ``broker_id``, supplied by the application composition boundary
from the authenticated database user — never from an Agent request body. This is
the only place that turns a stored ciphertext into a live credential for agent
traffic, and it is deliberately conservative about what it reports:

* no configuration row, or a disabled one -> ``None``. The caller then uses the
  shared free pool; that is a deliberate broker state, not an error.
* an active configuration that cannot become a usable provider (undecryptable
  ciphertext, missing encryption key, unsupported provider kind, or a rejected
  credential shape) -> ``BrokerLLMConfigurationError``. The caller fails safely
  instead of silently consuming the system free pool.
* an active usable configuration -> the provider, ready to call.

The plaintext key exists only inside this function's frame and the provider it
builds: it is never returned, logged, or embedded in an error message.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import EncryptionError, decrypt_secret
from app.core.url_security import UrlSecurityError, validate_llm_base_url
from app.db.models import BrokerLLMConfig
from app.providers.llm import LLMProvider, LLMProviderKind
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider


class BrokerLLMConfigurationError(RuntimeError):
    """A broker's stored LLM configuration exists but cannot be used.

    Deliberately its own type (rather than a bare RuntimeError) so the caller
    can fail safely instead of treating it as an outage of a shared provider.
    """


async def resolve_broker_llm_provider(session: AsyncSession, broker_id: int) -> LLMProvider | None:
    """Return the broker's active provider, or ``None`` when none is active."""
    config = await _load_active_config(session, broker_id)
    if config is None:
        return None

    if config.provider is not LLMProviderKind.OPENAI_COMPATIBLE:
        # The database CHECK constraint allows only implemented kinds; this
        # guard keeps the resolver explicit as more kinds are added.
        raise BrokerLLMConfigurationError("unsupported broker LLM provider kind")

    try:
        # Re-checked here, immediately before an outbound request, so a row
        # edited outside the API (or one written before this policy existed)
        # still cannot make the server call a loopback/private endpoint.
        # ``resolve_host=False``: the authoritative DNS check runs on write, and
        # a lookup on every agent request would add latency to the hot path.
        validate_llm_base_url(config.base_url, resolve_host=False)
    except UrlSecurityError as exc:
        raise BrokerLLMConfigurationError("broker LLM endpoint is not permitted") from exc

    try:
        api_key = decrypt_secret(config.api_key_encrypted)
    except EncryptionError as exc:
        # A configuration problem, not a transient outage: fail rather than let
        # the broker's traffic silently move onto the shared free pool.
        raise BrokerLLMConfigurationError("broker LLM credentials are unavailable") from exc

    try:
        return OpenAICompatibleLLMProvider(
            api_key=api_key,
            base_url=config.base_url,
            model=config.model,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        )
    except RuntimeError as exc:
        # The adapter rejects blank/placeholder credentials at construction:
        # that is a stored-configuration defect, not a provider outage.
        raise BrokerLLMConfigurationError("broker LLM configuration is invalid") from exc


async def _load_active_config(session: AsyncSession, broker_id: int) -> BrokerLLMConfig | None:
    """Load the broker's enabled configuration (at most one row per broker)."""
    result = await session.execute(
        select(BrokerLLMConfig).where(
            BrokerLLMConfig.broker_id == broker_id,
            BrokerLLMConfig.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()
