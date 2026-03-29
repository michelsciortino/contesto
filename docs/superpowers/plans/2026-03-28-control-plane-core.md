# Control Plane Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dummy `control-plane/server.py` with a production-grade FastAPI + gRPC service implementing the Matcher → Mutator → Recorder pipeline, backed by PostgreSQL and Redis.

**Architecture:** A single container runs both a gRPC server (port 50051, serves the LiteLLM hook) and a FastAPI HTTP server (port 8080, serves the UI). Rules are stored in PostgreSQL and cached in Redis on every write. During a recording session, traces are buffered in a Redis List and bulk-flushed to PostgreSQL on stop.

**Tech Stack:** Python 3.11, FastAPI, grpcio/grpcio-tools, SQLAlchemy (async), asyncpg, redis-py (async), json-logic-py, Alembic, pytest, pytest-asyncio, httpx (test client), docker-compose

**Spec:** `docs/superpowers/specs/2026-03-28-control-plane-core-design.md`

---

## File Map

```
control-plane/
├── app/
│   ├── main.py                      # CREATE — FastAPI app + gRPC server bootstrap
│   ├── grpc_servicer.py             # CREATE — ContextServicer: Matcher → Mutator → Recorder
│   ├── matcher.py                   # CREATE — Read rules from Redis, evaluate match_logic
│   ├── mutator.py                   # CREATE — Apply mutate_logic pipeline in priority order
│   ├── recorder.py                  # CREATE — RPUSH to Redis; bulk flush to DB on stop
│   ├── models.py                    # CREATE — SQLAlchemy models: Rule, RecordingSession, Trace
│   ├── database.py                  # CREATE — Async engine + session factory
│   ├── redis_client.py              # CREATE — Async Redis client singleton + key constants
│   ├── schemas.py                   # CREATE — Pydantic request/response schemas
│   └── routers/
│       ├── rules.py                 # CREATE — CRUD endpoints
│       ├── recording.py             # CREATE — start/stop/status
│       ├── recordings.py            # CREATE — list/delete historical sessions
│       ├── traces.py                # CREATE — list + detail
│       └── health.py                # CREATE — DB + Redis connectivity
├── migrations/                      # CREATE — Alembic migrations directory
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── tests/
│   ├── conftest.py                  # CREATE — pytest fixtures: DB, Redis, test client
│   ├── test_matcher.py              # CREATE
│   ├── test_mutator.py              # CREATE
│   ├── test_recorder.py             # CREATE
│   ├── test_grpc_servicer.py        # CREATE
│   └── test_routers/
│       ├── test_rules.py            # CREATE
│       ├── test_recording.py        # CREATE
│       └── test_traces.py           # CREATE
├── Dockerfile                       # MODIFY — add new deps
├── requirements.txt                 # MODIFY — full dependency list
server.py                            # DELETE — replaced by app/
```

**docker-compose.yml** — MODIFY: add `governor-db`, `redis`, update `control-plane` service.

---

## Task 1: Dependencies & docker-compose

**Files:**
- Modify: `control-plane/requirements.txt`
- Modify: `control-plane/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
grpcio==1.64.0
grpcio-tools==1.64.0
sqlalchemy[asyncio]==2.0.35
asyncpg==0.29.0
alembic==1.13.2
redis[asyncio]==5.0.8
json-logic-py==0.6.4
pydantic==2.8.0
pydantic-settings==2.4.0
httpx==0.27.0
pytest==8.3.0
pytest-asyncio==0.23.8
```

- [ ] **Step 2: Update Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini .
# Proto generated files mounted via docker-compose volume
CMD ["python", "-m", "app.main"]
```

- [ ] **Step 3: Add governor-db and redis to docker-compose.yml**

Add after existing services:

```yaml
  governor-db:
    image: postgres:16-alpine
    container_name: governor-db
    environment:
      POSTGRES_USER: governor
      POSTGRES_PASSWORD: ${GOVERNOR_DB_PASSWORD}
      POSTGRES_DB: governor
    env_file:
      - .env
    volumes:
      - governor_postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U governor -d governor"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: governor-redis
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
```

Update `control-plane` service:
```yaml
  control-plane:
    build:
      context: .
      dockerfile: control-plane/Dockerfile
    container_name: governor-control-plane
    volumes:
      - ./control-plane/app:/app/app:ro
      - ./protos/generated/interceptor_pb2.py:/app/interceptor_pb2.py
      - ./protos/generated/interceptor_pb2_grpc.py:/app/interceptor_pb2_grpc.py
    ports:
      - "50051:50051"
      - "8080:8080"
    env_file:
      - .env
    depends_on:
      governor-db:
        condition: service_healthy
      redis:
        condition: service_started
