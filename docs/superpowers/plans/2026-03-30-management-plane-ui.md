# Management Plane UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Governor Management Plane — a Next.js + Blueprint.js web application that visualises agent traffic in real time via WebSocket, and two required Control Plane additions (WebSocket endpoint + session_id on Trace).

**Architecture:** Two sequential phases. Phase A (Tasks 1–2) extends the Control Plane with a `/ws/live` WebSocket endpoint and adds `session_id` to the `Trace` model. Phase B (Tasks 3–10) builds the Next.js application against those endpoints, starting with the shared infrastructure (lib/, layout, CSS) and then each view in dependency order.

**Tech Stack:** Python/FastAPI (WebSocket), Next.js 14 App Router, Blueprint.js 5, ReactFlow 12, JetBrains Mono + Space Grotesk fonts, CSS Modules, native WebSocket API, no external state library.

**Spec:** `docs/superpowers/specs/2026-03-30-management-plane-ui-design.md`

---

## File Map

```
# Phase A — Control Plane additions
control-plane/app/models.py                   MODIFY — add session_id column to Trace
control-plane/migrations/versions/002_add_session_id.py  CREATE — Alembic migration
control-plane/app/routers/ws.py               CREATE — WebSocket endpoint + ConnectionManager
control-plane/app/grpc_servicer.py            MODIFY — broadcast trace event after each call
control-plane/app/main.py                     MODIFY — include ws router

# Phase B — Next.js application
management-plane/
├── package.json
├── next.config.ts
├── tsconfig.json
├── Dockerfile
├── app/
│   ├── layout.tsx
│   ├── page.tsx
│   ├── live/page.tsx
│   ├── liveflow/page.tsx
│   ├── liveflow/[agentId]/page.tsx
│   ├── recordings/page.tsx
│   ├── rules/page.tsx
│   └── traces/[traceId]/page.tsx
├── components/
│   ├── layout/Sidebar.tsx
│   ├── layout/Topbar.tsx
│   ├── live/StatCards.tsx
│   ├── live/TraceTable.tsx
│   ├── live/SessionsPanel.tsx
│   ├── live/RecordingButton.tsx
│   ├── liveflow/HierarchyGraph.tsx
│   ├── liveflow/AgentNode.tsx
│   ├── liveflow/TimelineView.tsx
│   ├── liveflow/CallPill.tsx
│   ├── liveflow/dagreLayout.ts
│   ├── conversation/CallRow.tsx
│   ├── conversation/AnatomyBar.tsx
│   ├── conversation/FieldSection.tsx
│   ├── conversation/FieldItem.tsx
│   └── shared/
│       ├── NeonBadge.tsx
│       ├── TokenBar.tsx
│       └── TraceDetailSlideOver.tsx
├── lib/
│   ├── ws.tsx
│   ├── api.ts
│   ├── models.ts
│   ├── tokens.ts
│   └── hierarchy.ts
└── styles/
    ├── globals.css
    └── neon.css
```

---

## Task 1: Add `session_id` to Trace + Alembic migration

**Files:**
- Modify: `control-plane/app/models.py`
- Create: `control-plane/migrations/versions/002_add_session_id.py`
- Modify: `control-plane/app/grpc_servicer.py`

- [ ] **Step 1: Write failing test**

```python
# control-plane/tests/test_models.py — add at bottom
def test_trace_model_has_session_id():
    cols = {c.name for c in Trace.__table__.columns}
    assert "session_id" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd control-plane && python -m pytest tests/test_models.py::test_trace_model_has_session_id -v
```
Expected: FAIL — `assert "session_id" in cols`

- [ ] **Step 3: Add session_id column to Trace model**

In `control-plane/app/models.py`, add to the `Trace` class after the `trace_id` column:

```python
session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_models.py -v
```
Expected: all PASS

- [ ] **Step 5: Write Alembic migration**

Create `control-plane/migrations/versions/002_add_session_id.py`:

```python
"""add session_id to traces

Revision ID: 002
Revises: 001
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("traces", sa.Column("session_id", sa.String(64), nullable=True))
    op.create_index("ix_traces_session_id", "traces", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_traces_session_id", table_name="traces")
    op.drop_column("traces", "session_id")
```

- [ ] **Step 6: Update gRPC servicer to extract and store session_id**

In `control-plane/app/grpc_servicer.py`, add a helper and use it when building the trace:

```python
import json as _json

def _extract_session_id(payload: dict) -> str | None:
    """Extract session_id from metadata.user_id JSON blob."""
    try:
        meta = payload.get("metadata", {})
        uid_raw = meta.get("user_id", "")
        if uid_raw:
            uid = _json.loads(uid_raw)
            return uid.get("session_id")
    except Exception:
        pass
    return None
```

Then in `push_trace(...)` call, add `"session_id": _extract_session_id(payload)` to the trace dict.

In `recorder.py`, add `session_id=t.get("session_id")` when constructing `Trace(...)`.

- [ ] **Step 7: Run full test suite**

```bash
cd control-plane && python -m pytest tests/ -v
```
Expected: all 31+ tests PASS

- [ ] **Step 8: Commit**

```bash
git add control-plane/app/models.py \
        control-plane/migrations/versions/002_add_session_id.py \
        control-plane/app/grpc_servicer.py \
        control-plane/app/recorder.py \
        control-plane/tests/test_models.py
git commit -m "feat: add session_id to Trace model and extract from payload metadata"
```

---

## Task 2: WebSocket endpoint `/ws/live`

**Files:**
- Create: `control-plane/app/routers/ws.py`
- Modify: `control-plane/app/grpc_servicer.py`
- Modify: `control-plane/app/main.py`

- [ ] **Step 1: Write failing test**

```python
# control-plane/tests/test_routers/test_ws.py
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_health_still_works_after_ws_added(client: AsyncClient):
    """Smoke test: adding WS router does not break existing routes."""
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_connection_manager_broadcast():
    from app.routers.ws import ConnectionManager
    manager = ConnectionManager()

    mock_ws = AsyncMock()
    await manager.connect(mock_ws)
    await manager.broadcast('{"type":"test"}')
    mock_ws.send_text.assert_awaited_once_with('{"type":"test"}')

    await manager.disconnect(mock_ws)
    await manager.broadcast('{"type":"after-disconnect"}')
    # After disconnect, no new sends to this ws
    assert mock_ws.send_text.await_count == 1
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd control-plane && python -m pytest tests/test_routers/test_ws.py -v
```
Expected: FAIL — `app.routers.ws` not found

- [ ] **Step 3: Create `control-plane/app/routers/ws.py`**

```python
import asyncio
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.redis_client import get_redis, RECORDING_KEY

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"WS client connected ({len(self._connections)} total)")

    async def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info(f"WS client disconnected ({len(self._connections)} remaining)")

    async def broadcast(self, message: str) -> None:
        dead = set()
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self._connections -= dead


# Module-level singleton — imported by grpc_servicer
manager = ConnectionManager()


async def _status_ping_loop() -> None:
    """Background task: broadcast status every 3 seconds."""
    while True:
        await asyncio.sleep(3)
        try:
            redis = get_redis()
            recording_val = await redis.get(RECORDING_KEY)
            is_recording = bool(recording_val)
            session_id = recording_val if is_recording else None

            # Check DB connectivity
            from app.database import engine
            from sqlalchemy import text
            db_ok = "ok"
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            except Exception:
                db_ok = "error"

            redis_ok = "ok"
            try:
                await redis.ping()
            except Exception:
                redis_ok = "error"

            # Compute elapsed_seconds if recording
            session_started_at = None
            elapsed_seconds = None
            if is_recording and session_id:
                try:
                    from app.database import AsyncSessionFactory
                    from app.models import RecordingSession
                    from sqlalchemy import select as _select
                    import uuid as _uuid
                    async with AsyncSessionFactory() as db_sess:
                        row = await db_sess.get(RecordingSession, _uuid.UUID(session_id))
                        if row and row.started_at:
                            session_started_at = row.started_at.isoformat()
                            elapsed_seconds = int((datetime.now(timezone.utc) - row.started_at.replace(tzinfo=timezone.utc)).total_seconds())
                except Exception:
                    pass

            payload = json.dumps({
                "type": "status",
                "data": {
                    "is_recording": is_recording,
                    "session_id": session_id,
                    "session_started_at": session_started_at,
                    "elapsed_seconds": elapsed_seconds,
                    "db": db_ok,
                    "redis": redis_ok,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            })
            if self._connections:
                await manager.broadcast(payload)
        except Exception as e:
            logger.error(f"Status ping error: {e}")


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send current status immediately on connect
        redis = get_redis()
        recording_val = await redis.get(RECORDING_KEY)
        await websocket.send_text(json.dumps({
            "type": "status",
            "data": {
                "is_recording": bool(recording_val),
                "session_id": recording_val if recording_val else None,
                "db": "ok",
                "redis": "ok",
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        }))
        # Keep connection alive — server pushes, client just stays open
        while True:
            await websocket.receive_text()  # ignore client messages
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
```

- [ ] **Step 4: Add broadcast call to `grpc_servicer.py`**

> **Important:** The gRPC servicer runs in a thread pool (grpcio default). Calling `asyncio.create_task()` from a thread that does not own the event loop will raise "no running event loop". Use a thread-safe queue bridge instead.

First, add a module-level queue to `control-plane/app/routers/ws.py`:

```python
import asyncio

# Thread-safe queue for events from the gRPC thread pool
_event_queue: asyncio.Queue = asyncio.Queue()
```

Update `_status_ping_loop` to also drain the event queue:

