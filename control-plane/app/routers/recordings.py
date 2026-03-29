import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from app.database import get_db
from app.models import RecordingSession, Trace
from app.schemas import RecordingListOut

router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.get("", response_model=list[RecordingListOut])
async def list_recordings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RecordingSession, func.count(Trace.id).label("trace_count"))
        .outerjoin(Trace, Trace.recording_session_id == RecordingSession.id)
        .group_by(RecordingSession.id)
        .order_by(RecordingSession.started_at.desc())
    )
    rows = result.all()
    return [
        RecordingListOut(
            id=r.RecordingSession.id,
            started_at=r.RecordingSession.started_at,
            stopped_at=r.RecordingSession.stopped_at,
            is_active=r.RecordingSession.is_active,
            trace_count=r.trace_count,
        )
        for r in rows
    ]


@router.delete("/{session_id}", status_code=204)
async def delete_recording(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    session = await db.get(RecordingSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Recording session not found")
    await db.execute(delete(Trace).where(Trace.recording_session_id == session_id))
    await db.delete(session)
    await db.commit()