```

Add to `volumes` section:
```yaml
  governor_postgres_data:
  redis_data:
```

- [ ] **Step 4: Add env vars to .env**

```
GOVERNOR_DB_PASSWORD=governor_secret
GOVERNOR_DATABASE_URL=postgresql+asyncpg://governor:governor_secret@governor-db:5432/governor
GOVERNOR_REDIS_URL=redis://redis:6379/0
```

- [ ] **Step 5: Commit**

```bash
git add control-plane/requirements.txt control-plane/Dockerfile docker-compose.yml .env
git commit -m "chore: add governor-db, redis to docker-compose and update control-plane deps"
```

---

## Task 2: Database models & connection

**Files:**
- Create: `control-plane/app/database.py`
- Create: `control-plane/app/models.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_models.py
import pytest
from app.models import Rule, RecordingSession, Trace

def test_rule_model_has_required_fields():
    cols = {c.name for c in Rule.__table__.columns}
    assert {"id", "name", "priority", "is_active", "match_logic", "mutate_logic"} <= cols

def test_trace_model_references_session():
    fk_targets = {fk.target_fullname for col in Trace.__table__.columns for fk in col.foreign_keys}
    assert "recording_sessions.id" in fk_targets

def test_priority_is_unique():
    unique_constraints = [c for c in Rule.__table__.constraints if hasattr(c, 'columns')]
    priority_unique = any(
        list(c.columns.keys()) == ["priority"]
        for c in Rule.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    )
    assert priority_unique
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd control-plane && pytest tests/test_models.py -v
```
Expected: FAIL — `app.models` not found.

- [ ] **Step 3: Implement database.py**

```python
# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import os

DATABASE_URL = os.environ["GOVERNOR_DATABASE_URL"]

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionFactory() as session:
        yield session
```

- [ ] **Step 4: Implement models.py**

```python
# app/models.py
import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, Enum, UniqueConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMPTZ
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
    match_logic: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mutate_logic: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class RecordingSession(Base):
    __tablename__ = "recording_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, default=lambda: datetime.now(timezone.utc))
    stopped_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    traces: Mapped[list["Trace"]] = relationship("Trace", back_populates="session")


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recording_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recording_sessions.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    original_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    final_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mutation_steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    action: Mapped[ActionEnum] = mapped_column(Enum(ActionEnum), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, default=lambda: datetime.now(timezone.utc))
    session: Mapped["RecordingSession"] = relationship("RecordingSession", back_populates="traces")
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_models.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add control-plane/app/database.py control-plane/app/models.py control-plane/tests/test_models.py
git commit -m "feat: add SQLAlchemy models for Rule, RecordingSession, Trace"
```

---

## Task 3: Alembic migration

**Files:**
- Create: `control-plane/alembic.ini`
- Create: `control-plane/migrations/env.py`
- Create: `control-plane/migrations/versions/001_initial_schema.py`

- [ ] **Step 1: Initialise Alembic**

```bash
cd control-plane && alembic init migrations
```

- [ ] **Step 2: Update alembic.ini**

Set `sqlalchemy.url` to a placeholder (actual URL comes from env at runtime):
```ini
sqlalchemy.url = postgresql+asyncpg://governor:governor_secret@localhost:5432/governor
```

- [ ] **Step 3: Update migrations/env.py to use async engine and import models**

```python
# migrations/env.py (key sections)
from app.database import Base
from app import models  # noqa: F401 — ensures models are registered
target_metadata = Base.metadata
```

Use `run_async_migrations()` pattern from Alembic async docs.

- [ ] **Step 4: Generate initial migration**

```bash
alembic revision --autogenerate -m "initial schema"
```

- [ ] **Step 5: Verify migration SQL looks correct**

```bash
alembic upgrade head --sql | head -60
```
Expected: CREATE TABLE rules, recording_sessions, traces with correct columns and FK.

- [ ] **Step 6: Commit**

```bash
git add control-plane/alembic.ini control-plane/migrations/
git commit -m "feat: add Alembic initial schema migration"
```

---

## Task 4: Redis client

**Files:**
- Create: `control-plane/app/redis_client.py`
- Create: `control-plane/tests/test_redis_client.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_redis_client.py
import pytest
from app.redis_client import RULES_KEY, RECORDING_KEY, traces_key

def test_key_constants_defined():
    assert RULES_KEY == "governor:rules"
    assert RECORDING_KEY == "governor:recording"

