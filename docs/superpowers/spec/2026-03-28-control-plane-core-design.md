# Control Plane Core — Design Spec
_Date: 2026-03-28_

---

## 1. Scope

This spec covers the first real implementation of the Governor Control Plane, replacing the current dummy `server.py` with a production-grade service. It does **not** cover the Management Plane UI, Redis-backed Hold state, or enterprise deployment.

**Deliverables:**
- Restructured `control-plane/` Python package
- PostgreSQL-backed rule storage with Redis hot-path cache
- Matcher → Mutator pipeline (JSONLogic DSL)
- Redis-buffered Recorder with bulk flush on session stop
- FastAPI REST layer (rules CRUD, recording control, traces, recordings list)
- Expanded `docker-compose.yml` (adds Redis + governor-db)

---

## 2. Module Layout

```
control-plane/
├── app/
│   ├── main.py              # FastAPI app + gRPC server bootstrapped together
│   ├── grpc_servicer.py     # ContextServicer: Matcher → Mutator → Recorder
│   ├── matcher.py           # Reads rules from Redis, evaluates match_logic
│   ├── mutator.py           # Applies mutate_logic pipeline in priority order
│   ├── recorder.py          # RPUSH to Redis during session; bulk flush on stop
│   ├── models.py            # SQLAlchemy async models
│   ├── database.py          # Async engine + session factory
│   └── routers/
│       ├── rules.py         # CRUD endpoints
│       ├── recording.py     # start/stop/status/list/delete
│       └── traces.py        # list + detail
├── Dockerfile
└── requirements.txt
```

`main.py` starts both servers with `asyncio.gather`:
- `uvicorn` serves FastAPI on port `8080`
- `grpc.aio.server` serves the gRPC servicer on port `50051`

---

## 3. Data Model

### `rules`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| name | TEXT | Human-readable label |
| priority | INT UNIQUE | Execution order; no two rules share a slot |
| is_active | BOOL | Soft toggle; inactive rules are excluded from cache |
| match_logic | JSONB | JSONLogic expression evaluated against request context |
| mutate_logic | JSONB | JSONLogic mutation applied if match passes |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `recording_sessions`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | Returned to UI on start |
| started_at | TIMESTAMPTZ | |
| stopped_at | TIMESTAMPTZ nullable | Set on stop |
| is_active | BOOL | Only one active session at a time |

### `traces`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| trace_id | UUID | Forwarded from LiteLLM `litellm_call_id` |
| recording_session_id | UUID FK → recording_sessions.id | |
| model | TEXT | |
| original_payload | JSONB | Payload as received from the hook |
| final_payload | JSONB | Payload after all matched rules applied |
| mutation_steps | JSONB | Ordered array: `[{rule_id, rule_name, priority, payload_after}]` |
| action | ENUM(PROCEED, MUTATED, REJECT) | |
| recorded_at | TIMESTAMPTZ | |

`mutation_steps` enables the UI to replay the full transformation chain, step by step, and render the agent/subagent hierarchy tree embedded in the payloads.

---

## 4. Redis Key Schema

| Key | Value | Notes |
|---|---|---|
| `governor:rules` | JSON array of active rules ordered by priority ASC | Refreshed on every rule write |
| `governor:recording` | `""` (inactive) or `"<session_uuid>"` (active) | Persists across page refreshes and server restarts |
| `governor:traces:<session_id>` | Redis List (RPUSH) of JSON trace objects | Exists only during an active session; flushed to DB on stop |

Redis must run with `appendonly yes` so state survives container restarts.

---

## 5. Rules Cache Lifecycle

**Bootstrap (startup):**
1. Load all `is_active = true` rules from PostgreSQL ordered by `priority ASC`
2. Serialize to JSON array → write to `governor:rules`

**Rule write (POST/PUT/DELETE /rules):**
1. Write to PostgreSQL
2. In `finally`: reload active rules from DB → overwrite `governor:rules`
3. If Redis refresh fails: log error with rule ID, DB write is not rolled back (rules never lost, briefly stale)

**Matcher hot path:**
- Read `governor:rules` from Redis on every gRPC call — no DB touch

---

## 6. Matcher Logic

The Matcher evaluates each rule's `match_logic` JSONLogic expression against a normalized context object derived from the incoming request:

```json
{
  "model": "claude-sonnet-4-6",
  "tool_names": ["Bash", "Read", "Agent"],
  "message_count": 3,
  "has_system": true,
  "total_chars": 42000
}
```

Rules that pass evaluation are collected into the **matched rule list**, preserving priority order. The list is passed to the Mutator.

