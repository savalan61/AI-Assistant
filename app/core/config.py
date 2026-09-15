import logging
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# The dotenv file Settings reads. One constant so the unknown-key warning below
# inspects exactly the file pydantic-settings consumes.
ENV_FILE = ".env"


class Settings(BaseSettings):
    # Reads from .env file; no secrets are hard-coded.
    APP_NAME: str = "AI Financial Assistant"
    APP_ENV: str = "development"
    DEBUG: bool = True
    DATABASE_URL: str = "postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/financial_assistant"

    # Authentication settings. SECRET_KEY has no committed value: it must come
    # from the environment (.env / process env). The empty default keeps app
    # import working; token operations fail closed (RuntimeError) until it is
    # configured. Never log it, never commit it.
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # LLM settings for the OpenAI-compatible chat/completions adapter. The
    # adapter is vendor-agnostic: point base_url at any server exposing the
    # /chat/completions shape (hosted API or self-hosted runtime). No default
    # model and no committed key: with an empty key the adapter is unavailable
    # (RuntimeError -> HTTP 503) rather than falling back to a placeholder.
    # Never log the key, never commit it.
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = ""
    LLM_TIMEOUT_SECONDS: float = 30.0

    # Maximum Agent requests per authenticated user per UTC day. Configurable
    # so deployments can tune it; checked in-process before any MT5 read or
    # LLM call. Must be at least 1 (validated by the limiter).
    AGENT_DAILY_REQUEST_LIMIT: int = 50

    # Agent request/prompt size limits. The message cap is enforced at the API
    # validation boundary (422); the trade cap and the total prompt cap are
    # applied deterministically while building the prompt, so an unusually
    # large financial context can never produce an unbounded outbound payload.
    AGENT_MAX_MESSAGE_LENGTH: int = 2000
    AGENT_MAX_PROMPT_TRADES: int = 50
    AGENT_MAX_PROMPT_CHARS: int = 24000

    # Outbound LLM data policy: what may be rendered into an external model
    # prompt. All True reproduces the original behaviour; each flag removes a
    # specific data class from the prompt (see app/services/agent/egress.py).
    # These are the seam for a future per-broker consent/data-processing policy.
    LLM_SEND_TRADE_HISTORY: bool = True
    LLM_SEND_ACCOUNT_BALANCES: bool = True
    LLM_SEND_POSITION_PRICING: bool = True

    # Login brute-force protection (in-process, per client IP and per submitted
    # username). After LOGIN_MAX_FAILURES failures inside
    # LOGIN_FAILURE_WINDOW_SECONDS the login endpoint answers a generic 429.
    # Counters reset on a successful login and on process restart.
    LOGIN_MAX_FAILURES: int = 10
    LOGIN_FAILURE_WINDOW_SECONDS: int = 300

    # Encryption key for secrets stored in the database (broker LLM API keys).
    # A urlsafe-base64 Fernet key; generate one with
    # app.core.encryption.generate_encryption_key(). No committed value: an
    # empty key means credential storage is unavailable (RuntimeError -> HTTP
    # 503) rather than storing keys in a recoverable form. Never log it,
    # never commit it.
    SECRET_ENCRYPTION_KEY: str = ""

    # Unknown keys in the env FILE are ignored rather than fatal, for two
    # reasons that both come from the same root: an unrecognised key is
    # usually a *mistyped* setting name, and settings frequently hold
    # credentials.
    #
    #   1. Availability. One stray key (a local-only helper variable, or an
    #      unrelated variable someone added to .env) would otherwise abort the
    #      process at import time — the failure is raised while this module is
    #      being imported, so the app cannot start at all.
    #   2. Disclosure. Pydantic renders the offending VALUE inside an
    #      "extra_forbidden" ValidationError, so a secret stored under a
    #      mistyped name is printed verbatim in the traceback/log. With
    #      "ignore" no unknown value reaches an error message at all.
    #
    # Typo detection is preserved without exposing values: unrecognised keys
    # are reported by NAME only (see _warn_unknown_env_file_keys), which still
    # reveals the mistake while never reading the value.
    #
    # Note this applies to the env *file* only; unrelated process environment
    # variables were never treated as settings inputs by pydantic-settings, so
    # a normal deployment environment (PATH, CI variables, ...) is unaffected.
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


def _warn_unknown_env_file_keys(env_file: str | Path | None = ENV_FILE) -> list[str]:
    """Warn, by NAME only, about env-file keys that match no Settings field.

    The value of an unknown key is never read or rendered: an unrecognised key
    is frequently a mistyped field name whose value is a real credential.
    Returns the ignored names (used by tests) and never raises.
    """
    if env_file is None or env_file == "":
        return []
    path = Path(env_file)
    if not path.is_file():
        return []

    known = {name.lower() for name in Settings.model_fields}
    unknown: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name and name.lower() not in known:
            unknown.add(name)

    if unknown:
        logger.warning(
            "Ignoring %d unrecognised environment variable(s) in %s: %s",
            len(unknown),
            path,
            ", ".join(sorted(unknown)),
        )
    return sorted(unknown)


def _format_settings_error(exc: ValidationError) -> str:
    """Render a Settings failure using field paths and error kinds only.

    Deliberately drops everything pydantic attaches to an error apart from its
    ``loc`` and ``type``: the submitted value must never appear in a
    configuration error, and ``loc`` holds a setting *name*, not a value.
    """
    problems = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ())) or "<settings>"
        problems.append(f"{loc} ({error.get('type', 'invalid')})")
    return (
        "Invalid configuration: "
        + "; ".join(problems)
        + ". Check .env / environment values for the named setting(s)."
    )


def _settings_for_env_file(env_file: str | Path | None) -> type[Settings]:
    """Build a Settings variant that reads ``env_file`` as its dotenv file.

    pydantic-settings also accepts the dotenv path as an ``_env_file`` *init*
    argument, but pydantic's ``@dataclass_transform`` makes type checkers
    synthesise ``__init__`` from the model fields alone, so that argument is
    invisible to Pylance/pyright ("No parameter named '_env_file'"). Selecting
    the file through ``model_config`` is the statically visible equivalent and
    behaves identically at runtime; ``None`` reads no dotenv file at all.
    """
    config = SettingsConfigDict(env_file=env_file, extra="ignore")
    return type(Settings.__name__, (Settings,), {"model_config": config})


def load_settings(*, env_file: str | Path | None = ENV_FILE) -> Settings:
    """Build Settings, failing closed with a message that never echoes a value.

    A configuration value is often a credential, so an invalid one must fail
    the process without that value reaching the traceback. Importing this
    module therefore surfaces, at worst, the NAME of the offending setting.

    ``env_file`` selects the dotenv file to read (``None`` reads none). The
    default path instantiates ``Settings`` unchanged, so the production
    settings object keeps its normal identity and behaviour.
    """
    _warn_unknown_env_file_keys(env_file)
    settings_cls: type[Settings] = Settings
    if env_file is None or Path(env_file) != Path(ENV_FILE):
        settings_cls = _settings_for_env_file(env_file)
    try:
        return settings_cls()
    except ValidationError as exc:
        raise RuntimeError(_format_settings_error(exc)) from None


settings = load_settings()