def test_traces_key_includes_session_id():
    assert traces_key("abc-123") == "governor:traces:abc-123"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_redis_client.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement redis_client.py**

```python
# app/redis_client.py
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_redis_client.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control-plane/app/redis_client.py control-plane/tests/test_redis_client.py
git commit -m "feat: add Redis client singleton and key constants"
```

---

## Task 5: Matcher

**Files:**
- Create: `control-plane/app/matcher.py`
- Create: `control-plane/tests/test_matcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_matcher.py
import pytest
from app.matcher import build_context, evaluate_rules

RULES = [
    {
        "id": "rule-1",
        "name": "Block large claude calls",
        "priority": 10,
        "match_logic": {">": [{"var": "total_chars"}, 10000]},
        "mutate_logic": {"strip_tag": [{"var": "messages"}, "<system-reminder>"]},
    },
    {
        "id": "rule-2",
        "name": "Flag bash tool usage",
        "priority": 20,
        "match_logic": {"in": ["Bash", {"var": "tool_names"}]},
        "mutate_logic": {"truncate_after": [{"var": "messages"}, 5000]},
    },
]

def test_build_context_extracts_fields():
    payload = {
        "model": "claude-sonnet-4-6",
        "tools": [{"name": "Bash"}, {"name": "Read"}],
        "messages": [{"role": "user", "content": "hello"}],
        "system": "you are helpful",
    }
    ctx = build_context(payload)
    assert ctx["model"] == "claude-sonnet-4-6"
    assert "Bash" in ctx["tool_names"]
    assert ctx["has_system"] is True
    assert ctx["message_count"] == 1

def test_evaluate_rules_returns_matched_in_priority_order():
    payload = {
        "model": "claude-sonnet-4-6",
        "tools": [{"name": "Bash"}],
        "messages": [{"role": "user", "content": "x" * 20000}],
    }
    matched = evaluate_rules(RULES, payload)
    assert len(matched) == 2
    assert matched[0]["id"] == "rule-1"
    assert matched[1]["id"] == "rule-2"

def test_evaluate_rules_returns_empty_when_no_match():
    payload = {
        "model": "claude-haiku",
        "tools": [],
        "messages": [{"role": "user", "content": "hi"}],
    }
    matched = evaluate_rules(RULES, payload)
    assert matched == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_matcher.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement matcher.py**

```python
# app/matcher.py
import json
from json_logic import jsonLogic
from app.redis_client import get_redis, RULES_KEY


def build_context(payload: dict) -> dict:
    tools = payload.get("tools") or []
    messages = payload.get("messages") or []
    total_chars = sum(
        len(str(m.get("content", ""))) for m in messages
    )
    return {
        "model": payload.get("model", ""),
        "tool_names": [t["name"] for t in tools if "name" in t],
        "message_count": len(messages),
        "has_system": bool(payload.get("system")),
        "total_chars": total_chars,
    }


def evaluate_rules(rules: list[dict], payload: dict) -> list[dict]:
    context = build_context(payload)
    return [
        rule for rule in rules
        if jsonLogic(rule["match_logic"], context)
    ]


async def load_rules_from_redis() -> list[dict]:
    redis = get_redis()
    raw = await redis.get(RULES_KEY)
    if not raw:
        return []
    return json.loads(raw)


async def refresh_rules_cache(db_rules: list[dict]) -> None:
    redis = get_redis()
    await redis.set(RULES_KEY, json.dumps(db_rules))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_matcher.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control-plane/app/matcher.py control-plane/tests/test_matcher.py
git commit -m "feat: implement Matcher with JSONLogic rule evaluation"
```

---

## Task 6: Mutator

**Files:**
- Create: `control-plane/app/mutator.py`
- Create: `control-plane/tests/test_mutator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mutator.py
import pytest
import copy
from app.mutator import apply_pipeline

BASE_PAYLOAD = {
    "model": "claude-sonnet-4-6",
    "messages": [
        {"role": "user", "content": "Hello <system-reminder>ignore this</system-reminder> world " + "x" * 200}
    ],
}

RULES = [
    {
        "id": "r1", "name": "Strip reminders", "priority": 10,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<system-reminder>"]},
    },
    {
        "id": "r2", "name": "Truncate", "priority": 20,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"truncate_after": [50]},
    },
]

def test_strip_tag_removes_content():
    result, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), RULES[:1])
    content = result["messages"][0]["content"]
    assert "<system-reminder>" not in content
    assert "ignore this" not in content
    assert len(steps) == 1
    assert steps[0]["rule_id"] == "r1"

