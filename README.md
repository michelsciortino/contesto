# Governor

A rules engine that intercepts LLM requests before they reach the model — inspecting, mutating, and recording them in real time.

Governor sits between your AI agents and the LLM provider. Every request passes through a Matcher → Mutator → Recorder pipeline: rules are evaluated against the request context, matched rules transform the payload in priority order, and traces are buffered to Redis and flushed to PostgreSQL at the end of a recording session.

---

## Architecture

```
 AI Agent (Claude Code, etc.)
        │  HTTP
        ▼
 ┌─────────────┐     gRPC (port 50051)     ┌───────────────────────┐
 │   LiteLLM   │ ────────────────────────► │   Control Plane       │
 │   Proxy     │ ◄──────────────────────── │                       │
 │  :4000      │   PROCEED / MUTATED /     │  Matcher → Mutator    │
 └─────────────┘   REJECT + payload        │  → Recorder           │
                                           │  FastAPI REST  :8080  │
                                           │  gRPC          :50051 │
                                           └───────────┬───────────┘
                                                       │
                                            ┌──────────┴──────────┐
                                            │                     │
                                       ┌────▼────┐          ┌─────▼──────┐
                                       │  Redis  │          │ PostgreSQL │
                                       │  :6379  │          │  :5432     │
                                       └─────────┘          └────────────┘
```

**Data Plane** — `litellm-hook/custom_hooks.py`
The `GovernorHook` fires on every `async_pre_call_hook`. It serialises the full LiteLLM `data` object into a Protobuf `ContextRequest` and sends it to the Control Plane over gRPC. If the Control Plane returns `MUTATED`, the hook merges the safe mutable keys back into `data`. The hook is fail-open: any error lets the original request through unchanged.

**Control Plane** — `control-plane/app/`
A single Python container that runs FastAPI (REST) and a gRPC server side-by-side. On every incoming gRPC call it runs:

1. **Matcher** — reads active rules from the Redis cache (`governor:rules`) and evaluates each rule's `match_logic` JSONLogic expression against a normalised context object (`model`, `tool_names`, `message_count`, `has_system`, `total_chars`).
2. **Mutator** — applies each matched rule's `mutate_logic` in priority order. Each step records its output into `mutation_steps`, making the full transformation chain replayable.
3. **Recorder** — if a recording session is active (`governor:recording`), the trace is `RPUSH`-ed to `governor:traces:<session_id>` in Redis. On `POST /recording/stop` the entire list is bulk-inserted into PostgreSQL and the Redis key is deleted.

---

## Project layout

```
governor/
├── control-plane/
│   ├── app/
│   │   ├── main.py             # FastAPI app + gRPC server bootstrap
│   │   ├── grpc_servicer.py    # GovernorServicer: Matcher → Mutator → Recorder
│   │   ├── matcher.py          # JSONLogic evaluation, Redis cache read/write
│   │   ├── mutator.py          # strip_tag, truncate_after, regex_delete, replace
│   │   ├── recorder.py         # Redis RPUSH + bulk DB flush on session stop
│   │   ├── models.py           # SQLAlchemy: Rule, RecordingSession, Trace
│   │   ├── database.py         # Async engine + session factory
│   │   ├── redis_client.py     # Singleton client + key constants
│   │   ├── schemas.py          # Pydantic request/response schemas
│   │   └── routers/
│   │       ├── rules.py        # CRUD + Redis cache refresh on every write
│   │       ├── recording.py    # start / stop / status
│   │       ├── recordings.py   # list / delete historical sessions
│   │       ├── traces.py       # paginated list + detail
│   │       └── health.py       # DB + Redis connectivity probe
│   ├── migrations/             # Alembic async migrations
│   ├── tests/                  # 31 unit + integration tests
│   ├── Dockerfile
│   └── requirements.txt
├── litellm-hook/
│   └── custom_hooks.py         # LiteLLM pre-call hook (Data Plane)
├── protos/
│   ├── interceptor.proto       # gRPC contract
│   └── generated/              # Auto-generated Protobuf stubs
├── docker-compose.yml
└── .env                        # Secret values (not committed)
```

---

## Quick start

### Prerequisites

- Docker + Docker Compose
- Python 3.11+ (for local development / tests only)

### 1. Configure environment

Create a `.env` file in the project root:

```env
# LiteLLM database
LITELLM_DB_PASSWORD=your_litellm_db_password

# Governor database
GOVERNOR_DB_PASSWORD=governor_secret
GOVERNOR_DATABASE_URL=postgresql+asyncpg://governor:governor_secret@governor-db:5432/governor
GOVERNOR_REDIS_URL=redis://redis:6379/0

# Your LLM provider keys (passed through to LiteLLM)
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Generate Protobuf stubs

```bash
mkdir -p protos/generated
python -m grpc_tools.protoc -I./protos \
  --python_out=./protos/generated \
  --grpc_python_out=./protos/generated \
  ./protos/interceptor.proto
