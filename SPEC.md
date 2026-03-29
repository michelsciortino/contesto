---

## GOVERNOR: Technical Specification

### 1. The 3-Plane Architecture

#### **Data Plane (The Interceptor Hook)**
* **Runtime:** Python (Custom Hook in LiteLLM Proxy).
* **Communication:** gRPC Client (Unary Call).
* **Responsibility:** * Intercept the `data` payload using `async_pre_call_hook`.
    * Serialize the payload into a Protobuf message.
    * Send to the Control Plane and `await` the mutated response.
    * **Performance Target:** < 5ms serialization overhead.

#### **Control Plane (The Brain/Rules Engine)**
* **Runtime:** Python (FastAPI with gRPC support).
* **Communication:** gRPC Server (Incoming from Hook) + REST/WebSocket Server (Outgoing to UI).
* **Responsibility:**
    * **The Matcher:** Compare the request against a "Live Rule Tree" (e.g., *Is model == claude-3? Is tool == Notebook?*).
    * **The Mutator:** Execute the scrubbing logic (stripping system-reminders, truncating git diffs).
    * **The Orchestrator:** Manage the "Hold" state for manual interventions from the Management Plane.
    * **The Recorder:** Async write of "Original vs. Mutated" states to PostgreSQL.

#### **Management Plane (Palantir-Style UI)**
* **Runtime:** Next.js + Blueprint.js (Design System).
* **Responsibility:**
    * **Live View:** Real-time visualization of "Flying" contexts using a Blueprint-based data-dense table.
    * **The Trace Tree:** Using **React Flow** to show the hierarchical "Agent spawning Agent" recursion.
    * **Rule Designer:** A GUI to build mutation rules without writing code (e.g., a "Scrubber" node that takes a Regex).
    * **Replay Sandbox:** A tool to "re-fire" historical traces through different mutation rules to see token savings.

---

### 2. Communication Protocol (The gRPC Contract)

Using gRPC ensures that if Claude Code updates its JSON schema, your system won't just "break"—it will fail validation at the Protobuf layer.

**Proposed `interceptor.proto`:**

```protobuf
syntax = "proto3";

service ContextService {
  rpc MutateContext (ContextRequest) returns (ContextResponse);
}

message ContextRequest {
  string trace_id = 1;
  string model = 2;
  string raw_json_payload = 3; // The full LiteLLM 'data' object
  map<string, string> metadata = 4;
}

message ContextResponse {
  enum Action {
    PROCEED = 0;
    MUTATED = 1;
    REJECT = 2;
  }
  Action action = 1;
  string modified_json_payload = 2;
}
```

---

### 3. Deployment Strategies

#### **Local Deployment (The "Personal Sandbox")**

Hosted via `docker-compose` on a developer's machine.

- **`litellm`**: Custom image (LiteLLM + `grpcio` library).
- **`governor-control`**: The gRPC/FastAPI logic.
- **`governor-ui`**: The Blueprint.js frontend.
- **`redis`**: For fast ephemeral "Hold" states.
- **`postgres`**: For trace history.

#### **Enterprise Deployment (The "Infrastructure" Scale)**

Modularized for high availability and zero-latency impact.

- **LiteLLM Pool:** Hosted on **AWS ECS** (Fargate/EC2) with an ALB.
- **Control Plane:** A separate **ECS Service** optimized for high-concurrency gRPC.
- **Database:** **RDS Aurora (PostgreSQL)** for persistent traces.
- **Cache:** **ElastiCache (Redis)** to sync "Manual Intervention" states across multiple Control Plane nodes.
- **Frontend:** Static build hosted on **S3 + CloudFront**, communicating with the Control Plane via a dedicated API Gateway.

---

### 4. The UI Aesthetic: "The Governor Monitor"

Following the aesthetic, your frontend will focus on **Information Density over White Space**.

1.  **The "Flight Deck":** A real-time log where each row is a flying request. A "Weight" bar shows the token count relative to the model's context window.
2.  **The "Context Scrubber":** A side-by-side diff view.
    - _Left:_ The raw 32k token mess.
    - _Right:_ The 12k token "cleaned" version.
    - _Highlight:_ Your `<system-reminder>` blocks are highlighted in red (deleted) or yellow (compressed).
3.  **The "Agent Hierarchy Graph":** A node-based map showing how your main Claude agent is delegating tasks to sub-agents. You can click any node to see that specific agent's private context.

---

### 5. Final Confirmation on LiteLLM Mutation

**Yes, I am 100% sure.** In the `async_pre_call_hook`, if you return a `dict`, LiteLLM replaces its internal `data` object with yours.

**One Detail:** Since you are using Docker, you will need to create a simple `Dockerfile` for LiteLLM to include the `grpcio` library so your hook can talk to your Control Plane:

```dockerfile
FROM ghcr.io/berriai/litellm:main-latest
RUN pip install grpcio grpcio-tools httpx
COPY ./custom_hooks.py /app/custom_hooks.py
# Your proto generated files go here too
```