def test_pipeline_applies_rules_in_order():
    result, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), RULES)
    content = result["messages"][0]["content"]
    assert len(content) <= 50
    assert len(steps) == 2
    assert steps[0]["priority"] == 10
    assert steps[1]["priority"] == 20

def test_each_step_records_payload_after():
    _, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), RULES)
    assert "payload_after" in steps[0]
    assert "payload_after" in steps[1]

def test_empty_rules_returns_original_unchanged():
    result, steps = apply_pipeline(copy.deepcopy(BASE_PAYLOAD), [])
    assert result == BASE_PAYLOAD
    assert steps == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_mutator.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement mutator.py**

```python
# app/mutator.py
import re
import copy
import json


def _apply_strip_tag(messages: list[dict], tag: str) -> list[dict]:
    pattern = re.compile(rf"<{re.escape(tag[1:-1])}[^>]*>.*?</{re.escape(tag[1:-1])}>", re.DOTALL)
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = pattern.sub("", new_msg["content"]).strip()
        result.append(new_msg)
    return result


def _apply_truncate_after(messages: list[dict], max_chars: int) -> list[dict]:
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = new_msg["content"][:max_chars]
        result.append(new_msg)
    return result


def _apply_regex_delete(messages: list[dict], pattern: str) -> list[dict]:
    compiled = re.compile(pattern, re.DOTALL)
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = compiled.sub("", new_msg["content"]).strip()
        result.append(new_msg)
    return result


def _apply_replace(messages: list[dict], find: str, replacement: str) -> list[dict]:
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = new_msg["content"].replace(find, replacement)
        result.append(new_msg)
    return result


def _execute_mutate_logic(payload: dict, logic: dict) -> dict:
    payload = copy.deepcopy(payload)
    messages = payload.get("messages", [])

    if "strip_tag" in logic:
        tag = logic["strip_tag"][0]
        payload["messages"] = _apply_strip_tag(messages, tag)
    elif "truncate_after" in logic:
        max_chars = logic["truncate_after"][0]
        payload["messages"] = _apply_truncate_after(messages, max_chars)
    elif "regex_delete" in logic:
        pattern = logic["regex_delete"][0]
        payload["messages"] = _apply_regex_delete(messages, pattern)
    elif "replace" in logic:
        find, replacement = logic["replace"][0], logic["replace"][1]
        payload["messages"] = _apply_replace(messages, find, replacement)

    return payload


def apply_pipeline(payload: dict, matched_rules: list[dict]) -> tuple[dict, list[dict]]:
    """
    Apply matched rules in priority order.
    Returns (final_payload, mutation_steps).
    """
    steps = []
    current = copy.deepcopy(payload)

    for rule in matched_rules:
        current = _execute_mutate_logic(current, rule["mutate_logic"])
        steps.append({
            "rule_id": str(rule["id"]),
            "rule_name": rule["name"],
            "priority": rule["priority"],
            "payload_after": copy.deepcopy(current),
        })

    return current, steps
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_mutator.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control-plane/app/mutator.py control-plane/tests/test_mutator.py
git commit -m "feat: implement Mutator pipeline with strip_tag, truncate_after, regex_delete, replace"
```

---

## Task 7: Recorder

**Files:**
- Create: `control-plane/app/recorder.py`
- Create: `control-plane/tests/test_recorder.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_recorder.py
import pytest
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from app.recorder import should_record, push_trace, flush_to_db

@pytest.mark.asyncio
async def test_should_record_returns_session_id_when_active():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "session-abc"
    with patch("app.recorder.get_redis", return_value=mock_redis):
        result = await should_record()
    assert result == "session-abc"

@pytest.mark.asyncio
async def test_should_record_returns_none_when_inactive():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = ""
    with patch("app.recorder.get_redis", return_value=mock_redis):
        result = await should_record()
    assert result is None

@pytest.mark.asyncio
async def test_push_trace_rpushes_to_redis():
    mock_redis = AsyncMock()
    with patch("app.recorder.get_redis", return_value=mock_redis):
        await push_trace("session-1", {
            "trace_id": str(uuid.uuid4()),
            "model": "claude-sonnet-4-6",
            "original_payload": {},
            "final_payload": {},
            "mutation_steps": [],
            "action": "PROCEED",
        })
    mock_redis.rpush.assert_called_once()
    key = mock_redis.rpush.call_args[0][0]
    assert "session-1" in key
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_recorder.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement recorder.py**

```python
# app/recorder.py
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_recorder.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control-plane/app/recorder.py control-plane/tests/test_recorder.py
git commit -m "feat: implement Recorder with Redis buffer and DB flush"
```

---

## Task 8: gRPC Servicer

**Files:**
- Create: `control-plane/app/grpc_servicer.py`
- Create: `control-plane/tests/test_grpc_servicer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_grpc_servicer.py
import pytest
import json
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from app.grpc_servicer import GovernorServicer
import interceptor_pb2

