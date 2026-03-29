import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Trace
from app.schemas import TraceOut, TraceDetailOut

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("", response_model=list[TraceOut])
async def list_traces(
    session_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(Trace).order_by(Trace.recorded_at.desc())
    if session_id:
        query = query.where(Trace.recording_session_id == session_id)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{trace_id}", response_model=TraceDetailOut)
async def get_trace(trace_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trace).where(Trace.trace_id == trace_id))
    trace = result.scalar_one_or_none()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace
