from fastapi import APIRouter
from sqlalchemy import text
from app.database import engine
from app.redis_client import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    status = {"db": "ok", "redis": "ok"}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        status["db"] = "error"
    try:
        redis = get_redis()
        await redis.ping()
    except Exception:
        status["redis"] = "error"
    ok = all(v == "ok" for v in status.values())
    return {"status": "ok" if ok else "degraded", "components": status}