```python
async def _status_ping_loop() -> None:
    """Background task: drain event queue and broadcast status every 3 seconds."""
    while True:
        # Drain all queued trace events first (non-blocking)
        while not _event_queue.empty():
            try:
                msg = _event_queue.get_nowait()
                await manager.broadcast(msg)
            except asyncio.QueueEmpty:
                break
        # Then do the status ping
        await asyncio.sleep(3)
        # ... (rest of ping logic unchanged)
```

Then in `grpc_servicer.py`, at the bottom of the `MutateContext` method:

```python
# Enqueue trace event for WebSocket broadcast (thread-safe — no asyncio required)
try:
    from app.routers.ws import _event_queue
    import json as _json
    event = _json.dumps({
        "type": "trace",
        "data": {
            "trace_id": request.trace_id,
            "session_id": _extract_session_id(payload),
            "model": request.model,
            "action": action_str,  # "PROCEED", "MUTATED", or "REJECT"
            "original_payload": payload,
            "final_payload": mutated_payload,  # the final payload after mutations
            "mutation_steps": steps,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    }, default=str)
    _event_queue.put_nowait(event)
except Exception as e:
    logger.debug(f"WS enqueue error: {e}")
```

Note: `action_str` is derived from the `ContextResponse.Action` enum before returning. `mutated_payload` is the payload dict after all mutations have been applied (or the original if `PROCEED`).

- [ ] **Step 5: Register ws router and start ping loop in `main.py`**

In `create_app()`, add:
```python
from app.routers import ws as ws_router
app.include_router(ws_router.router)
```

In the `lifespan` context manager, after `asyncio.create_task(_run_grpc())`, add:
```python
asyncio.create_task(ws_router._status_ping_loop())
```

- [ ] **Step 6: Run tests**

```bash
cd control-plane && python -m pytest tests/ -v
```
Expected: all tests PASS (including new WS tests)

- [ ] **Step 7: Commit**

```bash
git add control-plane/app/routers/ws.py \
        control-plane/app/grpc_servicer.py \
        control-plane/app/main.py \
        control-plane/tests/test_routers/test_ws.py
git commit -m "feat: add /ws/live WebSocket endpoint with trace broadcast and status ping"
```

---

## Task 3: Next.js project scaffold

**Files:**
- Create: `management-plane/package.json`
- Create: `management-plane/next.config.ts`
- Create: `management-plane/tsconfig.json`
- Create: `management-plane/styles/globals.css`
- Create: `management-plane/styles/neon.css`
- Create: `management-plane/app/layout.tsx`
- Create: `management-plane/app/page.tsx`
- Create: `management-plane/components/layout/Sidebar.tsx`
- Create: `management-plane/components/layout/Topbar.tsx`

- [ ] **Step 1: Scaffold the Next.js project**

```bash
cd governor
npx create-next-app@14 management-plane \
  --typescript \
  --app \
  --no-tailwind \
  --no-src-dir \
  --import-alias "@/*"
cd management-plane
```

- [ ] **Step 2: Install dependencies**

```bash
npm install \
  @blueprintjs/core@5 \
  @blueprintjs/table@5 \
  @blueprintjs/icons@5 \
  reactflow@12 \
  normalize.css
```

- [ ] **Step 3: Write `styles/globals.css`**

```css
@import "normalize.css";
@import "@blueprintjs/core/lib/css/blueprint.css";
@import "@blueprintjs/table/lib/css/table.css";
@import "@blueprintjs/icons/lib/css/blueprint-icons.css";
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');

:root {
  --bg-void:    #070a0f;
  --bg-surface: #0c1018;
  --bg-raised:  #111822;
  --bg-card:    #131b24;
  --border:     rgba(255,255,255,.055);
  --border-hi:  rgba(255,255,255,.10);

  --neon-cyan:   #00f5ff;
  --neon-green:  #39ff14;
  --neon-amber:  #ffaa00;
  --neon-red:    #ff2d55;
  --neon-purple: #c263ff;
  --neon-blue:   #4d9fff;
  --neon-teal:   #00e5c0;

  --font-mono: 'JetBrains Mono', monospace;
  --font-ui:   'Space Grotesk', sans-serif;

  --sidebar-w: 180px;
}

html, body {
  background: var(--bg-void);
  color: #dce8f0;
  font-family: var(--font-ui);
  height: 100%;
  margin: 0;
}

/* Scanline overlay */
body::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
  background: repeating-linear-gradient(
    0deg, transparent, transparent 3px,
    rgba(0,0,0,.02) 3px, rgba(0,0,0,.02) 4px
  );
}

/* Blueprint dark overrides */
.bp5-card { background: var(--bg-card) !important; }
.bp5-html-table { width: 100%; }
.bp5-html-table th {
  font-size: 9px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: rgba(255,255,255,.3);
  background: var(--bg-raised);
  border-bottom: 1px solid var(--border);
}
.bp5-html-table td {
  border-bottom: 1px solid rgba(255,255,255,.025);
  vertical-align: middle;
}
```

- [ ] **Step 4: Write `styles/neon.css`**

```css
.neon-cyan   { color: var(--neon-cyan);   text-shadow: 0 0 18px rgba(0,245,255,.5); }
.neon-green  { color: var(--neon-green);  text-shadow: 0 0 18px rgba(57,255,20,.5); }
.neon-amber  { color: var(--neon-amber);  text-shadow: 0 0 18px rgba(255,170,0,.5); }
.neon-red    { color: var(--neon-red);    text-shadow: 0 0 18px rgba(255,45,85,.5); }
.neon-purple { color: var(--neon-purple); text-shadow: 0 0 18px rgba(194,99,255,.5); }
.neon-teal   { color: var(--neon-teal);   text-shadow: 0 0 18px rgba(0,229,192,.5); }

@keyframes neon-pulse-cyan {
  0%,100% { box-shadow: 0 0 8px rgba(0,245,255,.2); }
  50%      { box-shadow: 0 0 20px rgba(0,245,255,.5); }
}
@keyframes neon-pulse-red {
  0%,100% { box-shadow: 0 0 8px rgba(255,45,85,.15); }
  50%      { box-shadow: 0 0 18px rgba(255,45,85,.4); }
}
@keyframes blink {
  0%,100% { opacity: 1; }
  50%      { opacity: .35; }
}
```

- [ ] **Step 5: Write `app/layout.tsx`**

```tsx
import type { Metadata } from "next";
import "@/styles/globals.css";
import "@/styles/neon.css";
import { Sidebar } from "@/components/layout/Sidebar";

export const metadata: Metadata = { title: "Governor" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="bp5-dark">
      <body>
        <Sidebar />
        <div style={{ marginLeft: "var(--sidebar-w)", minHeight: "100vh", display: "flex", flexDirection: "column" }}>
          {children}
        </div>
      </body>
    </html>
  );
}
```

- [ ] **Step 6: Write `app/page.tsx`** (redirect to /live)

```tsx
import { redirect } from "next/navigation";
export default function Home() { redirect("/live"); }
```

- [ ] **Step 7: Write `components/layout/Sidebar.tsx`**

```tsx
"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./Sidebar.module.css";

const NAV = [
  { section: "Monitor", items: [
    { href: "/live",      icon: "◉", label: "Live View",  badge: "REC" },
    { href: "/liveflow",  icon: "⬡", label: "LiveFlow" },
    { href: "/recordings",icon: "◎", label: "Recordings" },
  ]},
  { section: "Configure", items: [
    { href: "/rules", icon: "⊞", label: "Rules" },
  ]},
];

export function Sidebar() {
  const path = usePathname();
  return (
    <nav className={styles.sidebar}>
      <div className={styles.brand}>
        <div className={styles.brandMark}>G</div>
        <div>
          <div className={styles.brandName}>Governor</div>
          <div className={styles.brandVer}>v0.2.0</div>
        </div>
      </div>
      {NAV.map(group => (
        <div key={group.section}>
          <div className={styles.navSec}>{group.section}</div>
          {group.items.map(item => (
            <Link key={item.href} href={item.href}
              className={`${styles.navItem} ${path.startsWith(item.href) ? styles.active : ""}`}>
              <span className={styles.icon}>{item.icon}</span>
              <span className={styles.label}>{item.label}</span>
              {item.badge && <span className={styles.badge}>{item.badge}</span>}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
```

Create `components/layout/Sidebar.module.css` with the sidebar styles (fixed left, 180px, dark bg, neon active indicator — reference `docs/ui-mocks/01-live-view.html` for exact values).

- [ ] **Step 8: Create `lib/ws.tsx` stub** (full implementation in Task 4 Step 6, but Topbar needs the import to exist)

```tsx
"use client";
import { createContext, useContext } from "react";
interface WsContextValue { connected: boolean; lastTrace: null; lastStatus: null; traces: never[]; }
const WsContext = createContext<WsContextValue>({ connected: false, lastTrace: null, lastStatus: null, traces: [] });
export function WebSocketProvider({ children }: { children: React.ReactNode }) { return <WsContext.Provider value={{ connected: false, lastTrace: null, lastStatus: null, traces: [] }}>{children}</WsContext.Provider>; }
export function useWebSocket() { return useContext(WsContext); }
```

This stub will be replaced wholesale in Task 4 Step 6.

- [ ] **Step 9: Write `components/layout/Topbar.tsx`**

```tsx
"use client";
import { useWebSocket } from "@/lib/ws";
import styles from "./Topbar.module.css";

interface TopbarProps {
  crumb?: string;
  title: string;
  right?: React.ReactNode;
}

export function Topbar({ crumb, title, right }: TopbarProps) {
  const { connected } = useWebSocket();
  return (
    <header className={styles.topbar}>
      {crumb && <span className={styles.crumb}>{crumb} ›</span>}
      <span className={styles.title}>{title}</span>
      <div className={styles.right}>
        <div className={`${styles.pill} ${connected ? styles.connected : styles.disconnected}`}>
          <div className={styles.pillDot} />
          {connected ? "WS Connected" : "WS Disconnected"}
        </div>
        {right}
      </div>
    </header>
  );
}
```

