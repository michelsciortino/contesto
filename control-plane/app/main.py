import asyncio
import logging
import os
import sys

# Allow the app to find proto-generated files when running inside Docker
# (they are mounted at /app/interceptor_pb2.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import grpc
from contextlib import asynccontextmanager
from fastapi import FastAPI
import interceptor_pb2_grpc
from app.grpc_servicer import GovernorServicer
from app.database import engine, Base, AsyncSessionFactory
from app.matcher import refresh_rules_cache
from app.models import Rule, RecordingSession
from app.redis_client import get_redis, RECORDING_KEY
from sqlalchemy import select
from app.routers import rules, recording, recordings, traces, health

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _bootstrap_redis_from_db() -> None:
    """Load active rules + recover recording state into Redis on startup."""
    async with AsyncSessionFactory() as db:
        result = await db.execute(
            select(Rule).where(Rule.is_active == True).order_by(Rule.priority)
        )
        db_rules = result.scalars().all()
        await refresh_rules_cache([
            {
                "id": str(r.id),
                "name": r.name,
                "priority": r.priority,
                "match_logic": r.match_logic,
                "mutate_logic": r.mutate_logic,
            }
            for r in db_rules
        ])
        logger.info(f"Loaded {len(db_rules)} active rules into Redis cache")

        # Recover active recording session
        active = await db.execute(
            select(RecordingSession).where(RecordingSession.is_active == True)
        )
        session = active.scalar_one_or_none()
        redis = get_redis()
        if session:
            await redis.set(RECORDING_KEY, str(session.id))
            logger.info(f"Recovered active recording session {session.id}")
        else:
            current = await redis.get(RECORDING_KEY)
            if not current:
                await redis.set(RECORDING_KEY, "")


async def _run_grpc() -> None:
    server = grpc.aio.server()
    interceptor_pb2_grpc.add_ContextServiceServicer_to_server(GovernorServicer(), server)
    server.add_insecure_port("[::]:50051")
    logger.info("gRPC server starting on :50051")
    await server.start()
    await server.wait_for_termination()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-create tables (idempotent; Alembic handles migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _bootstrap_redis_from_db()
    except Exception as e:
        logger.warning(f"Redis bootstrap failed (continuing fail-open): {e}")
    asyncio.create_task(_run_grpc())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Governor Control Plane", lifespan=lifespan)
    app.include_router(rules.router)
    app.include_router(recording.router)
    app.include_router(recordings.router)
    app.include_router(traces.router)
    app.include_router(health.router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False)
