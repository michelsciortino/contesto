import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import RecordingSession
from app.schemas import RecordingStartResponse, RecordingStatusResponse, RecordingStopResponse
from app.redis_client import get_redis, RECORDING_KEY
from app.recorder import flush_to_db

router = APIRouter(prefix="/recording", tags=["recording"])


@router.post("/start", response_model=RecordingStartResponse)
async def start_recording(db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(RecordingSession).where(RecordingSession.is_active == True)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A recording session is already active")
    session = RecordingSession()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    redis = get_redis()
    await redis.set(RECORDING_KEY, str(session.id))
    return RecordingStartResponse(session_id=session.id, started_at=session.started_at)


@router.post("/stop", response_model=RecordingStopResponse)
async def stop_recording(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RecordingSession).where(RecordingSession.is_active == True)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No active recording session")
    redis = get_redis()
    await redis.set(RECORDING_KEY, "")
    flushed = await flush_to_db(str(session.id), db)
    session.is_active = False
    session.stopped_at = datetime.now(timezone.utc)
    await db.commit()
    return RecordingStopResponse(
        session_id=session.id,
        traces_flushed=flushed,
        stopped_at=session.stopped_at,
    )


@router.get("/status", response_model=RecordingStatusResponse)
async def recording_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RecordingSession).where(RecordingSession.is_active == True)
    )
    session = result.scalar_one_or_none()
    if not session:
        return RecordingStatusResponse(is_active=False, session_id=None, started_at=None)
    return RecordingStatusResponse(
        is_active=True, session_id=session.id, started_at=session.started_at
    )
