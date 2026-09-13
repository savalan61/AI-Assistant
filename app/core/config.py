from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Reads from .env file; no secrets are hard-coded.
    APP_NAME: str = "AI Financial Assistant"
    APP_ENV: str = "development"
    DEBUG: bool = True
    DATABASE_URL: str = "postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/financial_assistant"

    class Config:
        env_file = ".env"


settings = Settings()