```

### 3. Start all services

```bash
docker compose up --build
```

Services started:

| Service | Port | Description |
|---|---|---|
| `litellm-proxy` | 4000 | LiteLLM proxy with Governor hook |
| `governor-control-plane` | 8080 (REST), 50051 (gRPC) | Rules engine |
| `governor-db` | — | PostgreSQL for Governor |
| `governor-redis` | 6379 | Redis for rules cache + trace buffer |
| `litellm-db` | — | PostgreSQL for LiteLLM |

### 4. Verify

```bash
curl http://localhost:8080/health
# {"status":"ok","components":{"db":"ok","redis":"ok"}}
```

---

## Using the REST API

### Create a rule

```bash
curl -X POST http://localhost:8080/rules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Strip system reminders",
    "priority": 10,
    "match_logic": {"==": [1, 1]},
    "mutate_logic": {"strip_tag": ["<system-reminder>"]}
  }'
```

Rules are evaluated in `priority` order (lowest first). No two rules share a priority slot.

### Available mutate operators

| Operator | Effect |
|---|---|
| `{"strip_tag": ["<tag-name>"]}` | Removes all `<tag-name>…</tag-name>` blocks from message content |
| `{"truncate_after": [N]}` | Cuts message content to the first N characters |
| `{"regex_delete": ["pattern"]}` | Removes all content matching the regex |
| `{"replace": ["find", "replacement"]}` | String substitution across all messages |

### Match context fields

Each rule's `match_logic` is a [JSONLogic](https://jsonlogic.com) expression evaluated against:

```json
{
  "model": "claude-sonnet-4-6",
  "tool_names": ["Bash", "Read", "Agent"],
  "message_count": 12,
  "has_system": true,
  "total_chars": 38000
}
```

### Record a session

```bash
# Start recording
curl -X POST http://localhost:8080/recording/start
# → {"session_id": "uuid", "started_at": "..."}

# ... send requests through LiteLLM at :4000 ...

# Stop and flush traces to DB
curl -X POST http://localhost:8080/recording/stop
# → {"session_id": "uuid", "traces_flushed": 7, "stopped_at": "..."}

# Inspect traces
curl "http://localhost:8080/traces?session_id=<uuid>"
curl "http://localhost:8080/traces/<trace_id>"
```

### Full endpoint reference

```
GET    /health

GET    /rules
POST   /rules
GET    /rules/{id}
PUT    /rules/{id}
DELETE /rules/{id}          # soft-deactivates; removes from cache

POST   /recording/start
POST   /recording/stop
GET    /recording/status

GET    /recordings
DELETE /recordings/{session_id}

GET    /traces?session_id=&page=
GET    /traces/{trace_id}
```

---

## Redis key schema

| Key | Value |
|---|---|
| `governor:rules` | JSON array of active rules, ordered by priority ASC. Refreshed on every rule write. |
| `governor:recording` | `""` (inactive) or `"<session_uuid>"` (active). Persists across restarts. |
| `governor:traces:<session_id>` | Redis List. `RPUSH`-ed during a session. Deleted after a successful DB flush. |

Redis is configured with `appendonly yes` so state survives container restarts. On startup the Control Plane reloads the rules cache from PostgreSQL and recovers any active recording session.

---

## Running tests

Tests run entirely in-memory (SQLite + mocked Redis) — no Docker required.

```bash
cd control-plane
pip install -r requirements.txt aiosqlite
pytest tests/ -v
```

```
31 passed in 2.00s
```

---

## Fail-open policy

Every error in the gRPC servicer is caught and logged. The hook returns the original, unmodified payload to LiteLLM. This means:

- If the Control Plane container is down → requests pass through unchanged
- If Redis is unavailable at startup → rules are loaded from DB as fallback
- If Redis is unavailable during a gRPC call → `PROCEED` with original payload
- If a rule's JSONLogic is malformed → that rule is skipped and logged; others still apply
- If the DB write fails on recording stop → the Redis trace list is preserved; retry `POST /recording/stop`

---

## What's not yet implemented

The following are in the spec but out of scope for this iteration:

- Hold state / manual intervention (pausing a request mid-flight)
- WebSocket / real-time push to the Management Plane UI
- Rule Designer GUI
- Agent Hierarchy Graph
- Replay Sandbox
- Enterprise deployment (ECS, Aurora, ElastiCache)
