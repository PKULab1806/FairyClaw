# Module Layout

This document maps each directory to its responsibility and key files.

---

## Top-level

| Path | Description |
|---|---|
| `main.py` | Process entry point. Calls `uvicorn.run("fairyclaw.main:app", ...)`. |
| `fairyclaw/` | The Python package — all runtime code. |
| `config/` | Configuration files: `fairyclaw.env` (gitignored), `fairyclaw.env.example`, `llm_endpoints.yaml` (gitignored), `llm_endpoints.yaml.example`. |
| `tests/` | pytest test suite. |
| `scripts/` | One-off developer scripts (not production). |
| `web/` | React/TypeScript SPA served by the Gateway at `/app`. |
| `deploy/` | Dockerfile and docker-compose.yml. |
| `docs/` | Supplementary documentation. |

---

## `fairyclaw/` package

```
fairyclaw/
├── main.py                  ← Business process: FastAPI app, startup/shutdown
├── config/
│   └── settings.py          ← Process-level configuration (FAIRYCLAW_* prefix); capability-specific params live in group config snapshots via fairyclaw.sdk.group_runtime
├── api/                     ← Business HTTP API (routers, schemas, dependencies)
├── bridge/                  ← WebSocket bridge (Business side)
│   ├── user_gateway.py      ← UserGateway: bridge WS + user inbound/outbound
│   └── ws_server.py         ← Re-exports `UserGateway` / `create_ws_bridge_router`
├── core/                    ← Framework core — stable, minimal surface
│   ├── domain.py            ← ContentSegment, SegmentType (shared value types)
│   ├── events/              ← Event bus and session scheduler
│   ├── gateway_protocol/    ← Bridge envelope models and inbound service
│   ├── capabilities/        ← Capability registry and dynamic loader
│   └── agent/               ← Planner, context, hooks, session (see below)
├── gateway/                 ← Gateway process
│   ├── main.py              ← Gateway FastAPI app + startup
│   ├── runtime.py           ← GatewayRuntime: adapters + bridge client
│   ├── adapters/            ← HTTP and OneBot adapters
│   │   ├── web_gateway_adapter.py
│   │   └── onebot_adapter.py
│   └── bridge/              ← WS bridge client (Gateway side)
├── sdk/                     ← Stable import surface for capability scripts (see below)
├── capabilities/            ← Pluggable capability groups (extend here)
├── infrastructure/          ← DB, LLM client, embedding, files, tokenizer
└── tools/                   ← Internal tool dispatch helpers
```

---

## `fairyclaw/sdk/`

The stable public import surface for capability group scripts.  Capability scripts should import from here rather than directly from `fairyclaw.core`.

| Module | Responsibility |
|---|---|
| `sdk.tools` | `ToolContext` (with `group_runtime_config` and `filesystem_root_dir`), `resolve_safe_path`, `get_context_db`, result/listing types. |
| `sdk.ir` | `SessionMessageBlock`, `ToolCallRound`, `ChatHistoryItem`, `SessionMessageRole`, `UserTurn` — typed history IR. |
| `sdk.hooks` | All hook boundary types: stage payloads, `HookStageInput/Output`, `HookStatus`, `EventHookHandler`. |
| `sdk.runtime` | **Semantic API A** — `publish_user_message_received`, `request_planner_wakeup`; `publish_runtime_event` escape hatch; `deliver_file_to_user`. |
| `sdk.subtasks` | **Semantic API B** — `bind_sub_session`, `get_or_create_subtask_state`, `is_sub_session_cancel_requested`, `request_cancel_subtask`, `clear_sub_session_cancel`. |
| `sdk.events` | `EventType`, `WakeupReason`, typed event payload contracts (`FileUploadReceivedEventPayload`, etc.). |
| `sdk.types` | `ContentSegment`, `SegmentType`, `SystemPromptPart`, `SUB_SESSION_MARKER`, `TaskType`. |
| `sdk.group_runtime` | `load_group_runtime_config` — unified group config loader (env vars + optional YAML → frozen snapshot); `expect_group_config` — typed retrieval from `ToolContext`. |

