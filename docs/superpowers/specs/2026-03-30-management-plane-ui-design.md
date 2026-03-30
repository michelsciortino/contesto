# Management Plane UI — Design Spec
_Date: 2026-03-30_

---

## 1. Scope

This spec covers the first iteration of the Governor Management Plane: a Next.js + Blueprint.js web application that connects to the Control Plane WebSocket and REST API to visualise agent traffic in real time, navigate recording sessions, and inspect individual traces.

**Deliverables:**
- Next.js 14 application scaffolded in `management-plane/`
- Five views: Live View, LiveFlow (Hierarchy), LiveFlow (Timeline), Agent Conversation, Trace Detail
- WebSocket client that receives live trace events and status pings from `ws://<host>:8080/ws/live`
- REST client wrapping the existing Control Plane API (`/recording`, `/recordings`, `/traces`, `/rules`, `/health`)
- Neon-on-dark aesthetic built with Blueprint.js dark theme + custom CSS overrides

**Not in scope:**
- Rules Manager UI (future)
- Replay Sandbox (future)
- Hold / manual intervention controls (future)
- Authentication / multi-user

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Framework | Next.js 14 (App Router) | |
| UI component base | Blueprint.js 5 (`@blueprintjs/core`, `@blueprintjs/table`) | Dark theme |
| Graph / flow | ReactFlow 12 | LiveFlow hierarchy |
| Styling | CSS Modules + CSS custom properties | Neon overrides on top of Blueprint dark |
| WebSocket | Native browser `WebSocket` in a React context provider | |
| HTTP client | `fetch` (no extra library) | |
| Fonts | JetBrains Mono (mono), Space Grotesk (UI) | Google Fonts |
| State | React `useState` / `useReducer` + React Context | No external store needed |

---

## 3. Project Layout

```
management-plane/
├── app/
│   ├── layout.tsx              # Root layout: sidebar + font imports
│   ├── page.tsx                # Redirect → /live
│   ├── live/page.tsx           # Live View dashboard
│   ├── liveflow/page.tsx       # LiveFlow (Hierarchy / Timeline tabs)
│   ├── liveflow/[agentId]/page.tsx  # Agent Conversation view
│   ├── recordings/page.tsx     # Historical recordings list
│   ├── traces/[traceId]/page.tsx    # Trace Detail (also accessible as modal)
│   └── rules/page.tsx          # Rules list (read-only in this iteration)
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx
│   │   └── Topbar.tsx
│   ├── live/
│   │   ├── StatCards.tsx
│   │   ├── TraceTable.tsx
│   │   └── SessionsPanel.tsx
│   ├── liveflow/
│   │   ├── HierarchyGraph.tsx      # ReactFlow canvas
│   │   ├── AgentNode.tsx           # ReactFlow custom node
│   │   ├── TimelineView.tsx        # Swim-lane SVG timeline
│   │   ├── TimelineLane.tsx
│   │   └── CallPill.tsx
│   ├── conversation/
│   │   ├── CallRow.tsx             # Single API call in the timeline list
│   │   ├── AnatomyBar.tsx          # Coloured context segments bar
│   │   ├── FieldSection.tsx        # Accordion section (System, Tools, etc.)
│   │   └── FieldItem.tsx           # Individual block with expand/collapse
│   └── shared/
│       ├── NeonBadge.tsx
│       ├── TokenBar.tsx
│       └── TraceDetailSlideOver.tsx
├── lib/
│   ├── ws.tsx                  # WebSocketProvider + useWebSocket hook
│   ├── api.ts                  # Typed fetch wrappers for all REST endpoints
│   ├── models.ts               # TypeScript interfaces matching Control Plane schemas
│   └── tokens.ts               # Context window sizes per model
├── styles/
│   ├── globals.css             # CSS variables, scanline overlay, Blueprint overrides
│   └── neon.css                # Neon glow utilities (.neon-cyan, .neon-amber, etc.)
├── Dockerfile
├── next.config.ts
└── package.json
```

---

## 4. WebSocket Protocol

The Control Plane exposes `ws://<host>:8080/ws/live`.

The server sends two event types as newline-delimited JSON:

