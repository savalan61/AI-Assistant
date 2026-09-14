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

    class Config:
        env_file = ".env"


settings = Settings()
