import json
import uuid
from datetime import datetime, timezone
from app.redis_client import get_redis, RECORDING_KEY, traces_key


async def should_record() -> str | None:
    """Returns active session_id if recording is on, else None."""
    redis = get_redis()
    val = await redis.get(RECORDING_KEY)
    return val if val else None


async def push_trace(session_id: str, trace: dict) -> None:
    """Buffer a trace in Redis during the recording session."""
    redis = get_redis()
    await redis.rpush(traces_key(session_id), json.dumps(trace))


async def flush_to_db(session_id: str, db) -> int:
    """
    Read all buffered traces from Redis and bulk-insert into PostgreSQL.
    Returns the number of traces flushed.
    Raises on DB error — caller must handle (do not delete Redis list on failure).
    """
    from app.models import Trace, ActionEnum

    redis = get_redis()
    key = traces_key(session_id)
    raw_traces = await redis.lrange(key, 0, -1)

    if not raw_traces:
        return 0

    db_traces = []
    for raw in raw_traces:
        t = json.loads(raw)
        db_traces.append(Trace(
            id=uuid.uuid4(),
            trace_id=uuid.UUID(t["trace_id"]),
            recording_session_id=uuid.UUID(session_id),
            model=t["model"],
            original_payload=t["original_payload"],
            final_payload=t["final_payload"],
            mutation_steps=t["mutation_steps"],
            action=ActionEnum(t["action"]),
            recorded_at=datetime.now(timezone.utc),
        ))

    db.add_all(db_traces)
    await db.commit()

    # Only delete Redis list after successful DB commit
    await redis.delete(key)
    return len(db_traces)