**Dependency direction**: `sdk.*` → `core.*`.  `sdk` never imports from `fairyclaw.capabilities`.

**Group runtime config**: each capability group defines its own `BaseModel` (frozen) and exposes it as `runtime_config_model` in `config.py`.  The registry calls `load_group_runtime_config` once at startup and injects the snapshot into `ToolContext.group_runtime_config`.  Scripts call `expect_group_config(context, MyGroupConfig)` to retrieve it safely.

---

## `fairyclaw/core/events/`

The runtime backbone. All agent activity is driven by events.

| File | Responsibility |
|---|---|
| `bus.py` | `RuntimeEvent`, `RuntimeEventEnvelope`, `SessionEventBus` — the in-process pub/sub backbone. Event types: `USER_MESSAGE_RECEIVED`, `SUBTASK_COMPLETED`, `WAKEUP_REQUESTED`, `FILE_UPLOAD_RECEIVED`, `FORCE_FINISH_REQUESTED`. Custom events can be strings declared in capability manifests. |
| `session_scheduler.py` | Core state machine: per-session mailbox, debounce, `run_session`, heartbeat watchdog. Drives the Planner on wakeup. |
| `plugin_dispatcher.py` | Dispatches non-scheduler events to `event:*` capability hooks. |
| `payloads.py` | Payload constructors for core event types. |
| `runtime.py` | Global singletons (`set_runtime_bus`, `publish_runtime_event`, `set_file_delivery`). |

---

## `fairyclaw/core/agent/`

The agent orchestration layer. The Planner runs one "turn" per wakeup.

### `planning/`

| File | Responsibility |
|---|---|
| `planner.py` | Single-step Planner: reads a `TurnRequest`, runs the shared orchestration loop (context → hook pipeline → LLM → tool execution → follow-up). |
| `planner_core.py` | Dependency assembly (registry, router, hook runtime, LLM client). |
| `turn_policy.py` | `MainSessionTurnPolicy` / `SubSessionTurnPolicy` — encapsulates behavioral differences between main and sub-agent turns (skip, follow-up, failure handling). |
| `turn_runner.py` | Executes one complete turn: hook pipeline, LLM call, tool loop, result. |
| `subtask_coordinator.py` | Sub-task lifecycle: terminal state marking, barrier aggregation, result relay. |

### `context/`

| File | Responsibility |
|---|---|
| `history_ir.py` | Typed history IR: `SessionMessageBlock`, `MessageBody`, `ToolCallRound`, `UserTurn`, `ChatHistoryItem`. This is the canonical in-memory representation of conversation history consumed by hooks. |
| `llm_message_assembler.py` | IR → `LlmChatMessage` serialization for provider APIs. Collapses consecutive `ToolCallRound` entries back into one `tool_calls` message. |
| `turn_context_builder.py` | Splits history into `history_items` (past) and `user_turn` (current input). |
| `system_prompts.py` | System prompt templates. |

### `hooks/`

| File | Responsibility |
|---|---|
| `protocol.py` | All hook boundary types: `HookStage`, `HookExecutionContext`, `HookStageInput`, `HookStageOutput`, `HookStatus`, `HookError`, five payload types (`ToolsPreparedHookPayload`, `BeforeLlmCallHookPayload`, `AfterLlmResponseHookPayload`, `BeforeToolCallHookPayload`, `AfterToolCallHookPayload`), `LlmTurnContext`, `LlmFunctionToolSpec`. |
| `runtime.py` | Hook execution with timeout, error strategy (`continue` / `fail` / `warn`), and typed payload chaining within a stage. |
| `hook_stage_runner.py` | Runs all hooks for one stage in priority order; enforces same-type payload chaining. |

### `session/`

| File | Responsibility |
|---|---|
| `memory.py` | `PersistentMemory`: DB row → IR, typed write of `SessionMessageBlock` and `ToolCallRound`, compaction snapshots. |
| `session_role.py` | `MainSessionRole` / `SubSessionRole` policy objects (user callbacks, auto-terminal for text). |
| `global_state.py` | Sub-agent registry, terminal status, barrier aggregation, batch numbering. |