### `trace` event — emitted after every gRPC call
```json
{
  "type": "trace",
  "data": {
    "trace_id": "uuid",
    "session_id": "uuid",
    "model": "claude-sonnet-4-6",
    "action": "PROCEED | MUTATED | REJECT",
    "original_payload": { ... },
    "final_payload": { ... },
    "mutation_steps": [ { "rule_id": "...", "rule_name": "...", "priority": 10, "payload_after": {...} } ],
    "recorded_at": "ISO8601"
  }
}
```

### `status` ping — emitted every 3 seconds
```json
{
  "type": "status",
  "data": {
    "is_recording": true,
    "session_id": "uuid | null",
    "session_started_at": "ISO8601 | null",
    "elapsed_seconds": 227,
    "db": "ok | error",
    "redis": "ok | error"
  }
}
```

**Connection lifecycle:**
- On mount: connect, send no handshake (server accepts immediately). Guard against React StrictMode double-mount: store the socket in a `useRef` and close the previous socket before opening a new one.
- On `trace` event: append to local trace ring buffer (max 200 **trace** events — status pings are not counted). Buffer persists across reconnects; it is never reset on disconnect.
- On `status` event: update recording state and health indicators in a separate atom. Initialize recording button state from the first `status` event received after connect.
- On disconnect: show "WS Disconnected" pill, retry with exponential backoff (1s → 2s → 4s → max 30s)
- Stat card totals (Intercepted, Mutated, Tokens Saved, Rejected) are derived from the buffer contents, not accumulated from events, so counts are consistent after reconnect.

---

## 5. Agent Hierarchy Extraction

Since the payload contains no explicit parent ID, hierarchy is inferred at the frontend from two signals present in `original_payload`:

1. **`metadata.user_id.session_id`** — stable UUID across all calls within one Claude Code session. All traces sharing the same `session_id` belong to the same conversation tree.

2. **`messages[]` tool_use blocks with `name == "Agent"`** — when a message contains a `tool_use` block calling the `Agent` tool, that call spawned a child agent. The `input.prompt` value is used to label the child. The `tool_use.id` (e.g. `toolu_01XYZ`) links the parent call to the child's first request.

**Algorithm (runs client-side on each new trace):**
```
for each incoming trace T:
  group = groups[T.session_id] ?? new AgentGroup()
  agent = group.agents[T.model + T.trace_id_prefix] ?? inferAgentFromMessages(T)
  agent.calls.push(T)
  for each tool_use block in T where name == "Agent":
    record spawn(parent=agent, childPrompt=tool_use.input.prompt, at=T.recorded_at)
```

**Agent identity heuristic:** A trace starts a new agent invocation if its `messages` array contains **no assistant-role messages**. Continuation traces (same agent, later turns) will have at least one `role: "assistant"` message. The agent key is `{model}-{session_id}-{first_trace_id_prefix}` for new invocations, and `{model}-{session_id}-cont` for continuations; all continuation traces within a session are grouped under the same agent.

**AgentNode variant rules:**
- `orchestrator` — the root node of a session (no `parentId`)
- `leaf` — spawned by a parent but has zero calls of its own yet
- `subagent` — spawned by a parent and has at least one call

---

## 6. Views

### 6.1 Live View (`/live`)

**Layout:** Full-width dashboard.

**Components:**
- **Topbar:** "Governor › Live View" breadcrumb, WS status pill (green pulsing dot when connected), Recording button (inactive = "▶ Start Recording" / active = "⬛ Stop · MM:SS" pulsing red).
- **Stat cards (4):** Intercepted, Mutated, Tokens Saved, Rejected. All scoped to the ring buffer contents. Tokens Saved is computed client-side using the §8 character/4 heuristic: `sum(estimateTokens(original_payload) - estimateTokens(final_payload))` for traces where `action == "MUTATED"`. The stat label reads "~Est. Saved" to signal approximation.
- **Empty state:** Before any traces arrive, the table shows a single row with "No traces received yet". Stat cards show 0.
- **Trace table:** Blueprint `HTMLTable`, columns: Trace ID (truncated, cyan, clickable), Action badge, Model tag, Context Weight bar (fill colour green/amber/red based on % of model context window; max from `lib/tokens.ts`), Rules Matched chips, Time. Rows flash cyan on arrival (single CSS animation, ~600ms, plays once and stops). Capped at 50 rows, newest first.
- **Sessions panel (right):** List of recording sessions from `/recordings`. Active session shown first with pulsing REC badge. Clicking a session navigates to `/liveflow?session=<session_id>&view=timeline`. This panel uses the same `RecordingListItem` model and the same data-fetch logic as the Recordings List page (§6.5); the `SessionsPanel` component lives in `components/live/` but can be reused or import shared logic.