SAMPLE_PAYLOAD = {
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello <system-reminder>remove me</system-reminder>"}],
    "tools": [],
    "litellm_call_id": str(uuid.uuid4()),
}

MATCHING_RULES = [{
    "id": str(uuid.uuid4()), "name": "Strip reminders", "priority": 10,
    "match_logic": {"==": [1, 1]},
    "mutate_logic": {"strip_tag": ["<system-reminder>"]},
}]

@pytest.mark.asyncio
async def test_proceeds_when_no_rules_match():
    servicer = GovernorServicer()
    request = interceptor_pb2.ContextRequest(
        trace_id=str(uuid.uuid4()),
        model="claude-sonnet-4-6",
        raw_json_payload=json.dumps(SAMPLE_PAYLOAD),
    )
    with patch("app.grpc_servicer.load_rules_from_redis", return_value=AsyncMock(return_value=[])()) as _:
        with patch("app.grpc_servicer.load_rules_from_redis", new=AsyncMock(return_value=[])):
            response = await servicer.MutateContext(request, None)
    assert response.action == interceptor_pb2.ContextResponse.Action.PROCEED

@pytest.mark.asyncio
async def test_mutates_when_rules_match():
    servicer = GovernorServicer()
    request = interceptor_pb2.ContextRequest(
        trace_id=str(uuid.uuid4()),
        model="claude-sonnet-4-6",
        raw_json_payload=json.dumps(SAMPLE_PAYLOAD),
    )
    with patch("app.grpc_servicer.load_rules_from_redis", new=AsyncMock(return_value=MATCHING_RULES)):
        with patch("app.grpc_servicer.should_record", new=AsyncMock(return_value=None)):
            response = await servicer.MutateContext(request, None)
    assert response.action == interceptor_pb2.ContextResponse.Action.MUTATED
    result = json.loads(response.modified_json_payload)
    assert "<system-reminder>" not in result["messages"][0]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_grpc_servicer.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement grpc_servicer.py**

```python
# app/grpc_servicer.py
import json
import uuid
import logging
import interceptor_pb2
import interceptor_pb2_grpc
from app.matcher import load_rules_from_redis, evaluate_rules
from app.mutator import apply_pipeline
from app.recorder import should_record, push_trace

logger = logging.getLogger(__name__)


class GovernorServicer(interceptor_pb2_grpc.ContextServiceServicer):

    async def MutateContext(self, request, context):
        try:
            payload = json.loads(request.raw_json_payload)
            rules = await load_rules_from_redis()
            matched = evaluate_rules(rules, payload)

            if not matched:
                return interceptor_pb2.ContextResponse(
                    action=interceptor_pb2.ContextResponse.Action.PROCEED,
                    modified_json_payload="",
                )

            final_payload, steps = apply_pipeline(payload, matched)

            session_id = await should_record()
            if session_id:
                await push_trace(session_id, {
                    "trace_id": request.trace_id or str(uuid.uuid4()),
                    "model": request.model,
                    "original_payload": payload,
                    "final_payload": final_payload,
                    "mutation_steps": steps,
                    "action": "MUTATED",
                })

            return interceptor_pb2.ContextResponse(
                action=interceptor_pb2.ContextResponse.Action.MUTATED,
                modified_json_payload=json.dumps(final_payload),
            )

        except Exception as e:
            logger.error(f"Governor servicer error for trace {request.trace_id}: {e}", exc_info=True)
            # Fail-open: return PROCEED so LiteLLM lets the original request through
            return interceptor_pb2.ContextResponse(
                action=interceptor_pb2.ContextResponse.Action.PROCEED,
                modified_json_payload="",
            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_grpc_servicer.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control-plane/app/grpc_servicer.py control-plane/tests/test_grpc_servicer.py
git commit -m "feat: implement GovernorServicer orchestrating Matcher → Mutator → Recorder"
```

---

## Task 9: Pydantic schemas

**Files:**
- Create: `control-plane/app/schemas.py`

- [ ] **Step 1: Implement schemas.py** (no separate test — schemas are validated implicitly by router tests)