### `routing/`

`router.py` — `ToolRouter`: selects capability groups for a sub-agent turn based on `task_type`. Main sessions use `always_enable_planner` groups directly.

### `executors/`

`context_pipeline.py` / `tool_pipeline.py` — compose hooks with LLM context building and tool execution without holding global state.

### `interfaces/`

Light exports (e.g. `CompactionSnapshot`); session persistence is `session/memory.py` (`PersistentMemory`).

---

## `fairyclaw/core/gateway_protocol/`

Bridge-protocol models shared between Business and Gateway.

| File | Responsibility |
|---|---|
| `models.py` | `BridgeFrame`, all payload dataclasses (hello, session open, inbound, outbound, ack, file transfer). See [`docs/GATEWAY_ENVELOPE.md`](docs/GATEWAY_ENVELOPE.md) for the full protocol reference. |
| `ingress.py` | `GatewayIngressService`: converts typed inbound frames into session history and runtime events. |
| `files.py` | File upload/download service (business side). |

---

## `fairyclaw/capabilities/`

Pluggable capability groups. Each subdirectory is a group:

| Group | What it provides |
|---|---|
| `agent_tools/` | `delegate_task`, `get_subtask_status`, `kill_subtask`, `message_subtask`. Core sub-agent delegation. |
| `sub_agent_tools/` | `report_subtask_done`. Used by sub-agents to signal completion. |
| `core_ops/` | File system tools, Python execution, shell commands, file export. |
| `web_tools/` | Web search, page fetch, file download. |
| `sourced_research/` | Citation-backed research pipeline for sub-agents: `find_evidence_sources` → `extract_evidence_excerpt` → `format_answer_with_citations`. Not always-enabled; selected by `ToolRouter` for tasks requiring verifiable sources. |
| `memory_hooks/` | Hybrid memory extraction and context injection hooks. |
| `rag_hooks/` | RAG retrieval hook (before_llm_call). |
| `compression_hooks/` | Context compression hook to fit token budgets. |
| `routing_hooks/` | Turn routing hook for sub-agent group selection. |
| `runtime_event_hooks/` | Event-driven hooks (e.g. `file_upload_received`). |

To add a group: create `fairyclaw/capabilities/<your_group>/manifest.json` and a `scripts/` directory. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full schema.

---

## `fairyclaw/infrastructure/`

Adapters and services with no framework coupling:

| Module | Description |
|---|---|
| `database/` | SQLAlchemy async engine, `models.py`, `repository.py`, `session.py`. |
| `llm/` | `client.py` (LLM call wrapper), `factory.py` (profile → client), `config.py` (yaml loader). |
| `embedding/` | `service.py` — hashing-based and OpenAI-compatible embedding backends. |
| `files/` | `file_kind.py` — magic-byte file type detection for inbound uploads. |
| `tokenizer/` | `counter.py` — token counting via tiktoken. |
| `web/` | `ddgs_client.py` — async DuckDuckGo search wrapper; `page_text.py` — HTTP fetch + BeautifulSoup text extraction. Shared by `web_tools` and `sourced_research`. |
| `logging_setup.py` | Root and `fairyclaw` logger initialization with structured formatting. |

---

## `fairyclaw/gateway/`

The Gateway process: user-facing adapters and the bridge client.

| File/Dir | Description |
|---|---|
| `main.py` | FastAPI app for the Gateway; mounts adapter routes; serves `web/dist` at `/app` if built. |
| `runtime.py` | `GatewayRuntime`: builds adapter routers, starts the bridge client, routes outbound messages back to users. |
| `route_store.py` | In-memory mapping of `session_id → adapter_key` for outbound routing. |
| `adapters/web_gateway_adapter.py` | Web UI adapter: WebSocket at `/v1/ws` only (SPA; no REST chat). |
| `adapters/onebot_adapter.py` | OneBot v11 adapter: event intake at `POST /onebot/event`, session management commands. |
| `bridge/ws_client.py` | WebSocket client that connects to Business, handles reconnect, frame dispatch. |