**Interactions:**
- Clicking a trace row opens `TraceDetailSlideOver` (see §6.6).
- Start/Stop recording calls `POST /recording/start` or `POST /recording/stop`.

---

### 6.2 LiveFlow — Hierarchy tab (`/liveflow`)

**Layout:** Full-canvas ReactFlow graph with a fixed right detail panel.

**Components:**
- **Toolbar:** Hierarchy / Timeline tabs, Session selector dropdown, Filter by model, Filter by action, Auto-layout button.
- **ReactFlow canvas:**
  - One custom `AgentNode` per inferred agent. Variants: `orchestrator` (cyan), `subagent` (purple), `leaf` (blue).
  - Node body: type label, agent name, model, token weight bar, call count, last-action status dot.
  - Active call: node pulses with neon glow (animation).
  - Edges: dashed animated bezier from parent node to child node, with a moving particle.
  - Session boundary: dashed rounded rectangle grouping all nodes sharing a `session_id`. Past sessions rendered at 45% opacity.
  - Pointer events on session boundary rectangle: **none** (never intercepts node clicks).
- **Detail panel (right drawer, 320px):** Opens on node click. Shows: agent type/name/model, stats grid (calls, mutated, tokens saved, spawned), context window bar, spawn chain tree, list of 4 most recent traces with action badge + timestamp + link to full trace.
- **Minimap** (bottom-right, Blueprint `Minimap`-style).
- **Zoom controls** (bottom-centre).
- **Empty state:** When no session is active and the ring buffer is empty, the canvas shows a centred placeholder: "No agent data. Start a recording session to see live traffic."

---

### 6.3 LiveFlow — Timeline tab (`/liveflow?view=timeline`)

**Layout:** Left lane labels (140px fixed) + horizontally scrollable SVG canvas.

**Components:**
- **Lane labels:** One row per agent. Coloured dot, name, model, depth badge. Clicking navigates to the Agent Conversation view for that agent.
- **Time ruler:** SVG, major ticks at 60s, minor at 15s, labels at 30s/60s.
- **Call pills:** HTML `<div>` absolutely positioned within a horizontally scrollable container of fixed width `CANVAS_W = 1400px`. `x = (call_time_ms - session_start_ms) / total_duration_ms * CANVAS_W`, where `total_duration_ms = max(session_end_ms - session_start_ms, 60_000)` (minimum 60s so short sessions are still navigable). Each pill has a minimum rendered width of 12px so even sub-second calls are clickable. Wide pills (> 40px) show `#N ✦ 18k`; narrow show only the status mark dot. Clicking a pill opens `TraceDetailSlideOver` for that trace.
- **Spawn curves:** SVG bezier from `(t1_parent, y_parent)` to `(t0_child, y_child)` with dashed stroke in the child lane's colour. Animated particle on session load.
- **Live cursor:** Vertical cyan line at current time. Canvas auto-scrolls on mount to show the live cursor at 70% from the left edge.
- **Tooltip on hover:** Agent name, call number, action badge, token count, start time, duration, spawned agent (if applicable).

**Pill colour coding:**
- Background: lane colour at 10% opacity
- Border: lane colour at 33%
- Amber dot overlay = mutated
- Red dot overlay = rejected
- Cyan pulse animation = currently in-flight

---

### 6.4 Agent Conversation (`/liveflow/[agentId]`)

**Route parameter:** `agentId` is a client-side generated identifier (e.g. `main-agent-d024e4be`) derived from agent name + session prefix. It is not persisted. On page refresh or direct navigation, the WebSocket ring buffer will be empty and the agent will not be found. In this case the page shows a centered empty state: "Agent data not available — this view requires an active session. [Return to LiveFlow]" with no auto-redirect.

**Layout:** Topbar with back button → LiveFlow, agent name pill, then a vertically scrolling timeline of call rows.

**Sub-toolbar:** Filter tabs (All / Mutated / Proceed / Rejected), sort direction, Expand All button, segment legend.

**Call row structure (three nesting levels):**

