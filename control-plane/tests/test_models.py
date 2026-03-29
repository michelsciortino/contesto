import pytest
from app.models import Rule, RecordingSession, Trace


def test_rule_model_has_required_fields():
    cols = {c.name for c in Rule.__table__.columns}
    assert {"id", "name", "priority", "is_active", "match_logic", "mutate_logic"} <= cols


def test_trace_model_references_session():
    fk_targets = {fk.target_fullname for col in Trace.__table__.columns for fk in col.foreign_keys}
    assert "recording_sessions.id" in fk_targets


def test_priority_is_unique():
    priority_unique = any(
        list(c.columns.keys()) == ["priority"]
        for c in Rule.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    )
    assert priority_unique
