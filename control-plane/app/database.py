from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import os


DATABASE_URL = os.environ.get(
    "GOVERNOR_DATABASE_URL",
    "postgresql+asyncpg://governor:governor_secret@localhost:5432/governor",
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionFactory() as session:
        yield session