**Level 1 — Call card** (always visible, click to expand):
- Left: vertical timeline line with coloured dot (cyan=live, amber=mutated, green=proceed, red=reject) and a connector line to the next row.
- Card header: `#N`, action badge, model tag, timestamp, token count (with savings in green if mutated), chevron.
- **Anatomy bar:** Horizontal proportional bar subdivided into coloured segments, one per field group present in the payload. Segment widths are proportional to estimated token count. Segments: `system` (blue `#2979ff`), `tools` (purple `#c263ff`), `user messages` (teal `#00e5c0`), `assistant messages` (green `#39ff14`), `tool results` (amber `#ffaa00`). Mutated segments show a `✦` label. Hovering a segment shows a tooltip with the field name and token count.

**Level 2 — Field sections** (visible when card is expanded):
- One collapsible accordion section per top-level payload field: System, Tools, Messages (split by role), Tool Results, Scalars (model, max_tokens, thinking, output_config).
- Section header: coloured dot, field name, item count badge, token count, mutation indicator if this field was touched, chevron.

**Level 3 — Individual items** (visible when section is expanded):
- One card per content block.
- Item header: index `[N]`, type tag (`text` / `tool_use` / `tool_result`), preview (first 80 chars), token size, chevron.
- Expanded item body: JSON-highlighted raw content. Removed content shown with red background + strikethrough. Added/replacement content shown with green background. Colour coding: keys in cyan, string values in teal, numbers in amber.

**New traces appended live** to the top of the list while the recording session is active.

---

### 6.5 Recordings List (`/recordings`)

**Layout:** Full-width page, similar structure to Live View but for historical sessions.

**Components:**
- **Session table:** Blueprint `HTMLTable`. Columns: Session ID (truncated, cyan), Started At, Duration, Trace Count, Status (active pulsing / stopped). Rows are clickable.
- Clicking a session row navigates to `/liveflow?session=<session_id>` (Timeline view pre-filtered to that session).
- Delete button per row calls `DELETE /recordings/{session_id}` with a confirmation dialog.

---

### 6.6 Trace Detail Slide-Over

**Trigger:** Clicking a trace row in the Live View table, or the "↗ Open trace" link in any other view.

**Layout:** 620px right drawer with animation. Header has "↗ Open in tab" button (navigates to `/traces/[traceId]` in a new tab) and close button.

**Header:** Action pill, model pill, session pill, token savings pill, full trace ID and timestamp.

**Tabs:** Summary · Scalars · Tools · System · Messages · Metadata. Tabs that contain changed fields show an amber "N changed" badge.

**Summary tab:** Token impact bar (original vs final side-by-side with percentage saved), mutation pipeline steps (ordered list: step number, rule name, operator + fields affected, tokens removed, priority badge).

**Other tabs:** Follow the same three-level accordion pattern as Agent Conversation (§6.4 Level 2 and 3), but scoped to a single trace's `original_payload` vs `final_payload` diff.

---

## 7. Neon Aesthetic

**Base:** Blueprint.js `Classes.DARK` applied to `<body>`.

