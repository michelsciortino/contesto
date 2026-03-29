import os
import redis.asyncio as aioredis

RULES_KEY = "governor:rules"
RECORDING_KEY = "governor:recording"


def traces_key(session_id: str) -> str:
    return f"governor:traces:{session_id}"


_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        url = os.environ.get("GOVERNOR_REDIS_URL", "redis://localhost:6379/0")
        _client = aioredis.from_url(url, decode_responses=True)
    return _client
