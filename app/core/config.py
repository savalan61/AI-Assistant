from pydantic_settings import BaseSettings


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

    # Encryption key for secrets stored in the database (broker LLM API keys).
    # A urlsafe-base64 Fernet key; generate one with
    # app.core.encryption.generate_encryption_key(). No committed value: an
    # empty key means credential storage is unavailable (RuntimeError -> HTTP
    # 503) rather than storing keys in a recoverable form. Never log it,
    # never commit it.
    SECRET_ENCRYPTION_KEY: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