- [ ] **Step 10: Verify the app builds**

```bash
cd management-plane && npm run build
```
Expected: Build succeeds with no errors (pages may be mostly empty stubs at this point).

- [ ] **Step 11: Commit**

```bash
cd ..
git add management-plane/
git commit -m "feat: scaffold Next.js management-plane with Blueprint, CSS vars, Sidebar, Topbar"
```

---

## Task 4: `lib/` — models, tokens, API client, hierarchy

**Files:**
- Create: `management-plane/lib/models.ts`
- Create: `management-plane/lib/tokens.ts`
- Create: `management-plane/lib/api.ts`
- Create: `management-plane/lib/hierarchy.ts`

- [ ] **Step 1: Write `lib/models.ts`**

```ts
export type Action = "PROCEED" | "MUTATED" | "REJECT";

export interface MutationStep {
  rule_id: string;
  rule_name: string;
  priority: number;
  payload_after: Record<string, unknown>;
}

export interface TraceOut {
  id: string;
  trace_id: string;
  recording_session_id: string;
  model: string;
  action: Action;
  recorded_at: string;
  session_id?: string;
}

export interface TraceDetailOut extends TraceOut {
  original_payload: Record<string, unknown>;
  final_payload: Record<string, unknown>;
  mutation_steps: MutationStep[];
}

export interface RecordingListItem {
  id: string;
  started_at: string;
  stopped_at: string | null;
  is_active: boolean;
  trace_count: number;
}

export interface RuleOut {
  id: string;
  name: string;
  priority: number;
  is_active: boolean;
  match_logic: Record<string, unknown>;
  mutate_logic: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WsTraceEvent {
  type: "trace";
  data: TraceOut & { mutation_steps: MutationStep[] };
}

export interface WsStatusEvent {
  type: "status";
  data: {
    is_recording: boolean;
    session_id: string | null;
    db: "ok" | "error";
    redis: "ok" | "error";
    ts: string;
  };
}

export type WsEvent = WsTraceEvent | WsStatusEvent;
```

- [ ] **Step 2: Write `lib/tokens.ts`**

```ts
export const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  "claude-opus-4":     200_000,
  "claude-sonnet-4-6": 200_000,
  "claude-haiku-4-5":  200_000,
};

export function getContextWindow(model: string): number {
  return MODEL_CONTEXT_WINDOWS[model] ?? 200_000;
}

/** Rough token estimate: character count / 4 */
export function estimateTokens(obj: unknown): number {
  return Math.round(JSON.stringify(obj ?? "").length / 4);
}

export function contextFillPercent(model: string, tokens: number): number {
  return Math.min(100, Math.round((tokens / getContextWindow(model)) * 100));
}

export function fillColor(pct: number): string {
  if (pct >= 75) return "var(--neon-red)";
  if (pct >= 40) return "var(--neon-amber)";
  return "var(--neon-green)";
}
```

- [ ] **Step 3: Configure Jest**

```bash
npm install --save-dev jest @types/jest ts-jest @swc/jest @swc/core
```

Create `management-plane/jest.config.ts`:

```ts
import type { Config } from "jest";
const config: Config = {
  testEnvironment: "node",
  transform: { "^.+\\.(t|j)sx?$": ["@swc/jest", {}] },
  moduleNameMapper: { "^@/(.*)$": "<rootDir>/$1" },
};
export default config;
```

Create `management-plane/__tests__/` directory.

- [ ] **Step 3b: Write tests for tokens.ts**

```ts
// management-plane/__tests__/tokens.test.ts
import { getContextWindow, estimateTokens, contextFillPercent, fillColor } from "@/lib/tokens";

describe("tokens", () => {
  test("known model returns correct window", () => {
    expect(getContextWindow("claude-sonnet-4-6")).toBe(200_000);
  });
  test("unknown model falls back to 200k", () => {
    expect(getContextWindow("unknown-model")).toBe(200_000);
  });
  test("estimateTokens returns non-zero for non-empty input", () => {
    expect(estimateTokens({ text: "hello world" })).toBeGreaterThan(0);
  });
  test("contextFillPercent caps at 100", () => {
    expect(contextFillPercent("claude-sonnet-4-6", 999_999)).toBe(100);
  });
  test("fillColor returns red above 75%", () => {
    expect(fillColor(80)).toBe("var(--neon-red)");
  });
  test("fillColor returns green below 40%", () => {
    expect(fillColor(20)).toBe("var(--neon-green)");
  });
});
```

Run: `cd management-plane && npx jest __tests__/tokens.test.ts`
Expected: all PASS

- [ ] **Step 4: Write `lib/api.ts`**

```ts
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json();
}

export const api = {
  // Recording
  startRecording: () =>
    req<{ session_id: string; started_at: string }>("/recording/start", { method: "POST" }),
  stopRecording: () =>
    req<{ session_id: string; traces_flushed: number; stopped_at: string }>("/recording/stop", { method: "POST" }),
  getRecordingStatus: () =>
    req<{ is_active: boolean; session_id: string | null; started_at: string | null }>("/recording/status"),

  // Recordings
  listRecordings: () =>
    req<import("./models").RecordingListItem[]>("/recordings"),
  deleteRecording: (id: string) =>
    req<void>(`/recordings/${id}`, { method: "DELETE" }),

  // Traces
  listTraces: (params: { session_id?: string; page?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.session_id) qs.set("session_id", params.session_id);
    if (params.page) qs.set("page", String(params.page));
    return req<import("./models").TraceOut[]>(`/traces?${qs}`);
  },
  getTrace: (traceId: string) =>
    req<import("./models").TraceDetailOut>(`/traces/${traceId}`),

  // Rules
  listRules: () => req<import("./models").RuleOut[]>("/rules"),

  // Health
  getHealth: () =>
    req<{ status: string; components: { db: string; redis: string } }>("/health"),
};
```

- [ ] **Step 5: Write `lib/hierarchy.ts`** — agent hierarchy extraction

```ts
import type { TraceOut, WsTraceEvent } from "./models";

export interface AgentCall {
  traceId: string;
  action: string;
  recordedAt: string;
  tokenEstimate: number;
}

export interface Agent {
  id: string;        // unique per agent within session
  name: string;      // inferred from Agent tool input.description or first prompt words
  model: string;
  sessionId: string;
  parentId: string | null;
  calls: AgentCall[];
  spawnedAt: string | null;  // recordedAt of parent call that spawned this agent
}

export interface AgentSession {
  sessionId: string;
  agents: Map<string, Agent>;
  rootAgentId: string | null;
}

export class HierarchyStore {
  private sessions = new Map<string, AgentSession>();

  ingest(event: WsTraceEvent): void {
    const { data } = event;
    const sessionId = data.session_id ?? "unknown";

    let session = this.sessions.get(sessionId);
    if (!session) {
      session = { sessionId, agents: new Map(), rootAgentId: null };
      this.sessions.set(sessionId, session);
    }

    // Identify agent: traces with no prior assistant messages = new agent
    const payload = (data as any).original_payload ?? {};
    const messages: any[] = payload.messages ?? [];
    const hasAssistantTurn = messages.some((m: any) => m.role === "assistant");
    const agentKey = `${data.model}-${sessionId}-${hasAssistantTurn ? "cont" : data.trace_id.slice(0, 8)}`;

    let agent = session.agents.get(agentKey);
    if (!agent) {
      agent = {
        id: agentKey,
        name: _inferName(payload),
        model: data.model,
        sessionId,
        parentId: null,
        calls: [],
        spawnedAt: null,
      };
      session.agents.set(agentKey, agent);
      if (!session.rootAgentId) session.rootAgentId = agentKey;
    }

    agent.calls.push({
      traceId: data.trace_id,
      action: data.action,
      recordedAt: data.recorded_at,
      tokenEstimate: 0,
    });

    // Detect Agent tool_use spawns
    for (const msg of messages) {
      const blocks: any[] = Array.isArray(msg.content) ? msg.content : [];
      for (const block of blocks) {
        if (block.type === "tool_use" && block.name === "Agent") {
          const childName = block.input?.description ?? block.input?.prompt?.slice(0, 30) ?? "agent";
          const childKey = `spawned-${block.id}`;
          if (!session.agents.has(childKey)) {
            session.agents.set(childKey, {
              id: childKey,
              name: childName,
              model: block.input?.model ?? data.model,
              sessionId,
              parentId: agentKey,
              calls: [],
              spawnedAt: data.recorded_at,
            });
          }
        }
      }
    }
  }

  getSessions(): AgentSession[] {
    return Array.from(this.sessions.values());
  }

  getSession(sessionId: string): AgentSession | undefined {
    return this.sessions.get(sessionId);
  }
}

function _inferName(payload: any): string {
  const sys: any[] = payload.system ?? [];
  for (const block of sys) {
    if (block.type === "text" && block.text?.includes("You are Claude Code")) return "main-agent";
  }
  return "agent";
}
```

- [ ] **Step 6: Write `lib/ws.tsx`** — WebSocket context provider

