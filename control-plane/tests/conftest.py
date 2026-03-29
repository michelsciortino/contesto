import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from unittest.mock import AsyncMock
import app.redis_client as _redis_mod

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def client():
    from app.database import Base, get_db
    from app.main import create_app

    # ── in-memory SQLite ──────────────────────────────────────────────────────
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with session_factory() as session:
            yield session

    # ── mock Redis singleton ──────────────────────────────────────────────────
    mock_redis = AsyncMock()
    mock_redis.get.return_value = ""       # no active recording by default
    mock_redis.set.return_value = True
    mock_redis.rpush.return_value = 1
    mock_redis.lrange.return_value = []    # no buffered traces
    mock_redis.delete.return_value = 1
    mock_redis.ping.return_value = True

    original_client = _redis_mod._client
    _redis_mod._client = mock_redis        # inject directly into singleton slot

    # ── build app ────────────────────────────────────────────────────────────
    app = create_app()
    app.dependency_overrides[get_db] = override_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    # ── teardown ─────────────────────────────────────────────────────────────
    _redis_mod._client = original_client
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
