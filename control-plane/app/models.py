import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, Enum, UniqueConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import TIMESTAMP, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class ActionEnum(str, enum.Enum):
    PROCEED = "PROCEED"
    MUTATED = "MUTATED"
    REJECT = "REJECT"


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("priority"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    match_logic: Mapped[dict] = mapped_column(JSON, nullable=False)
    mutate_logic: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class RecordingSession(Base):
    __tablename__ = "recording_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    stopped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    traces: Mapped[list["Trace"]] = relationship("Trace", back_populates="session")


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recording_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recording_sessions.id"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    original_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    final_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    mutation_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    action: Mapped[ActionEnum] = mapped_column(Enum(ActionEnum), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    session: Mapped["RecordingSession"] = relationship("RecordingSession", back_populates="traces")
