import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel


# ── Rules ─────────────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    name: str
    priority: int
    is_active: bool = True
    match_logic: dict[str, Any]
    mutate_logic: dict[str, Any]


class RuleUpdate(BaseModel):
    name: str | None = None
    priority: int | None = None
    is_active: bool | None = None
    match_logic: dict[str, Any] | None = None
    mutate_logic: dict[str, Any] | None = None


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    priority: int
    is_active: bool
    match_logic: dict[str, Any]
    mutate_logic: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


# ── Recording ─────────────────────────────────────────────────────────────────

class RecordingStartResponse(BaseModel):
    session_id: uuid.UUID
    started_at: datetime


class RecordingStatusResponse(BaseModel):
    is_active: bool
    session_id: uuid.UUID | None
    started_at: datetime | None


class RecordingStopResponse(BaseModel):
    session_id: uuid.UUID
    traces_flushed: int
    stopped_at: datetime


# ── Recordings list ───────────────────────────────────────────────────────────

class RecordingListOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    stopped_at: datetime | None
    is_active: bool
    trace_count: int


# ── Traces ────────────────────────────────────────────────────────────────────

class TraceOut(BaseModel):
    id: uuid.UUID
    trace_id: uuid.UUID
    recording_session_id: uuid.UUID
    model: str
    action: str
    recorded_at: datetime
    model_config = {"from_attributes": True}


class TraceDetailOut(TraceOut):
    original_payload: dict[str, Any]
    final_payload: dict[str, Any]
    mutation_steps: list[dict[str, Any]]