```python
# app/schemas.py
import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel


# Rules
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


# Recording
class RecordingSessionOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    stopped_at: datetime | None
    is_active: bool
    model_config = {"from_attributes": True}

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


# Traces
class MutationStep(BaseModel):
    rule_id: str
    rule_name: str
    priority: int
    payload_after: dict[str, Any]

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


# Recordings list
class RecordingListOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    stopped_at: datetime | None
    is_active: bool
    trace_count: int
```

- [ ] **Step 2: Commit**

```bash
git add control-plane/app/schemas.py
git commit -m "feat: add Pydantic schemas for all REST endpoints"
```

---

## Task 10: Rules router

**Files:**
- Create: `control-plane/app/routers/rules.py`
- Create: `control-plane/tests/test_routers/test_rules.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routers/test_rules.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_rule(client: AsyncClient):
    payload = {
        "name": "Strip reminders",
        "priority": 10,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<system-reminder>"]},
    }
    resp = await client.post("/rules", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Strip reminders"
    assert "id" in data

@pytest.mark.asyncio
async def test_create_rule_duplicate_priority_fails(client: AsyncClient):
    payload = {"name": "R1", "priority": 99, "match_logic": {"==": [1, 1]}, "mutate_logic": {"strip_tag": ["<x>"]}}
    await client.post("/rules", json=payload)
    resp = await client.post("/rules", json=payload)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_list_rules_ordered_by_priority(client: AsyncClient):
    for p in [30, 10, 20]:
        await client.post("/rules", json={"name": f"R{p}", "priority": p, "match_logic": {"==": [1, 1]}, "mutate_logic": {"strip_tag": ["<x>"]}})
    resp = await client.get("/rules")
    assert resp.status_code == 200
    priorities = [r["priority"] for r in resp.json()]
    assert priorities == sorted(priorities)

@pytest.mark.asyncio
async def test_delete_rule_deactivates(client: AsyncClient):
    resp = await client.post("/rules", json={"name": "Temp", "priority": 50, "match_logic": {"==": [1, 1]}, "mutate_logic": {"strip_tag": ["<x>"]}})
    rule_id = resp.json()["id"]
    del_resp = await client.delete(f"/rules/{rule_id}")
    assert del_resp.status_code == 200
    detail = await client.get(f"/rules/{rule_id}")
    assert detail.json()["is_active"] is False
```

- [ ] **Step 2: Create conftest.py with test fixtures**

```python
# tests/conftest.py
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from unittest.mock import AsyncMock, patch
from app.main import create_app
from app.database import Base, get_db

TEST_DB_URL = "postgresql+asyncpg://governor:governor_secret@localhost:5432/governor_test"

@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_db

    with patch("app.redis_client.get_redis") as mock_redis_factory:
        mock_redis = AsyncMock()
        mock_redis.get.return_value = ""
        mock_redis.set.return_value = True
        mock_redis.rpush.return_value = 1
        mock_redis_factory.return_value = mock_redis
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_routers/test_rules.py -v
```
Expected: FAIL

- [ ] **Step 4: Implement routers/rules.py**

```python
# app/routers/rules.py
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Rule
from app.schemas import RuleCreate, RuleUpdate, RuleOut
from app.matcher import refresh_rules_cache

router = APIRouter(prefix="/rules", tags=["rules"])


async def _refresh_cache(db: AsyncSession):
    result = await db.execute(select(Rule).where(Rule.is_active == True).order_by(Rule.priority))
    rules = result.scalars().all()
    await refresh_rules_cache([{
        "id": str(r.id), "name": r.name, "priority": r.priority,
        "match_logic": r.match_logic, "mutate_logic": r.mutate_logic,
    } for r in rules])


@router.get("", response_model=list[RuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rule).order_by(Rule.priority))
    return result.scalars().all()


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Rule).where(Rule.priority == body.priority))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A rule with this priority already exists")
    rule = Rule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    try:
        await _refresh_cache(db)
    except Exception as e:
        import logging; logging.getLogger(__name__).error(f"Redis cache refresh failed: {e}")
    return rule


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: uuid.UUID, body: RuleUpdate, db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    try:
        await _refresh_cache(db)
    except Exception as e:
        import logging; logging.getLogger(__name__).error(f"Redis cache refresh failed: {e}")
    return rule


@router.delete("/{rule_id}", response_model=RuleOut)
async def delete_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = False
    await db.commit()
    await db.refresh(rule)
    try:
        await _refresh_cache(db)
    except Exception as e:
        import logging; logging.getLogger(__name__).error(f"Redis cache refresh failed: {e}")
    return rule
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_routers/test_rules.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add control-plane/app/routers/rules.py control-plane/tests/conftest.py control-plane/tests/test_routers/test_rules.py
git commit -m "feat: implement /rules CRUD router with Redis cache refresh"
```

