from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings

# Async engine and session factory are configured from app settings.
engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# FastAPI dependency providing an async DB session per request.
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