---

## 7. Mutator Pipeline

The Mutator receives the matched rule list and the original payload. It executes each rule's `mutate_logic` in priority order, passing the output of each step as the input to the next. This makes rules composable: a rule at priority 20 operates on the payload already transformed by priority 10.

**Built-in operators (JSONLogic custom operations):**

| Operator | Description |
|---|---|
| `strip_tag` | Removes all occurrences of an XML-style tag from message content (e.g. `<system-reminder>`) |
| `truncate_after` | Cuts message content after N characters |
| `regex_delete` | Removes content matching a regex pattern |
| `replace` | String substitution across message content |

Example `mutate_logic`:
```json
{"strip_tag": [{"var": "messages"}, "<system-reminder>"]}
```

After each step, the Mutator records `{rule_id, rule_name, priority, payload_after}` into `mutation_steps`.

**Final action resolution:**
- No rules matched → `PROCEED`, original payload returned unchanged
- One or more rules matched and mutated → `MUTATED`, final payload returned
- Any rule returns a REJECT signal → `REJECT`, exception raised in hook

---

## 8. Recorder

**During an active recording session:**
- On each gRPC call, the Recorder reads `governor:recording` from Redis
- If the value is a session UUID, it serializes the full trace (original, final, mutation_steps, action) and `RPUSH`es it to `governor:traces:<session_id>`
- This is synchronous within the gRPC handler — lossless while the session is active

**On `POST /recording/stop`:**
1. Set `governor:recording` to `""` in Redis
2. Read the full `governor:traces:<session_id>` list from Redis
3. Bulk-insert all traces into PostgreSQL with `recording_session_id` stamped
4. Delete `governor:traces:<session_id>` from Redis
5. Close the `recording_sessions` row (`stopped_at`, `is_active = false`)

If the bulk insert fails: the Redis list is preserved, the session remains open in DB, and an error is returned to the caller — the user can retry stop without data loss.

---

## 9. REST API

### Rules
```
GET    /rules              List all rules ordered by priority
POST   /rules              Create rule → DB write → Redis refresh
PUT    /rules/{id}         Update rule → DB write → Redis refresh
DELETE /rules/{id}         Deactivate rule → DB write → Redis refresh
```

### Recording
```
POST   /recording/start    Create recording_sessions row, set governor:recording = session_id
                           Returns: { session_id, started_at }
POST   /recording/stop     Flush Redis traces to DB, close session
GET    /recording/status   Returns: { is_active, session_id?, started_at? }
```

### Recordings (historical)
```
GET    /recordings                  List all sessions: id, started_at, stopped_at, trace_count
DELETE /recordings/{session_id}     Delete session + all its traces from PostgreSQL
```

### Traces
```
GET    /traces?session_id=&page=    Paginated trace list scoped to a session
GET    /traces/{trace_id}           Full trace with mutation_steps expanded
```

### Health
```
GET    /health             DB + Redis connectivity status
```

---

## 10. docker-compose Changes

Add two new services:

**`governor-db`** — dedicated PostgreSQL instance for Governor (separate from `litellm-db`):
```yaml
governor-db:
  image: postgres:16-alpine
  environment:
    POSTGRES_USER: governor
    POSTGRES_PASSWORD: ${GOVERNOR_DB_PASSWORD}
    POSTGRES_DB: governor
  volumes:
    - governor_postgres_data:/var/lib/postgresql/data
  healthcheck: ...
```

**`redis`**:
```yaml
redis:
  image: redis:7-alpine
  command: redis-server --appendonly yes
  volumes:
    - redis_data:/data
```

`control-plane` depends on both `governor-db` and `redis`.

---

## 11. Error Handling & Fail-Open Policy

All errors in the gRPC servicer are caught and logged. The hook already implements fail-open: if the control plane is unreachable or throws, the original request passes through unchanged. This policy is preserved.

Specific cases:
- Redis unavailable at startup → log warning, continue (rules loaded from DB fallback)
- Redis unavailable during gRPC call → log error, return PROCEED with original payload
- DB write failure on recording stop → preserve Redis list, return 500 to caller, session stays open
- Rule evaluation error (malformed JSONLogic) → skip that rule, log error with rule ID

---

## 12. Out of Scope

- Hold state / manual intervention (Redis-based pause, Management Plane)
- WebSocket / real-time push to UI
- Rule Designer UI
- Agent Hierarchy Graph
- Replay Sandbox
- Enterprise deployment (ECS, Aurora, ElastiCache)