```tsx
"use client";
import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import type { WsEvent, WsTraceEvent, WsStatusEvent } from "./models";

interface WsContextValue {
  connected: boolean;
  lastTrace: WsTraceEvent | null;
  lastStatus: WsStatusEvent | null;
  traces: WsTraceEvent[];  // ring buffer, max 200
}

const WsContext = createContext<WsContextValue>({
  connected: false,
  lastTrace: null,
  lastStatus: null,
  traces: [],
});

const WS_URL = (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8080") + "/ws/live";
const MAX_TRACES = 200;

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [lastTrace, setLastTrace] = useState<WsTraceEvent | null>(null);
  const [lastStatus, setLastStatus] = useState<WsStatusEvent | null>(null);
  const [traces, setTraces] = useState<WsTraceEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => { setConnected(true); retryRef.current = 0; };
    ws.onclose = () => {
      setConnected(false);
      const delay = Math.min(1000 * Math.pow(2, retryRef.current), 30_000);
      retryRef.current++;
      setTimeout(connect, delay);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (e) => {
      try {
        const event: WsEvent = JSON.parse(e.data);
        if (event.type === "trace") {
          setLastTrace(event as WsTraceEvent);
          setTraces(prev => [event as WsTraceEvent, ...prev].slice(0, MAX_TRACES));
        } else if (event.type === "status") {
          setLastStatus(event as WsStatusEvent);
        }
      } catch { /* ignore malformed */ }
    };
  }, []);

  useEffect(() => { connect(); return () => wsRef.current?.close(); }, [connect]);

  return (
    <WsContext.Provider value={{ connected, lastTrace, lastStatus, traces }}>
      {children}
    </WsContext.Provider>
  );
}

export function useWebSocket() { return useContext(WsContext); }
```

- [ ] **Step 6b: Wrap `app/layout.tsx` with `WebSocketProvider`**

In `management-plane/app/layout.tsx`, modify the import and the body:

```tsx
import { WebSocketProvider } from "@/lib/ws";

// In RootLayout, replace:
//   <div style={...}>{children}</div>
// with:
<WebSocketProvider>
  <div style={{ marginLeft: "var(--sidebar-w)", minHeight: "100vh", display: "flex", flexDirection: "column" }}>
    {children}
  </div>
</WebSocketProvider>
```

- [ ] **Step 7: Run tests**

```bash
cd management-plane && npx jest --passWithNoTests
```

- [ ] **Step 8: Commit**

```bash
git add management-plane/lib/ management-plane/__tests__/
git commit -m "feat: add lib/ layer — models, tokens, api client, hierarchy extractor, WS provider"
```

---

## Task 5: Shared components — NeonBadge, TokenBar, TraceDetailSlideOver

**Files:**
- Create: `management-plane/components/shared/NeonBadge.tsx`
- Create: `management-plane/components/shared/TokenBar.tsx`
- Create: `management-plane/components/shared/TraceDetailSlideOver.tsx`

- [ ] **Step 1: Write `NeonBadge.tsx`**

```tsx
import type { Action } from "@/lib/models";
import styles from "./NeonBadge.module.css";

interface Props { action: Action | "LIVE"; }

const CONFIG = {
  PROCEED: { label: "— PROCEED", cls: "proceed" },
  MUTATED: { label: "● MUTATED", cls: "mutated" },
  REJECT:  { label: "✕ REJECT",  cls: "reject"  },
  LIVE:    { label: "● LIVE",    cls: "live"    },
};

export function NeonBadge({ action }: Props) {
  const { label, cls } = CONFIG[action] ?? CONFIG.PROCEED;
  return <span className={`${styles.badge} ${styles[cls]}`}>{label}</span>;
}
```

- [ ] **Step 2: Write `TokenBar.tsx`**

```tsx
import { contextFillPercent, fillColor } from "@/lib/tokens";
import styles from "./TokenBar.module.css";

interface Props {
  model: string;
  tokens: number;
  showLabel?: boolean;
}

export function TokenBar({ model, tokens, showLabel = true }: Props) {
  const pct = contextFillPercent(model, tokens);
  const color = fillColor(pct);
  return (
    <div className={styles.wrap}>
      <div className={styles.bg}>
        <div className={styles.fill} style={{ width: `${pct}%`, background: color, boxShadow: `0 0 6px ${color}` }} />
      </div>
      {showLabel && (
        <div className={styles.label} style={{ fontFamily: "var(--font-mono)" }}>
          {tokens >= 1000 ? `${Math.round(tokens / 1000)}k` : tokens}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Write `TraceDetailSlideOver.tsx`**

This is the most complex shared component. It renders a 620px right drawer with 6 tabs (Summary, Scalars, Tools, System, Messages, Metadata), each diffing `original_payload` vs `final_payload`.

Reference `docs/ui-mocks/05-trace-detail.html` for the exact visual structure.

Key implementation details:
- Use `useEffect` to trap scroll when open (add `overflow: hidden` to `body`)
- Render the anatomy sections (System, Tools, Messages, Tool Results) as collapsible `<details>` elements styled to match the mock
- For each field, produce a `FieldDiff` showing removed lines in red and added lines in green
- The "Open in tab" button navigates to `/traces/{traceId}` using `window.open`

```tsx
"use client";
import { useEffect } from "react";
import type { TraceDetailOut } from "@/lib/models";
import { NeonBadge } from "./NeonBadge";
import { estimateTokens } from "@/lib/tokens";
import styles from "./TraceDetailSlideOver.module.css";

interface Props {
  trace: TraceDetailOut | null;
  onClose: () => void;
}

