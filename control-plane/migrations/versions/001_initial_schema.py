"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("match_logic", postgresql.JSONB, nullable=False),
        sa.Column("mutate_logic", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("priority"),
    )

    op.create_table(
        "recording_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )

    op.create_table(
        "traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "recording_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recording_sessions.id"),
            nullable=False,
        ),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("original_payload", postgresql.JSONB, nullable=False),
        sa.Column("final_payload", postgresql.JSONB, nullable=False),
        sa.Column("mutation_steps", postgresql.JSONB, nullable=False),
        sa.Column(
            "action",
            sa.Enum("PROCEED", "MUTATED", "REJECT", name="actionenum"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("traces")
    op.drop_table("recording_sessions")
    op.drop_table("rules")
    op.execute("DROP TYPE IF EXISTS actionenum")