---

## Task 11: Recording router

**Files:**
- Create: `control-plane/app/routers/recording.py`
- Create: `control-plane/tests/test_routers/test_recording.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routers/test_recording.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_start_recording_returns_session_id(client: AsyncClient):
    resp = await client.post("/recording/start")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "started_at" in data

@pytest.mark.asyncio
async def test_start_recording_twice_fails(client: AsyncClient):
    await client.post("/recording/start")
    resp = await client.post("/recording/start")
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_status_reflects_active_session(client: AsyncClient):
    await client.post("/recording/start")
    resp = await client.get("/recording/status")
    assert resp.json()["is_active"] is True

@pytest.mark.asyncio
async def test_stop_recording_flushes_and_closes(client: AsyncClient):
    start_resp = await client.post("/recording/start")
    session_id = start_resp.json()["session_id"]
    stop_resp = await client.post("/recording/stop")
    assert stop_resp.status_code == 200
    data = stop_resp.json()
    assert data["session_id"] == session_id
    assert "traces_flushed" in data
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_routers/test_recording.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement routers/recording.py**

```python
# app/routers/recording.py
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
    existing = await db.execute(select(RecordingSession).where(RecordingSession.is_active == True))
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
    result = await db.execute(select(RecordingSession).where(RecordingSession.is_active == True))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No active recording session")
    redis = get_redis()
    await redis.set(RECORDING_KEY, "")
    flushed = await flush_to_db(str(session.id), db)
    session.is_active = False
    session.stopped_at = datetime.now(timezone.utc)
    await db.commit()
    return RecordingStopResponse(session_id=session.id, traces_flushed=flushed, stopped_at=session.stopped_at)


@router.get("/status", response_model=RecordingStatusResponse)
async def recording_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RecordingSession).where(RecordingSession.is_active == True))
    session = result.scalar_one_or_none()
    if not session:
        return RecordingStatusResponse(is_active=False, session_id=None, started_at=None)
    return RecordingStatusResponse(is_active=True, session_id=session.id, started_at=session.started_at)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_routers/test_recording.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control-plane/app/routers/recording.py control-plane/tests/test_routers/test_recording.py
git commit -m "feat: implement /recording start/stop/status router"
```

---

## Task 12: Recordings & Traces routers + health

**Files:**
- Create: `control-plane/app/routers/recordings.py`
- Create: `control-plane/app/routers/traces.py`
- Create: `control-plane/app/routers/health.py`
- Create: `control-plane/tests/test_routers/test_traces.py`

- [ ] **Step 1: Write failing test for traces**

```python
# tests/test_routers/test_traces.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_traces_list_empty_before_recording(client: AsyncClient):
    resp = await client.get("/traces")
    assert resp.status_code == 200
    assert resp.json() == []

@pytest.mark.asyncio
async def test_traces_list_scoped_by_session(client: AsyncClient):
    start = await client.post("/recording/start")
    session_id = start.json()["session_id"]
    await client.post("/recording/stop")
    resp = await client.get(f"/traces?session_id={session_id}")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_routers/test_traces.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement routers/recordings.py**

```python
# app/routers/recordings.py
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
        ) for r in rows
    ]


@router.delete("/{session_id}", status_code=204)
async def delete_recording(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    session = await db.get(RecordingSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Recording session not found")
    await db.execute(delete(Trace).where(Trace.recording_session_id == session_id))
    await db.delete(session)
    await db.commit()
```

- [ ] **Step 4: Implement routers/traces.py**

```python
# app/routers/traces.py
import uuid
from fastapi import APIRouter, Depends, Query
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
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace
```

- [ ] **Step 5: Implement routers/health.py**

```python
# app/routers/health.py
from fastapi import APIRouter
from app.database import engine
from app.redis_client import get_redis
from sqlalchemy import text

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
```

- [ ] **Step 6: Run trace test to verify it passes**

```bash
pytest tests/test_routers/test_traces.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add control-plane/app/routers/
control-plane/tests/test_routers/test_traces.py
git commit -m "feat: implement /recordings, /traces, /health routers"
```

---

## Task 13: main.py — wire everything together

**Files:**
- Create: `control-plane/app/main.py`

- [ ] **Step 1: Implement main.py**