**Background:** `#070a0f` (deeper than Blueprint's default dark).

**CSS custom properties** (defined in `globals.css`):
```css
--neon-cyan:   #00f5ff;
--neon-green:  #39ff14;
--neon-amber:  #ffaa00;
--neon-red:    #ff2d55;
--neon-purple: #c263ff;
--neon-blue:   #4d9fff;
--neon-teal:   #00e5c0;
```

**Neon text glow utility:**
```css
.neon-cyan  { color: var(--neon-cyan);   text-shadow: 0 0 18px rgba(0,245,255,.5); }
.neon-amber { color: var(--neon-amber);  text-shadow: 0 0 18px rgba(255,170,0,.5); }
/* etc. */
```

**Scanline overlay:** Fixed `::after` pseudo-element on `<body>` with a repeating 4px linear gradient at 2% opacity — non-interactive.

**Ambient glows:** Radial gradient `<div>` elements placed absolutely at canvas corners — pointer-events none, zero z-index.

**Fonts:** JetBrains Mono for all monospaced content (IDs, numbers, JSON, model names, timestamps), Space Grotesk for UI labels and navigation.

**Blueprint overrides** (in `neon.css`):
- `.bp5-card` background → `#131b24`
- `.bp5-button` focused state border → neon-cyan
- Blueprint intent colours remapped to neon palette

---

## 8. Context Window Sizes (`lib/tokens.ts`)

Used to compute the % fill of the token weight bar:

```ts
export const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  'claude-opus-4':      200_000,
  'claude-sonnet-4-6':  200_000,
  'claude-haiku-4-5':   200_000,
};
export function getContextWindow(model: string): number {
  return MODEL_CONTEXT_WINDOWS[model] ?? 200_000;
}
```

Token count is estimated from the payload by counting characters and dividing by 4 (rough approximation). Actual token counts are not returned by the API.

---

## 9. REST API Client (`lib/api.ts`)

Typed wrappers for all existing Control Plane endpoints. Base URL read from `NEXT_PUBLIC_API_URL` env variable (default: `http://localhost:8080`). TypeScript interfaces for all response types are defined in `lib/models.ts`.

```ts
// Recording
startRecording(): Promise<{ session_id: string; started_at: string }>
stopRecording(): Promise<{ session_id: string; traces_flushed: number; stopped_at: string }>
getRecordingStatus(): Promise<{ is_active: boolean; session_id: string | null; started_at: string | null }>

// Recordings — returns RecordingListItem[]
// RecordingListItem: { id, started_at, stopped_at: string|null, is_active, trace_count }
listRecordings(): Promise<RecordingListItem[]>
deleteRecording(sessionId: string): Promise<void>

// Traces — TraceOut is the list shape; TraceDetailOut extends it with payloads
// TraceOut: { id, trace_id, recording_session_id, model, action, recorded_at, session_id? }
// TraceDetailOut: TraceOut & { original_payload, final_payload, mutation_steps }
listTraces(params: { session_id?: string; page?: number }): Promise<TraceOut[]>
getTrace(traceId: string): Promise<TraceDetailOut>

// Rules — RuleOut: { id, name, priority, is_active, match_logic, mutate_logic, created_at, updated_at }
listRules(): Promise<RuleOut[]>

// Health
getHealth(): Promise<{ status: string; components: { db: string; redis: string } }>
```

All data fetching is client-side (`"use client"` components with `useEffect`). Server Components are not used for data fetching in this iteration.

---

## 10. Docker + docker-compose

**`management-plane/Dockerfile`:**
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
CMD ["node", "server.js"]
```

**`docker-compose.yml` addition:**
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

> **Note:** Use the Docker Compose service name `control-plane` (not `localhost`) so the container can resolve the address. `NEXT_PUBLIC_WS_URL` is consumed by `lib/ws.tsx` for the WebSocket connection URL.

---

## 11. Control Plane Changes Required

This UI requires two additions to the Control Plane (not in the current codebase):

### 11.1 WebSocket endpoint (`/ws/live`)

Add to `app/routers/` a new `ws.py` router:
- FastAPI `WebSocket` endpoint at `/ws/live`
- A `ConnectionManager` singleton holds a `set` of active WebSocket connections
- `GovernorServicer` enqueues trace events into an `asyncio.Queue` (module-level in `ws.py`). The `_status_ping_loop` background task also drains this queue and broadcasts events. This avoids the cross-thread `asyncio.create_task` issue that would occur if the gRPC servicer (which may run in a thread pool) called async code directly.
- A background task emits `status_event` every 3 seconds to all connected clients. The status event includes `session_started_at` (looked up from Redis/DB when `is_recording=True`) and `elapsed_seconds` (computed as `(now - session_started_at).total_seconds()`).
- Health checks (`SELECT 1` and `redis.ping()`) in the ping loop are gated on `len(self._connections) > 0` to avoid unnecessary load when no clients are connected.
- On connection, immediately send the current recording status as a `status` event. No auth required in this iteration.

### 11.2 `session_id` field on `Trace` model

Extract `session_id` from `metadata.user_id` JSON in the gRPC servicer and store it as a dedicated column on the `Trace` model (currently the field does not exist). This enables grouping traces by conversation in the frontend without re-parsing the payload.

---

## 12. Out of Scope

- Rules Manager (create/edit/delete rules via UI)
- Replay Sandbox
- Hold / manual intervention
- Agent Hierarchy Graph in Trace Detail (ReactFlow within a slide-over)
- Real-time token counting (character-based approximation used instead)
- Authentication