export function TraceDetailSlideOver({ trace, onClose }: Props) {
  useEffect(() => {
    if (trace) document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, [trace]);

  if (!trace) return null;

  const origTokens = estimateTokens(trace.original_payload);
  const finalTokens = estimateTokens(trace.final_payload);
  const saved = origTokens - finalTokens;
  const savedPct = origTokens > 0 ? Math.round((saved / origTokens) * 100) : 0;

  return (
    <>
      <div className={styles.overlay} onClick={onClose} />
      <div className={styles.panel}>
        <div className={styles.header}>
          <div className={styles.headerTop}>
            <span className={styles.title}>Trace Detail</span>
            <button className={styles.openTab}
              onClick={() => window.open(`/traces/${trace.trace_id}`, "_blank")}>
              ↗ Open in tab
            </button>
            <button className={styles.close} onClick={onClose}>✕</button>
          </div>
          <div className={styles.pills}>
            <NeonBadge action={trace.action} />
            <span className={styles.modelPill}>{trace.model}</span>
            {saved > 0 && (
              <span className={styles.savedPill}>−{savedPct}% · {Math.round(saved/1000)}k saved</span>
            )}
          </div>
          <div className={styles.traceId}>
            {trace.trace_id} · {new Date(trace.recorded_at).toLocaleTimeString()}
          </div>
        </div>
        {/* Tabs and content — see ui-mock for detail */}
        <div className={styles.body}>
          <SummaryTab trace={trace} origTokens={origTokens} finalTokens={finalTokens} />
        </div>
      </div>
    </>
  );
}

function SummaryTab({ trace, origTokens, finalTokens }: { trace: TraceDetailOut; origTokens: number; finalTokens: number }) {
  return (
    <div>
      <div className={styles.secLabel}>Token Impact</div>
      {/* Two bars: original and final */}
      {/* Mutation steps list */}
      {trace.mutation_steps.map((step, i) => (
        <div key={step.rule_id} className={styles.step}>
          <span className={styles.stepNum}>{i + 1}</span>
          <div>
            <div className={styles.stepRule}>{step.rule_name}</div>
            <div className={styles.stepOp}>priority {step.priority}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add management-plane/components/shared/
git commit -m "feat: add shared components — NeonBadge, TokenBar, TraceDetailSlideOver"
```

---

## Task 6: Live View (`/live`)

**Files:**
- Create: `management-plane/components/live/StatCards.tsx`
- Create: `management-plane/components/live/TraceTable.tsx`
- Create: `management-plane/components/live/SessionsPanel.tsx`
- Create: `management-plane/app/live/page.tsx`

Reference: `docs/ui-mocks/01-live-view.html`

- [ ] **Step 1: Write `StatCards.tsx`**

```tsx
import { useWebSocket } from "@/lib/ws";
import { useMemo } from "react";
import { estimateTokens } from "@/lib/tokens";
import styles from "./StatCards.module.css";

export function StatCards() {
  const { traces } = useWebSocket();

  const stats = useMemo(() => {
    const intercepted = traces.length;
    const mutated = traces.filter(t => t.data.action === "MUTATED").length;
    const rejected = traces.filter(t => t.data.action === "REJECT").length;
    const tokensSaved = traces
      .filter(t => t.data.action === "MUTATED")
      .reduce((sum, t) => {
        const orig = estimateTokens((t.data as any).original_payload);
        const final = estimateTokens((t.data as any).final_payload);
        return sum + Math.max(0, orig - final);
      }, 0);
    return { intercepted, mutated, rejected, tokensSaved };
  }, [traces]);

  return (
    <div className={styles.grid}>
      <Card label="Intercepted"  value={stats.intercepted}  color="cyan" />
      <Card label="Mutated"      value={stats.mutated}      color="amber" sub={`${stats.intercepted ? Math.round(stats.mutated/stats.intercepted*100) : 0}% of traffic`} />
      <Card label="~Est. Saved"  value={stats.tokensSaved >= 1000 ? `${Math.round(stats.tokensSaved/1000)}k` : stats.tokensSaved} color="green" sub="tokens (approx)" />
      <Card label="Rejected"     value={stats.rejected}     color="red" />
    </div>
  );
}

function Card({ label, value, color, sub }: { label: string; value: number; color: string; sub?: string }) {
  return (
    <div className={`${styles.card} ${styles[color]}`}>
      <div className={styles.label}>{label}</div>
      <div className={`${styles.value} neon-${color}`}>{value.toLocaleString()}</div>
      {sub && <div className={styles.sub}>{sub}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Write `TraceTable.tsx`**

Renders a Blueprint `HTMLTable` consuming `traces` from `useWebSocket()`. Columns: Trace ID, Action badge, Model, Context Weight (TokenBar), Rules Matched chips, Time. New rows get a flash class via `useEffect` on `lastTrace` changes. Clicking a row calls `onSelect(trace)`.

```tsx
"use client";
import { useWebSocket } from "@/lib/ws";
import { NeonBadge } from "@/components/shared/NeonBadge";
import { TokenBar } from "@/components/shared/TokenBar";
import { estimateTokens } from "@/lib/tokens";
import type { WsTraceEvent } from "@/lib/models";
import styles from "./TraceTable.module.css";

interface Props { onSelect: (trace: WsTraceEvent) => void; }

export function TraceTable({ onSelect }: Props) {
  const { traces } = useWebSocket();

  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>Trace ID</th><th>Action</th><th>Model</th>
          <th>Context Weight</th><th>Time</th>
        </tr>
      </thead>
      <tbody>
        {traces.slice(0, 50).map(t => {
          const tokens = estimateTokens(t.data);
          return (
            <tr key={t.data.trace_id} onClick={() => onSelect(t)} className={styles.row}>
              <td><span className={`${styles.traceId} neon-cyan`}>{t.data.trace_id.slice(0, 8)}…</span></td>
              <td><NeonBadge action={t.data.action} /></td>
              <td><span className={styles.modelTag}>{t.data.model}</span></td>
              <td><TokenBar model={t.data.model} tokens={tokens} /></td>
              <td><span className={styles.ts}>{new Date(t.data.recorded_at).toLocaleTimeString()}</span></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 3: Write `SessionsPanel.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useWebSocket } from "@/lib/ws";
import { api } from "@/lib/api";
import type { RecordingListItem } from "@/lib/models";
import styles from "./SessionsPanel.module.css";

export function SessionsPanel() {
  const [sessions, setSessions] = useState<RecordingListItem[]>([]);
  const { lastStatus } = useWebSocket();
  const router = useRouter();

  useEffect(() => {
    api.listRecordings().then(setSessions).catch(() => {});
  }, [lastStatus]);

  return (
    <div className={styles.panel}>
      <div className={styles.header}>Sessions</div>
      {sessions.length === 0 && (
        <div className={styles.empty}>No recording sessions yet.</div>
      )}
      {sessions.map(s => (
        <div key={s.id} className={`${styles.row} ${s.is_active ? styles.active : ""}`}
          onClick={() => router.push(`/liveflow?session=${s.id}&view=timeline`)}>
          <div className={styles.sessionId}>{s.id.slice(0, 8)}…</div>
          <div className={styles.meta}>
            {s.is_active
              ? <span className={`${styles.recBadge} neon-red`}>● REC</span>
              : <span className={styles.dim}>{new Date(s.started_at).toLocaleString()}</span>}
          </div>
          <div className={styles.count}>{s.trace_count} traces</div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3b: Write `RecordingButton.tsx`**

```tsx
"use client";
import { useState, useEffect } from "react";
import { useWebSocket } from "@/lib/ws";
import { api } from "@/lib/api";
import styles from "./RecordingButton.module.css";

export function RecordingButton() {
  const { lastStatus } = useWebSocket();
  const [loading, setLoading] = useState(false);

  const isRecording = lastStatus?.data.is_recording ?? false;
  const elapsed = lastStatus?.data.elapsed_seconds ?? 0;

  const formatElapsed = (s: number) =>
    `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  const handleClick = async () => {
    setLoading(true);
    try {
      if (isRecording) await api.stopRecording();
      else await api.startRecording();
    } catch (e) {
      console.error("Recording toggle failed", e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      className={`${styles.btn} ${isRecording ? styles.recording : styles.idle}`}
      onClick={handleClick}
      disabled={loading}
    >
      {isRecording
        ? `⬛ Stop · ${formatElapsed(elapsed)}`
        : "▶ Start Recording"}
    </button>
  );
}
```

- [ ] **Step 4: Write `app/live/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import { Topbar } from "@/components/layout/Topbar";
import { StatCards } from "@/components/live/StatCards";
import { TraceTable } from "@/components/live/TraceTable";
import { SessionsPanel } from "@/components/live/SessionsPanel";
import { TraceDetailSlideOver } from "@/components/shared/TraceDetailSlideOver";
import { RecordingButton } from "@/components/live/RecordingButton";
import { useWebSocket } from "@/lib/ws";
import { api } from "@/lib/api";
import type { WsTraceEvent, TraceDetailOut } from "@/lib/models";
import styles from "./page.module.css";

export default function LivePage() {
  const { lastStatus } = useWebSocket();
  const [selectedTrace, setSelectedTrace] = useState<TraceDetailOut | null>(null);

  const handleSelectTrace = async (t: WsTraceEvent) => {
    try {
      const detail = await api.getTrace(t.data.trace_id);
      setSelectedTrace(detail);
    } catch { /* show error toast */ }
  };

  return (
    <>
      <Topbar title="Live View" right={<RecordingButton />} />
      <main className={styles.content}>
        <StatCards />
        <div className={styles.split}>
          <TraceTable onSelect={handleSelectTrace} />
          <SessionsPanel />
        </div>
      </main>
      <TraceDetailSlideOver trace={selectedTrace} onClose={() => setSelectedTrace(null)} />
    </>
  );
}
```

- [ ] **Step 5: Verify in browser**

```bash
cd management-plane && npm run dev
```
Open `http://localhost:3000/live`. Confirm stat cards, table, and sessions panel render. With the control plane running locally, WebSocket connection should show "WS Connected" and traces appear in real time.

- [ ] **Step 6: Commit**

```bash
git add management-plane/components/live/ management-plane/app/live/
git commit -m "feat: implement Live View dashboard with stat cards, trace table, sessions panel"
```

---

## Task 7: LiveFlow — Hierarchy view (`/liveflow`)

**Files:**
- Create: `management-plane/components/liveflow/HierarchyGraph.tsx`
- Create: `management-plane/components/liveflow/AgentNode.tsx`
- Create: `management-plane/app/liveflow/page.tsx`

Reference: `docs/ui-mocks/02-liveflow-hierarchy.html`

- [ ] **Step 0: Install dagre**

```bash
cd management-plane && npm install dagre @types/dagre
```

> Must run before Steps 1–3 since `dagreLayout.ts` and `HierarchyGraph.tsx` import it.

- [ ] **Step 1: Write `AgentNode.tsx`** — ReactFlow custom node

```tsx
import { Handle, Position, type NodeProps } from "reactflow";
import { TokenBar } from "@/components/shared/TokenBar";
import styles from "./AgentNode.module.css";

export type AgentNodeData = {
  name: string;
  model: string;
  type: "orchestrator" | "subagent" | "leaf";
  callCount: number;
  tokenEstimate: number;
  lastAction: "PROCEED" | "MUTATED" | "REJECT" | "LIVE";
};

export function AgentNode({ data, selected }: NodeProps<AgentNodeData>) {
  const colorVar = {
    orchestrator: "var(--neon-cyan)",
    subagent:     "var(--neon-purple)",
    leaf:         "var(--neon-blue)",
  }[data.type];

  return (
    <div className={`${styles.node} ${styles[data.type]} ${selected ? styles.selected : ""}`}>
      <Handle type="target" position={Position.Top} className={styles.handle} />
      <div className={styles.inner}>
        <div className={styles.typeLabel} style={{ color: colorVar }}>{data.type}</div>
        <div className={styles.name} style={{ color: colorVar }}>{data.name}</div>
        <div className={styles.model}>{data.model}</div>
        <TokenBar model={data.model} tokens={data.tokenEstimate} showLabel />
      </div>
      <div className={styles.footer}>
        <span className={styles.callCount}>{data.callCount} calls</span>
        <span className={`${styles.dot} ${styles[data.lastAction.toLowerCase()]}`} />
      </div>
      <Handle type="source" position={Position.Bottom} className={styles.handle} />
    </div>
  );
}
```

- [ ] **Step 2: Write `HierarchyGraph.tsx`**

Uses `useWebSocket` to get traces, feeds them into a `HierarchyStore` instance (from `lib/hierarchy.ts`), converts the store's agents into ReactFlow `nodes` and `edges`, and renders with auto-layout via `dagre` (install: `npm install dagre @types/dagre`).

```tsx
"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background, Controls, MiniMap,
  useNodesState, useEdgesState,
  type Node, type Edge,
} from "reactflow";
import "reactflow/dist/style.css";
import { useWebSocket } from "@/lib/ws";
import { HierarchyStore } from "@/lib/hierarchy";
import { AgentNode, type AgentNodeData } from "./AgentNode";
import { applyDagreLayout } from "./dagreLayout";

const nodeTypes = { agentNode: AgentNode };

export function HierarchyGraph({ sessionId }: { sessionId?: string }) {
  const { traces } = useWebSocket();
  const storeRef = useRef(new HierarchyStore());
  const ingestedRef = useRef(0);  // track how many traces have been ingested
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    const store = storeRef.current;
    // Only ingest new traces since last render to avoid duplicates
    const newTraces = traces.slice(0, traces.length - ingestedRef.current);
    newTraces.reverse().forEach(t => store.ingest(t));
    ingestedRef.current = traces.length;
    const session = sessionId
      ? store.getSession(sessionId)
      : store.getSessions()[0];
    if (!session) return;

    const rfNodes: Node<AgentNodeData>[] = [];
    const rfEdges: Edge[] = [];

    session.agents.forEach(agent => {
      rfNodes.push({
        id: agent.id,
        type: "agentNode",
        position: { x: 0, y: 0 }, // dagre will set this
        data: {
          name: agent.name,
          model: agent.model,
          type: agent.parentId === null ? "orchestrator"
              : agent.calls.length === 0 ? "leaf" : "subagent",
          callCount: agent.calls.length,
          tokenEstimate: 0,
          lastAction: agent.calls.at(-1)?.action as any ?? "PROCEED",
        },
      });
      if (agent.parentId) {
        rfEdges.push({
          id: `${agent.parentId}-${agent.id}`,
          source: agent.parentId,
          target: agent.id,
          animated: true,
          style: { stroke: "rgba(194,99,255,.4)", strokeDasharray: "5 4" },
        });
      }
    });

    const { nodes: laid, edges: laidEdges } = applyDagreLayout(rfNodes, rfEdges);
    setNodes(laid);
    setEdges(laidEdges);
  }, [traces, sessionId]);

  return (
    <ReactFlow
      nodes={nodes} edges={edges}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      fitView
    >
      <Background color="rgba(255,255,255,.03)" gap={28} />
      <Controls />
      <MiniMap />
    </ReactFlow>
  );
}
```

Create `management-plane/components/liveflow/dagreLayout.ts`:
```ts
import dagre from "dagre";
import type { Node, Edge } from "reactflow";

export function applyDagreLayout(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 60, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach(n => g.setNode(n.id, { width: 160, height: 90 }));
  edges.forEach(e => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return {
    nodes: nodes.map(n => {
      const { x, y } = g.node(n.id);
      return { ...n, position: { x: x - 80, y: y - 45 } };
    }),
    edges,
  };
}
```

- [ ] **Step 3: Write `app/liveflow/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Topbar } from "@/components/layout/Topbar";
import { HierarchyGraph } from "@/components/liveflow/HierarchyGraph";
import { TimelineView } from "@/components/liveflow/TimelineView";
import styles from "./page.module.css";

export default function LiveFlowPage() {
  const params = useSearchParams();
  const view = params.get("view") ?? "hierarchy";
  const sessionId = params.get("session") ?? undefined;

  return (
    <>
      <Topbar title="LiveFlow" />
      <div className={styles.toolbar}>
        <a href="/liveflow" className={view === "hierarchy" ? styles.active : ""}>⬡ Hierarchy</a>
        <a href="/liveflow?view=timeline" className={view === "timeline" ? styles.active : ""}>⏱ Timeline</a>
      </div>
      <div className={styles.canvas}>
        {view === "hierarchy"
          ? <HierarchyGraph sessionId={sessionId} />
          : <TimelineView sessionId={sessionId} />
        }
      </div>
    </>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add management-plane/components/liveflow/HierarchyGraph.tsx \
        management-plane/components/liveflow/AgentNode.tsx \
        management-plane/components/liveflow/dagreLayout.ts \
        management-plane/app/liveflow/
git commit -m "feat: implement LiveFlow hierarchy view with ReactFlow + dagre auto-layout"
```

---

## Task 8: LiveFlow — Timeline view

**Files:**
- Create: `management-plane/components/liveflow/TimelineView.tsx`
- Create: `management-plane/components/liveflow/CallPill.tsx`

Reference: `docs/ui-mocks/03-liveflow-timeline.html`

- [ ] **Step 1: Write `CallPill.tsx`**

```tsx
import type { AgentCall } from "@/lib/hierarchy";
import styles from "./CallPill.module.css";

interface Props {
  call: AgentCall;
  x: number;
  width: number;
  color: string;
  onClick: () => void;
}

export function CallPill({ call, x, width, color, onClick }: Props) {
  const hasMutation = call.action === "MUTATED";
  const hasReject   = call.action === "REJECT";
  return (
    <div
      className={`${styles.pill} ${hasReject ? styles.reject : ""}`}
      style={{ left: x, width: Math.max(width, 12), borderColor: color + "55", background: color + "18", boxShadow: `0 0 8px ${color}28` }}
      onClick={onClick}
      title={`#${call.traceId.slice(0,6)} · ${call.action} · ${new Date(call.recordedAt).toLocaleTimeString()}`}
    >
      {width > 40 && <span className={styles.label} style={{ color }}>{call.action.slice(0,3)}</span>}
      {hasMutation && <span className={styles.mutDot} style={{ background: "var(--neon-amber)" }} />}
      {hasReject   && <span className={styles.mutDot} style={{ background: "var(--neon-red)" }} />}
    </div>
  );
}
```

- [ ] **Step 2: Write `TimelineView.tsx`**

Full swim-lane SVG timeline. Key implementation points:
- Derive `agents` and `sessionDuration` from `HierarchyStore`
- `sPx(sec)` maps seconds to canvas pixels: `sec / totalSec * CANVAS_W`
- Lane labels are fixed-position `<div>`s; canvas is horizontally scrollable
- Spawn curves are `<path>` SVG bezier elements overlaid on all lanes
- Live cursor is a `<div>` with `position: absolute`; scroll canvas on mount so it's at 70% from left
- Clicking a lane label navigates to `/liveflow/${agentId}` (Agent Conversation view)

```tsx
"use client";
import { useEffect, useMemo, useRef } from "react";
import { useWebSocket } from "@/lib/ws";
import { HierarchyStore } from "@/lib/hierarchy";
import { CallPill } from "./CallPill";
import styles from "./TimelineView.module.css";

const CANVAS_W = 1400;
const LANE_H = 54;
const LANE_GAP = 10;
const ROW_H = LANE_H + LANE_GAP;
const RULER_H = 32;

const AGENT_COLORS = ["#00f5ff","#c263ff","#ff6ec7","#ffaa00","#39ff14","#4d9fff"];

export function TimelineView({ sessionId }: { sessionId?: string }) {
  const { traces } = useWebSocket();
  const storeRef = useRef(new HierarchyStore());
  const ingestedRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Only ingest new traces to avoid duplicates
    const newTraces = traces.slice(0, traces.length - ingestedRef.current);
    newTraces.reverse().forEach(t => storeRef.current.ingest(t));
    ingestedRef.current = traces.length;
  }, [traces]);

  const session = sessionId
    ? storeRef.current.getSession(sessionId)
    : storeRef.current.getSessions()[0];

  const agents = session ? Array.from(session.agents.values()) : [];

  const allTimes = agents.flatMap(a => a.calls.map(c => new Date(c.recordedAt).getTime()));
  const minT = allTimes.length ? Math.min(...allTimes) : Date.now();
  const maxT = allTimes.length ? Math.max(...allTimes) : Date.now() + 60_000;
  const totalSec = Math.max((maxT - minT) / 1000, 60);

  const sPx = (sec: number) => (sec / totalSec) * CANVAS_W;

  // Auto-scroll to live cursor
  useEffect(() => {
    if (scrollRef.current) {
      const livePx = sPx((Date.now() - minT) / 1000);
      scrollRef.current.scrollLeft = Math.max(0, livePx - scrollRef.current.clientWidth * 0.7);
    }
  }, [agents.length]);

  return (
    <div className={styles.layout}>
      {/* Lane labels */}
      <div className={styles.labels} style={{ paddingTop: RULER_H }}>
        {agents.map((agent, i) => (
          <a key={agent.id} href={`/liveflow/${encodeURIComponent(agent.id)}`}
            className={styles.laneLabel} style={{ height: ROW_H }}>
            <span className={styles.dot} style={{ background: AGENT_COLORS[i % AGENT_COLORS.length] }} />
            <div>
              <div className={styles.agentName} style={{ color: AGENT_COLORS[i % AGENT_COLORS.length] }}>{agent.name}</div>
              <div className={styles.agentModel}>{agent.model}</div>
            </div>
          </a>
        ))}
      </div>

      {/* Scrollable canvas */}
      <div ref={scrollRef} className={styles.canvasWrap}>
        <div style={{ width: CANVAS_W, position: "relative" }}>
          {/* Ruler */}
          <svg width={CANVAS_W} height={RULER_H} className={styles.ruler}>
            {Array.from({ length: Math.ceil(totalSec / 15) + 1 }, (_, i) => i * 15).map(s => (
              <g key={s}>
                <line x1={sPx(s)} x2={sPx(s)} y1={s % 60 === 0 ? 0 : 20} y2={RULER_H}
                  stroke={s % 60 === 0 ? "rgba(255,255,255,.12)" : "rgba(255,255,255,.05)"} strokeWidth={s % 60 === 0 ? 1 : 0.5} />
                {s % 30 === 0 && (
                  <text x={sPx(s)+3} y={13} fill="rgba(255,255,255,.25)" fontSize={9} fontFamily="JetBrains Mono, monospace">
                    {`${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`}
                  </text>
                )}
              </g>
            ))}
          </svg>

          {/* Lane rows + pills */}
          {agents.map((agent, laneIdx) => {
            const color = AGENT_COLORS[laneIdx % AGENT_COLORS.length];
            const y = RULER_H + laneIdx * ROW_H;
            return (
              <div key={agent.id} className={styles.laneRow} style={{ top: y, height: ROW_H }}>
                {agent.calls.map((call) => {
                  const callSec = (new Date(call.recordedAt).getTime() - minT) / 1000;
                  const x = sPx(callSec);
                  return (
                    <CallPill key={call.traceId} call={call} x={x} width={20} color={color}
                      onClick={() => {/* open trace detail */}} />
                  );
                })}
              </div>
            );
          })}

          {/* Spawn curves SVG */}
          <svg style={{ position: "absolute", top: 0, left: 0, width: CANVAS_W, height: RULER_H + agents.length * ROW_H, pointerEvents: "none" }}>
            {agents.map((agent, childIdx) => {
              if (!agent.parentId) return null;
              const parentIdx = agents.findIndex(a => a.id === agent.parentId);
              if (parentIdx < 0) return null;
              const parentFirstCall = agents[parentIdx].calls[0];
              const childFirstCall = agent.calls[0];
              if (!parentFirstCall || !childFirstCall) return null;
              const x1 = sPx((new Date(parentFirstCall.recordedAt).getTime() - minT) / 1000) + 20;
              const y1 = RULER_H + parentIdx * ROW_H + ROW_H / 2;
              const x2 = sPx((new Date(childFirstCall.recordedAt).getTime() - minT) / 1000);
              const y2 = RULER_H + childIdx * ROW_H + ROW_H / 2;
              const color = AGENT_COLORS[childIdx % AGENT_COLORS.length];
              return (
                <path key={agent.id} fill="none"
                  stroke={color + "55"} strokeWidth={1.5} strokeDasharray="4 3"
                  d={`M ${x1} ${y1} C ${x1+(x2-x1)*0.4} ${y1}, ${x1+(x2-x1)*0.6} ${y2}, ${x2} ${y2}`} />
              );
            })}
          </svg>

          {/* Live cursor */}
          <div className={styles.liveCursor}
            style={{ left: sPx((Date.now() - minT) / 1000), height: RULER_H + agents.length * ROW_H }} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add management-plane/components/liveflow/TimelineView.tsx \
        management-plane/components/liveflow/CallPill.tsx
git commit -m "feat: implement LiveFlow timeline with swim lanes, call pills, spawn curves"
```

---

## Task 9: Agent Conversation view (`/liveflow/[agentId]`)

**Files:**
- Create: `management-plane/components/conversation/AnatomyBar.tsx`
- Create: `management-plane/components/conversation/FieldSection.tsx`
- Create: `management-plane/components/conversation/FieldItem.tsx`
- Create: `management-plane/components/conversation/CallRow.tsx`
- Create: `management-plane/app/liveflow/[agentId]/page.tsx`

Reference: `docs/ui-mocks/04-agent-conversation.html`

- [ ] **Step 1: Write `AnatomyBar.tsx`**

Renders a proportional horizontal bar for a single payload. Five segment types, widths based on `estimateTokens` per section.

```tsx
import { estimateTokens } from "@/lib/tokens";
import styles from "./AnatomyBar.module.css";

const SEGMENTS = [
  { key: "system",      label: "SYS",     color: "#2979ff" },
  { key: "tools",       label: "TOOLS",   color: "#c263ff" },
  { key: "user_msgs",   label: "USR",     color: "#00e5c0" },
  { key: "asst_msgs",   label: "ASST",    color: "#39ff14" },
  { key: "tool_results",label: "RESULTS", color: "#ffaa00" },
] as const;

interface Props {
  payload: Record<string, unknown>;
  mutatedFields?: Set<string>;
}

export function AnatomyBar({ payload, mutatedFields }: Props) {
  const msgs = (payload.messages as any[]) ?? [];
  const sections = {
    system:       estimateTokens(payload.system),
    tools:        estimateTokens(payload.tools),
    user_msgs:    estimateTokens(msgs.filter((m: any) => m.role === "user")),
    asst_msgs:    estimateTokens(msgs.filter((m: any) => m.role === "assistant")),
    tool_results: estimateTokens(msgs.flatMap((m: any) =>
      Array.isArray(m.content) ? m.content.filter((b: any) => b.type === "tool_result") : []
    )),
  };
  const total = Object.values(sections).reduce((a, b) => a + b, 0) || 1;

  return (
    <div className={styles.bar}>
      {SEGMENTS.map(seg => {
        const tok = sections[seg.key];
        if (!tok) return null;
        const pct = (tok / total) * 100;
        const mut = mutatedFields?.has(seg.key);
        return (
          <div key={seg.key} className={`${styles.seg} ${mut ? styles.mutated : ""}`}
            style={{ flex: pct, background: seg.color + "30", border: `1px solid ${seg.color}55` }}
            title={`${seg.label} · ~${tok} tokens${mut ? " · MUTATED" : ""}`}>
            {pct > 8 && <span className={styles.segLabel} style={{ color: seg.color }}>{seg.label}{mut ? " ✦" : ""}</span>}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Write `FieldItem.tsx`**

Collapsible single content block (text, tool_use, tool_result). Shows type tag, preview, token size, chevron. Expanded body shows syntax-highlighted JSON with red/green diff markers.

```tsx
"use client";
import { useState } from "react";
import styles from "./FieldItem.module.css";

interface Props {
  index: number;
  block: Record<string, unknown>;
  originalBlock?: Record<string, unknown>;
  isRemoved?: boolean;
}

export function FieldItem({ index, block, originalBlock, isRemoved }: Props) {
  const [open, setOpen] = useState(false);
  const type = (block.type as string) ?? "unknown";
  const preview = type === "text"
    ? String(block.text ?? "").slice(0, 80)
    : type === "tool_use" ? `${block.name}` : `tool_result`;

  return (
    <div className={`${styles.item} ${open ? styles.open : ""} ${isRemoved ? styles.removed : ""}`}>
      <div className={styles.header} onClick={() => setOpen(v => !v)}>
        <span className={styles.index}>[{index}]</span>
        <span className={`${styles.typeTag} ${styles[type.replace("_","")]}`}>{type}</span>
        <span className={styles.preview}>{preview}</span>
        <span className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`}>›</span>
      </div>
      {open && (
        <div className={styles.body}>
          <pre className={styles.json} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
            {formatJson(block, originalBlock, isRemoved)}
          </pre>
        </div>
      )}
    </div>
  );
}

function formatJson(current: unknown, original?: unknown, isRemoved?: boolean): React.ReactNode {
  const currentStr = JSON.stringify(current, null, 2);
  if (!original || isRemoved) {
    return <span style={{ background: isRemoved ? "rgba(255,45,85,.15)" : undefined }}>{currentStr}</span>;
  }
  const originalStr = JSON.stringify(original, null, 2);
  if (currentStr === originalStr) return currentStr;

  // Line-level diff: mark removed lines red, added lines green
  const origLines = originalStr.split("\n");
  const currLines = currentStr.split("\n");
  const maxLen = Math.max(origLines.length, currLines.length);
  const nodes: React.ReactNode[] = [];
  for (let i = 0; i < maxLen; i++) {
    const o = origLines[i];
    const c = currLines[i];
    if (o === c) {
      nodes.push(<span key={i}>{c ?? ""}{"\n"}</span>);
    } else if (c === undefined) {
      nodes.push(<span key={i} style={{ background: "rgba(255,45,85,.2)", textDecoration: "line-through" }}>{o}{"\n"}</span>);
    } else if (o === undefined) {
      nodes.push(<span key={i} style={{ background: "rgba(57,255,20,.15)" }}>{c}{"\n"}</span>);
    } else {
      nodes.push(<span key={`r${i}`} style={{ background: "rgba(255,45,85,.2)", textDecoration: "line-through" }}>{o}{"\n"}</span>);
      nodes.push(<span key={`a${i}`} style={{ background: "rgba(57,255,20,.15)" }}>{c}{"\n"}</span>);
    }
  }
  return <>{nodes}</>;
}
```

- [ ] **Step 3: Write `FieldSection.tsx`**

Collapsible section containing a list of `FieldItem`s. Shows a coloured dot, field name, item count, token estimate, mutation indicator.

- [ ] **Step 4: Write `CallRow.tsx`**

Single API call in the timeline list. Wraps FieldSections for System, Tools, Messages, Tool Results. Uses AnatomyBar. Gets `TraceDetailOut` from `api.getTrace()` when expanded.

```tsx
"use client";
import { useState } from "react";
import { NeonBadge } from "@/components/shared/NeonBadge";
import { AnatomyBar } from "./AnatomyBar";
import { FieldSection } from "./FieldSection";
import { api } from "@/lib/api";
import type { TraceOut, TraceDetailOut } from "@/lib/models";
import { estimateTokens } from "@/lib/tokens";
import styles from "./CallRow.module.css";

interface Props { trace: TraceOut; callNum: number; }

export function CallRow({ trace, callNum }: Props) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TraceDetailOut | null>(null);

  const handleToggle = async () => {
    if (!open && !detail) {
      const d = await api.getTrace(trace.trace_id);
      setDetail(d);
    }
    setOpen(v => !v);
  };

  const tokens = estimateTokens({});

  return (
    <div className={styles.row}>
      <div className={styles.timeline}>
        <div className={`${styles.dot} ${styles[trace.action.toLowerCase()]}`} />
        <div className={styles.connector} />
      </div>
      <div className={`${styles.card} ${open ? styles.expanded : ""}`}>
        <div className={styles.header} onClick={handleToggle}>
          <span className={styles.num}>#{callNum}</span>
          <NeonBadge action={trace.action} />
          <span className={styles.model}>{trace.model}</span>
          <span className={styles.ts}>{new Date(trace.recorded_at).toLocaleTimeString()}</span>
          <span className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`}>›</span>
        </div>
        {detail && <AnatomyBar payload={detail.original_payload} />}
        {open && detail && (
          <div className={styles.detail}>
            <FieldSection name="System" color="#2979ff" items={(detail.original_payload.system as any[]) ?? []} />
            <FieldSection name="Tools"  color="#c263ff" items={(detail.original_payload.tools  as any[]) ?? []} />
            <FieldSection name="Messages" color="#00e5c0"
              items={(detail.original_payload.messages as any[]) ?? []} />
            <FieldSection name="Scalars" color="#7a93a8" items={[{
              model: detail.model,
              max_tokens: (detail.original_payload as any).max_tokens,
              thinking: (detail.original_payload as any).thinking,
            }]} />
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `app/liveflow/[agentId]/page.tsx`**

```tsx
"use client";
import { useMemo, useRef } from "react";
import { useParams } from "next/navigation";
import { Topbar } from "@/components/layout/Topbar";
import { CallRow } from "@/components/conversation/CallRow";
import { useWebSocket } from "@/lib/ws";
import { HierarchyStore } from "@/lib/hierarchy";
import styles from "./page.module.css";

export default function AgentConversationPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { traces } = useWebSocket();
  const storeRef = useRef(new HierarchyStore());

  useMemo(() => { traces.forEach(t => storeRef.current.ingest(t)); }, [traces]);

  const decodedId = decodeURIComponent(agentId);
  const allAgents = storeRef.current.getSessions().flatMap(s => Array.from(s.agents.values()));
  const agent = allAgents.find(a => a.id === decodedId);

  if (!agent) {
    return (
      <>
        <Topbar title="Agent Conversation" />
        <div className={styles.empty}>
          No agent data found. <a href="/liveflow">Return to LiveFlow</a>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar title={agent.name} crumb="LiveFlow" />
      <div className={styles.timeline}>
        {agent.calls.map((call, i) => (
          <CallRow key={call.traceId} trace={{ id: call.traceId, trace_id: call.traceId,
            recording_session_id: agent.sessionId, model: agent.model,
            action: call.action as any, recorded_at: call.recordedAt, session_id: agent.sessionId }}
            callNum={agent.calls.length - i} />
        ))}
      </div>
    </>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add management-plane/components/conversation/ \
        management-plane/app/liveflow/
git commit -m "feat: implement Agent Conversation view with call timeline, anatomy bar, field accordion"
```

---

## Task 10: Recordings page, docker-compose, final wiring

**Files:**
- Create: `management-plane/app/recordings/page.tsx`
- Create: `management-plane/app/traces/[traceId]/page.tsx`
- Create: `management-plane/app/rules/page.tsx`
- Create: `management-plane/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write `app/recordings/page.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Topbar } from "@/components/layout/Topbar";
import { api } from "@/lib/api";
import type { RecordingListItem } from "@/lib/models";
import styles from "./page.module.css";

export default function RecordingsPage() {
  const [recordings, setRecordings] = useState<RecordingListItem[]>([]);
  const router = useRouter();

  useEffect(() => { api.listRecordings().then(setRecordings); }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this recording and all its traces?")) return;
    await api.deleteRecording(id);
    setRecordings(prev => prev.filter(r => r.id !== id));
  };

  return (
    <>
      <Topbar title="Recordings" />
      <main className={styles.content}>
        <table className={styles.table}>
          <thead><tr><th>Session ID</th><th>Started</th><th>Duration</th><th>Traces</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {recordings.map(r => (
              <tr key={r.id} onClick={() => router.push(`/liveflow?session=${r.id}&view=timeline`)} className={styles.row}>
                <td className="neon-cyan" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{r.id.slice(0, 8)}…</td>
                <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{new Date(r.started_at).toLocaleString()}</td>
                <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {r.stopped_at
                    ? `${Math.round((new Date(r.stopped_at).getTime() - new Date(r.started_at).getTime()) / 1000)}s`
                    : "—"}
                </td>
                <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{r.trace_count}</td>
                <td>{r.is_active
                  ? <span className="neon-red" style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>● LIVE</span>
                  : <span style={{ color: "var(--text-dim)", fontFamily: "var(--font-mono)", fontSize: 10 }}>Stopped</span>}
                </td>
                <td onClick={e => { e.stopPropagation(); handleDelete(r.id); }} className={styles.deleteBtn}>✕</td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </>
  );
}
```

- [ ] **Step 2: Write `app/traces/[traceId]/page.tsx`** (full-page trace detail)

Full-page version of TraceDetailSlideOver — fetches `api.getTrace(traceId)` on mount and renders the same tabbed layout (Summary, Scalars, Tools, System, Messages, Metadata) but full-width without the overlay or close button.

```tsx
"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Topbar } from "@/components/layout/Topbar";
import { api } from "@/lib/api";
import type { TraceDetailOut } from "@/lib/models";
import { NeonBadge } from "@/components/shared/NeonBadge";
import { estimateTokens } from "@/lib/tokens";

export default function TraceDetailPage() {
  const { traceId } = useParams<{ traceId: string }>();
  const [trace, setTrace] = useState<TraceDetailOut | null>(null);

  useEffect(() => {
    api.getTrace(traceId).then(setTrace).catch(console.error);
  }, [traceId]);

  if (!trace) return <><Topbar title="Trace Detail" /><div style={{ padding: 32, fontFamily: "var(--font-mono)" }}>Loading…</div></>;

  const origTokens = estimateTokens(trace.original_payload);
  const finalTokens = estimateTokens(trace.final_payload);
  const saved = origTokens - finalTokens;

  return (
    <>
      <Topbar title="Trace Detail" crumb="Traces" />
      <main style={{ padding: 24 }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <NeonBadge action={trace.action} />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{trace.model}</span>
          {saved > 0 && <span style={{ color: "var(--neon-green)", fontFamily: "var(--font-mono)", fontSize: 11 }}>−{Math.round(saved/1000)}k saved</span>}
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, opacity: 0.5, marginBottom: 16 }}>
          {trace.trace_id} · {new Date(trace.recorded_at).toLocaleString()}
        </div>
        {trace.mutation_steps.map((step, i) => (
          <div key={step.rule_id} style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.4 }}>{i + 1}</span>
            <span>{step.rule_name}</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.5 }}>priority {step.priority}</span>
          </div>
        ))}
      </main>
    </>
  );
}
```

- [ ] **Step 2b: Write `app/rules/page.tsx`** (read-only rules list)

```tsx
"use client";
import { useEffect, useState } from "react";
import { Topbar } from "@/components/layout/Topbar";
import { api } from "@/lib/api";
import type { RuleOut } from "@/lib/models";

export default function RulesPage() {
  const [rules, setRules] = useState<RuleOut[]>([]);
  useEffect(() => { api.listRules().then(setRules).catch(console.error); }, []);

  return (
    <>
      <Topbar title="Rules" />
      <main style={{ padding: 24 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: 11 }}>
          <thead>
            <tr>{["Priority", "Name", "Active", "Match Logic", "Mutate Logic"].map(h =>
              <th key={h} style={{ textAlign: "left", padding: "4px 8px", opacity: 0.4 }}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {rules.map(r => (
              <tr key={r.id} style={{ borderTop: "1px solid rgba(255,255,255,.04)" }}>
                <td style={{ padding: "6px 8px" }}>{r.priority}</td>
                <td style={{ padding: "6px 8px", color: "var(--neon-cyan)" }}>{r.name}</td>
                <td style={{ padding: "6px 8px" }}>{r.is_active ? "✓" : "—"}</td>
                <td style={{ padding: "6px 8px", opacity: 0.6 }}>{JSON.stringify(r.match_logic)}</td>
                <td style={{ padding: "6px 8px", opacity: 0.6 }}>{JSON.stringify(r.mutate_logic)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </>
  );
}
```

- [ ] **Step 2c: Write `management-plane/Dockerfile`**

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
COPY --from=builder /app/.next/standalone .
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 3: Add management-plane to `docker-compose.yml`**

In `docker-compose.yml`, add under `services:`:

```yaml
  management-plane:
    build:
      context: ./management-plane
    container_name: governor-ui
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://control-plane:8080
      - NEXT_PUBLIC_WS_URL=ws://control-plane:8080
    depends_on:
      - control-plane
```

Add under `volumes:` — nothing new needed.

- [ ] **Step 4: Ensure `management-plane/next.config.ts` sets output standalone**

```ts
import type { NextConfig } from "next";
const config: NextConfig = { output: "standalone" };
export default config;
```

- [ ] **Step 5: Final build check**

```bash
cd management-plane && npm run build
```
Expected: Clean build, no TypeScript errors.

- [ ] **Step 6: Run all Control Plane tests**

```bash
cd ../control-plane && python -m pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 7: Final commit**

```bash
cd ..
git add management-plane/app/recordings/ \
        management-plane/app/traces/ \
        management-plane/next.config.ts \
        docker-compose.yml
git commit -m "feat: add Recordings page, Trace detail page, docker-compose management-plane service"
```

---

## Definition of Done

- [ ] `python -m pytest control-plane/tests/ -v` — all tests green (including new WS and session_id tests)
- [ ] `cd management-plane && npm run build` — no TypeScript or build errors
- [ ] `cd management-plane && npx jest` — tokens tests pass
- [ ] `docker compose up --build` starts all 6 services cleanly
- [ ] `curl http://localhost:8080/health` returns `{"status":"ok","components":{"db":"ok","redis":"ok"}}`
- [ ] WebSocket connects on `ws://localhost:8080/ws/live`; status pings arrive every ~3s with `session_started_at` and `elapsed_seconds` fields
- [ ] WS status event field `is_recording` matches actual recording state; Recording button reflects this on page load
- [ ] With 0 traces, Live View shows "No traces received yet" empty state and stat cards show 0
- [ ] With a recording active and traffic flowing, Live View shows traces in real time; new rows flash cyan
- [ ] "~Est. Saved" stat card shows a non-zero value for mutated traces
- [ ] Clicking a trace row opens the slide-over with action pill, model, mutation steps, and token impact bar
- [ ] LiveFlow Hierarchy shows agent nodes; with no data, shows empty state placeholder
- [ ] LiveFlow Timeline shows swim lanes with call pills positioned by time; clicking a pill opens slide-over
- [ ] Clicking a lane label in Timeline navigates to Agent Conversation view
- [ ] Navigating directly to `/liveflow/[agentId]` with no session data shows "Agent data not available" empty state
- [ ] Recordings page lists all sessions with delete functionality (confirm dialog shown before delete)
- [ ] Rules page shows a read-only table of all active rules
- [ ] `/traces/[traceId]` page fetches and displays the trace detail in full-page layout
- [ ] `session_id` column exists in the `traces` table in PostgreSQL (`alembic upgrade head` succeeds)