```python
# app/main.py
import asyncio
import logging
import os
import grpc
from concurrent import futures
from contextlib import asynccontextmanager

from fastapi import FastAPI
import interceptor_pb2_grpc
from app.grpc_servicer import GovernorServicer
from app.database import engine, Base
from app.matcher import refresh_rules_cache
from app.models import Rule
from app.redis_client import get_redis, RECORDING_KEY
from sqlalchemy import select
from app.routers import rules, recording, recordings, traces, health

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _bootstrap_redis_from_db():
    from app.database import AsyncSessionFactory
    async with AsyncSessionFactory() as db:
        result = await db.execute(
            select(Rule).where(Rule.is_active == True).order_by(Rule.priority)
        )
        db_rules = result.scalars().all()
        await refresh_rules_cache([{
            "id": str(r.id), "name": r.name, "priority": r.priority,
            "match_logic": r.match_logic, "mutate_logic": r.mutate_logic,
        } for r in db_rules])

        # Recover recording state: if a session was active before restart, keep it in Redis
        from app.models import RecordingSession
        active = await db.execute(
            select(RecordingSession).where(RecordingSession.is_active == True)
        )
        session = active.scalar_one_or_none()
        redis = get_redis()
        if session:
            await redis.set(RECORDING_KEY, str(session.id))
            logger.info(f"Recovered active recording session {session.id}")
        else:
            current = await redis.get(RECORDING_KEY)
            if not current:
                await redis.set(RECORDING_KEY, "")


async def _run_grpc():
    server = grpc.aio.server()
    interceptor_pb2_grpc.add_ContextServiceServicer_to_server(GovernorServicer(), server)
    server.add_insecure_port("[::]:50051")
    logger.info("gRPC server starting on :50051")
    await server.start()
    await server.wait_for_termination()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _bootstrap_redis_from_db()
    except Exception as e:
        logger.warning(f"Redis bootstrap failed (continuing): {e}")
    asyncio.create_task(_run_grpc())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Governor Control Plane", lifespan=lifespan)
    app.include_router(rules.router)
    app.include_router(recording.router)
    app.include_router(recordings.router)
    app.include_router(traces.router)
    app.include_router(health.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False)
```

- [ ] **Step 2: Delete the old server.py**

```bash
rm control-plane/server.py
```

- [ ] **Step 3: Run full test suite**

```bash
cd control-plane && pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add control-plane/app/main.py
git rm control-plane/server.py
git commit -m "feat: wire FastAPI + gRPC into main.py, remove dummy server.py"
```

---

## Task 14: Smoke test with docker-compose

- [ ] **Step 1: Build and start all services**

```bash
docker-compose up --build -d
```

- [ ] **Step 2: Verify health endpoint**

```bash
curl http://localhost:8080/health
```
Expected: `{"status":"ok","components":{"db":"ok","redis":"ok"}}`

- [ ] **Step 3: Create a rule via REST**

```bash
curl -X POST http://localhost:8080/rules \
  -H "Content-Type: application/json" \
  -d '{"name":"Strip reminders","priority":10,"match_logic":{"==": [1,1]},"mutate_logic":{"strip_tag":["<system-reminder>"]}}'
```
Expected: 201 with rule JSON including `id`.

- [ ] **Step 4: Start a recording session**

```bash
curl -X POST http://localhost:8080/recording/start
```
Expected: `{"session_id":"...","started_at":"..."}`

- [ ] **Step 5: Send a test request through LiteLLM proxy (port 4000) and verify mutation**

Send a request via the LiteLLM proxy. Observe control-plane logs:
```bash
docker logs governor-control-plane --follow
```
Expected: log lines showing matched rules and MUTATED action.

- [ ] **Step 6: Stop recording and verify traces flushed**

```bash
curl -X POST http://localhost:8080/recording/stop
```
Expected: `{"traces_flushed": N, ...}` where N > 0 if requests were intercepted.

- [ ] **Step 7: Verify traces persisted**

```bash
curl http://localhost:8080/traces
```
Expected: array of trace objects.

- [ ] **Step 8: Final commit**

```bash
git add .
git commit -m "chore: smoke test confirmed, control plane core complete"
```

---

## Definition of Done

- [ ] All unit tests pass (`pytest tests/ -v`)
- [ ] `docker-compose up` starts cleanly with all services healthy
- [ ] `/health` returns `ok` for both DB and Redis
- [ ] A rule created via REST is immediately reflected in Redis cache
- [ ] A recording session can be started, traces are buffered in Redis, and flushed to DB on stop
- [ ] Restarting the control-plane container recovers active recording state from Redis
- [ ] The old `server.py` is deleted