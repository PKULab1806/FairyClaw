# FairyClaw AI Development and Architecture Guide

This guide is written for AI assistants and human developers working with the FairyClaw runtime. It reflects the current implementation, with particular focus on recent architectural changes: event-bus wakeup model, typed history IR, five-stage Hook pipeline, single-step Planner, Sub-Agent barrier aggregation, capability group decomposition, and single-dispatch routing.

## 1. System Overview

FairyClaw is an async, event-driven agent framework. Its core goal is to transform "handle user request" from a synchronously blocking operation into a continuously advanceable background task model.

Key characteristics:
- Session-level scheduling via the Runtime Event Bus; no more long loops inside a single request.
- The Planner executes exactly one inference step per wakeup, then re-publishes an event to advance.
- The main agent can delegate to multiple Sub-Agents running concurrently; results are aggregated via a barrier and flow back to the main session.
- Tools are dynamically registered via Manifest + Script, keeping the system fully pluggable.
- Capability groups can declare custom `event_types` in `manifest.json` for pluggable runtime event dispatch.
- The Hook lifecycle has exactly five stages: `tools_prepared` / `before_llm_call` / `after_llm_response` / `before_tool_call` / `after_tool_call`.
- `before_llm_call` receives the full typed history IR by default, not raw `dict` history.
- A Hook can silently short-circuit the current turn at any supported stage via `force_finish`: the event is published but no further inference or follow-up occurs.

## 2. Directory and Key Modules

- [`LAYOUT.md`](LAYOUT.md)
  - Full module responsibility map: top-level directories, core event system, agent sub-directories, capability groups, infrastructure layers.
- `fairyclaw/main.py`
  - Starts `SessionEventBus` and the runtime scheduler; owns dependency assembly and lifecycle management.
- `fairyclaw/core/events/bus.py`
  - Defines `RuntimeEvent`, `RuntimeEventEnvelope`, `SessionEventBus`.
  - Event types allow `EventType | str`; core events use the enum; custom events use the string names declared in manifests.
  - The internal subscriber table normalizes all keys to strings wrapped in `EventTypeKey`, preventing `EventType.X` and `"x"` from coexisting as separate keys.
- `fairyclaw/core/events/session_scheduler.py`
  - Owns the core session scheduling state machine: mailbox consumption, debounce, `run_session`, `heartbeat_watchdog`.
  - Receives all runtime events through a single entry point `on_event`.
  - Three core events — `USER_MESSAGE_RECEIVED` / `SUBTASK_COMPLETED` / `WAKEUP_REQUESTED` — go through the mailbox + wakeup state machine.
  - All other events are forwarded to `EventPluginDispatcher` for `event:*` hook dispatch; manifest-declared custom events only follow this path and never reach the Planner.
- `fairyclaw/core/agent/planning/planner.py`
  - Single-step Planner core: reads a typed `TurnRequest`, runs the shared orchestration.
  - Main/sub-session behavioral differences are abstracted into `turn_policy.py`; no more scattered `_is_sub_session(...)` branches.
  - The main session does not go through tool-group routing; capability groups are enabled by `always_enable_planner=true`.
  - Sub-Agent terminal state and barrier aggregation are delegated to `SubtaskCoordinator`.
- `fairyclaw/core/agent/planning/turn_policy.py`
  - Defines `MainSessionTurnPolicy` / `SubSessionTurnPolicy`, encapsulating skip, follow-up, and failure handling differences.
- `fairyclaw/core/agent/planning/subtask_coordinator.py`
  - Unified handling of sub-task terminal state marking, immediate failure notifications, and batch barrier aggregation publishing.
- `fairyclaw/core/agent/context/turn_context_builder.py`
  - Builds the `LlmTurnContext` from typed history.
  - Explicitly separates past history from the current user turn, generating `user_turn` as a distinct field.
- `fairyclaw/core/agent/context/history_ir.py`
  - Defines `SessionMessageBlock` / `MessageBody` / `ToolCallRound` / `UserTurn`.
- `fairyclaw/core/agent/context/llm_message_assembler.py`
  - Assembles the IR into `LlmChatMessage`.
  - Collapses consecutive `ToolCallRound` entries back into a single assistant `tool_calls` message, preventing history replay from splitting one tool-call batch into multiple empty assistant messages.
- `fairyclaw/core/agent/planning/planner_core.py`
  - Shared orchestration dependency assembly (registry, router, hook runtime, LLM client resolution).
- `fairyclaw/core/agent/session/session_role.py`
  - Behavioral policy for main and sub sessions (whether to call back the user, whether text turns auto-terminate).
- `fairyclaw/core/agent/interfaces/memory_provider.py`
  - The single active interface: `MemoryProvider`.
  - `get_history()` returns `ChatHistoryItem` IR directly.
  - Optional compaction snapshot interface: `get_latest_compaction()` / `create_compaction_snapshot()`.
- `fairyclaw/core/agent/session/memory.py`
  - `PersistentMemory`: parses database rows into IR; writes typed `SessionMessageBlock` / `ToolCallRound`.
  - Also reads and writes compaction snapshots in the `memory_compactions` table.
- `fairyclaw/core/agent/session/global_state.py`
  - Sub-agent registry, terminal state checking, aggregated result construction.
  - Batch number management: retains full history while aggregating only the current batch.
- `fairyclaw/core/capabilities/registry.py`
  - Dynamically loads capability groups; supports `.resolve()` on script paths (enables cross-group script reuse).
  - Handles Hook registration, enabled-group resolution, manifest `event_types` parsing, and per-stage hook retrieval.
- `fairyclaw/core/agent/hooks/protocol.py`
  - Defines the Hook I/O protocol: `HookExecutionContext`, `HookStageInput/Output`.
  - Five strongly-typed stage payloads: `ToolsPreparedHookPayload`, `BeforeLlmCallHookPayload`, `AfterLlmResponseHookPayload`, `BeforeToolCallHookPayload`, `AfterToolCallHookPayload`.
  - `LlmTurnContext` carries:
    - `history_items`: full typed history IR
    - `user_turn`: current turn user input IR
    - `llm_messages`: provider-facing messages derived from the IR
- `fairyclaw/api/outbound/session_text_outbound.py`
  - (Removed) The old callback_url outbound delivery implementation has been deleted. Outbound is now handled by the Gateway process via the WS Bridge.
- `fairyclaw/core/agent/hooks/runtime.py`
  - Unified Hook execution with timeout and error strategy (continue / fail / warn).
  - Within a stage, only typed payload chaining is allowed; `dict` merge is no longer supported.
- `fairyclaw/core/agent/hooks/hook_stage_runner.py`
  - Resolves enabled hooks per stage and delegates to `HookRuntime`; does not occupy the `SessionEventBus`.
- `fairyclaw/capabilities/agent_tools/manifest.json`
  - Main Planner tools: `delegate_task`, `get_subtask_status`, `kill_subtask`, `message_subtask`.
- `fairyclaw/capabilities/sub_agent_tools/manifest.json`
  - Sub-Agent exclusive tools: `report_subtask_done`.

## 3. Event-Driven Execution Model

### 3.1 From User Message to Planner Execution

1. A user message enters the system and publishes a `USER_MESSAGE_RECEIVED` event.
2. `session_scheduler.py`'s `on_event(...)` only enqueues the event into the session mailbox on `user_message_received`; it does not directly trigger `WAKEUP_REQUESTED`.
3. `WAKEUP_REQUESTED` is issued by:
   - `run_session(...)` detecting unconsumed mailbox events at the end of a session run and auto-publishing;
   - `heartbeat_watchdog` periodically scanning for sessions that have mailbox events but are neither inflight nor queued, and re-issuing wakeups.
4. `RuntimeSessionScheduler.run_session(session_id)` wakes up, consumes the mailbox, selects `task_type` and `enabled_groups`, and calls `process_background_turn(...)`.
5. `planner.process_turn(request: TurnRequest)` performs the single-step decision and execution.

### 3.2 Single-Step Inference and Continuation

The current Planner no longer uses a multi-round for-loop within a single wakeup. Instead:
- Each wakeup performs exactly one LLM inference.
- If the LLM returns plain text:
  - `_handle_text_fallback` writes a session event; text delivery to the user is handled by the Gateway layer outbound (main session only; sub-sessions do not callback directly).
  - Sub-sessions simultaneously mark terminal state and attempt to trigger the barrier.
- If the LLM returns tool calls:
  - Tools are executed and an operation event is written.
  - If the assistant also returned visible text alongside the tool calls, that text is first persisted as an assistant session message to prevent loss during history replay.
  - If the tool is not a terminal tool, an internal follow-up event is published, triggering the next `USER_MESSAGE_RECEIVED` to continue the next step.
  - If a hook sets `force_finish=True` at a supported stage, `force_finish_requested` is published and the current turn ends immediately without a follow-up.

This follow-up mechanism is critical to the stability of the single-step architecture; its absence causes "stuck after one tool call".

### 3.3 Heartbeat and Missed-Wakeup Protection

- A per-session heartbeat task is started during `run_session`, periodically updating `heartbeat_at`.
- The global `heartbeat_watchdog` periodically:
  - Logs stale session warnings;
  - Re-issues `WAKEUP_REQUESTED` for sessions that have mailbox events but are not queued for wakeup.

Note: the watchdog does not advance sessions without mailbox events, so tool continuation must always be triggered via a follow-up event or another explicit event.

## 4. Sub-Agent Mechanics

### 4.1 Delegation Model

`delegate_task` no longer recursively calls the Planner directly. Instead:
- It creates a sub-session and writes the initial user content.
- It registers the sub-task in `global_state`.
- It immediately returns and publishes `USER_MESSAGE_RECEIVED` to the sub-session (without waiting for router LLM selection), letting the sub-session run on the unified event path.

This ensures main/sub execution models are consistent, and the main session is never blocked by sub-tasks.

### 4.2 Terminal State Reporting Tool

`report_subtask_done` is called by the Sub-Agent to write terminal state and a summary to `global_state`.

Properties:
- Should only be visible to sub-sessions.
- `record_event: false` — not written to the normal operation history.
- Idempotent via `mark_terminal`: repeated calls on an already-terminal task return "already in terminal state".

### 4.3 Barrier Aggregation

When a sub-task's terminal state changes, the Planner attempts `_publish_subtask_barrier_if_ready`:
- The final aggregation notification is only published when all sub-tasks in the current batch have reached a terminal state.
- The barrier flag is only set after the aggregation event is successfully published, preventing premature flagging that could cause missed aggregations.
- The notification summarizes each sub-task's status and summary, then publishes `SUBTASK_COMPLETED` to wake the main session to continue responding to the user.
- The failure path retains "dual-channel" semantics: a failure can trigger an immediate notification, and then the final aggregation at batch completion (with deduplication of immediate failure notifications for the same sub-task).

## 5. Sub-Task State Management and Concurrency Safety

`SessionSubTaskState` key behaviors:
- `register_task`: registers a new task; if the current batch is already fully terminal, auto-advances to a new batch number.
- `mark_terminal`: atomically transitions a task to `completed/failed/cancelled`.
- `reopen_task`: can restore an already-terminal sub-task to `running:<task_type>` (for `message_subtask` continuation).
- `is_all_subtasks_terminal`: checks only whether the current batch is fully terminal.
- `get_aggregated_subtask_results`: aggregates only the current batch; `list_records()` preserves full history visibility (for `get_subtask_status`).

Recent fixes:
- No longer clears old `records`, fixing the regression where `get_subtask_status` could not see completed sub-tasks.
- Batch number isolation for aggregation windows prevents new-batch aggregations from mixing in old-batch summaries.

Concurrency note:
- `get_subtask_status` performs only in-memory reads; it holds no mutex waits and involves no nested await lock chains, so there are currently no deadlock paths.

## 6. Capability Groups and Tool Visibility

### 6.1 Why Groups

The old approach used an `exclude_tools` name blacklist for main/sub differences — poor readability and stability. The new model:
- Main-agent exclusive group: `AgentTools` (contains `delegate_task`, etc.).
- Sub-agent exclusive group: `SubAgentTools` (contains `report_subtask_done`).

### 6.2 Current Visibility Guarantees

Inside the Planner, the session-level tool set is controlled by scope flags:
- Main session: only `always_enable_planner=true` groups are enabled; no routing.
- Sub-session: `always_enable_subagent=true` baseline groups + `enabled_groups` from delegation routing.
- Delegation routing only selects among candidate groups with `always_enable_subagent=false`.

Visibility is now enforced by capability fields and session scope, not prompt luck.

### 6.3 Capability Lifecycle Stabilization

- Groups with `always_enable_planner=true` are always enabled for the main session.
- Groups with `always_enable_subagent=true` are always enabled for sub-sessions.
- Non-always-enable groups are enabled based on routing results.
- Sub-sessions perform routing exactly once before their first dispatch and persist `enabled_groups`; subsequent wakeups reuse the same set without re-routing.
- Hooks and tool schemas are only registered for enabled capability groups.

### 6.4 Typed Hook Protocol

- `before_llm_call` receives `BeforeLlmCallHookPayload`; core fields are `turn: LlmTurnContext` and `tools: list[LlmFunctionToolSpec]`.
- Hooks must not parse raw string history; text derivation goes through `SessionMessageBlock` (and its body subtypes) instance methods.
- When reading history semantics in `before_llm_call`:
  - Use `turn.history_items` for the full typed history.
  - Use `turn.user_turn` for the current user input.
  - Use `turn.llm_messages` only when you need to rewrite the provider request.
- `after_llm_response` / `before_tool_call` / `after_tool_call` use structured payloads (e.g. `LlmToolCallRequest`, `AfterToolCallHookPayload`), minimizing magic keys.

### 6.5 CoreOperations Constraints

- The actual capability group name in the implementation is `CoreOperations`, not `CoreMessaging`.
- `CoreOperations` is not always-enable by default; it contains filesystem, command execution, session file I/O, and `send_file` tools.
- Whether the main session exposes this group depends on `always_enable_planner`; whether a sub-session exposes it depends on `always_enable_subagent` and delegation routing results.
- Text/file delivery goes through the **GatewayEgress interface** (`emit_text` / `emit_file`) injected into the Planner — Business → WS Bridge → Gateway → Adapter — and does not use HTTP callbacks.
- The Gateway maintains a separate database routing table for `session → channel` and `sub_session → parent_session` mappings; Business no longer reads or writes `session.meta.gateway_route`.
- The `send_file` tool only persists a local file as a session file (generating a `file_id`); the Planner itself no longer hard-codes `send_file` outbound delivery — file return is handled uniformly by the outer tool-result egress adapter.
- `send_file` does not insert file segments into the session message history (preventing file segment / JSON arrays in the Planner context); persisted tool-result rows are redacted to a brief description; users only receive the actual file through the Gateway channel.
- File outbound frames carry only `file_id`; the adapter resolves the file bytes and delivers them according to its channel semantics.
- Gateway and Business must share the same `DATABASE_URL`; otherwise `gateway_session_routes` cannot resolve session routing and file/text delivery to OneBot and other adapters will fail.

### 6.6 Script Path Loading

`CapabilityRegistry` calls `.resolve()` on script paths and loads scripts using "nearest implementation in group" semantics. The `SubAgentTools/report_subtask_done` script lives at `sub_agent_tools/scripts/`.

## 7. Typed History / IR Layers

The system maintains three layers of conversation data:

1. Persistence / repository layer
   - Database rows, repository return values.
   - May be raw JSON or row objects, but must not cross the `session/` boundary to reach the planner directly.

2. System IR (Internal Representation)
   - `SessionMessageBlock`
   - `ToolCallRound`
   - `UserTurn`
   - `ChatHistoryItem`
   - These are the canonical semantic objects that hooks and the planner should consume.

3. LLM boundary objects
   - `LlmChatMessage`
   - Derived from IR; used for the final provider request.
   - Not a business IR.

Current boundary rules:
- `MemoryProvider.get_history()` returns `list[ChatHistoryItem]` directly.
- `TurnContextBuilder` separates the current `user_turn` from history.
- `LlmMessageAssembler` handles `IR → LlmChatMessage`.
- `to_openai_messages(...)` is the final provider serialization boundary.

## 8. Routing and Scheduling Strategy

### 8.1 Main Planner Routing

The main Planner (non-sub-session) does not go through tool-group routing. Tool groups are determined by `always_enable_planner=true` capability groups.

Benefits:
- Shorter, more stable context; execution tools cannot pollute the Planner.
- The orchestration role is clear and does not depend on prompt self-discipline.

### 8.2 Sub-Agent Routing

Sub-agents route exactly once, before their first dispatch after delegation:
- `delegate_task` only writes baseline `enabled_groups`, `routing_pending`, and `route_input`, then returns immediately.
- `run_session` detects `routing_pending=true`, calls the Router, writes back the final `enabled_groups`, and clears the pending flag.
- Both the first wakeup and all subsequent wakeups reuse the already-persisted `enabled_groups`.
- Routing failure falls back to the set of `always_enable_subagent=true` groups.

Benefits:
- Avoids repeated Router calls on every wakeup.
- Stable tool set across sub-task steps; reduces routing churn.
- Removes fingerprint/budget cache logic; state is more direct.

## 9. Runtime Events and Compatibility Requirements

Runtime events fall into two categories:

- `user_message_received`
- `subtask_completed`
- `wakeup_requested`
- `file_upload_received`
- `force_finish_requested`
- Custom string events declared in capability manifests (e.g. `my_custom_event`)

Each event in `fairyclaw/core/events/payloads.py` has an explicit dataclass payload inheriting from `EventPayloadBase`:
- `session_id`
- `event_id`
- `source`
- `timestamp_ms`

Notes:
- The old `rag_index_requested` / `memory_compaction_requested` / `route_recompute_requested` events have been removed.
- File uploads now drive runtime hooks via `file_upload_received`.
- Rail-like hook short-circuiting now uses `force_finish_requested` to drive the runtime hook.
- Custom events are exposed to event hooks via `GenericRuntimeEventPayload`; `event_type`, `data`, and `schema_definition` are all explicit fields.
- Event hooks consume typed payloads via `EventHookHandler`, not raw `dict`.
- Custom events only enter `EventPluginDispatcher`; they do not write to the mailbox, trigger wakeups, or reach the Planner.

`RuntimeEventEnvelope` must have:
- An `event_type` field.
- A `payload` field.
- A `type` property alias (returns `event_type`).

Reason: `run_session` parses `task_type` from `event.type` and `event.payload` when consuming the mailbox; missing fields will cause event processing exceptions that break the wakeup chain.

## 10. Standard Way to Add New Capabilities

1. Define the capability group and tool schema in `fairyclaw/capabilities/<group>/manifest.json`.
2. Implement `async def execute(args, context)` under `scripts/`.
3. The `script` field is resolved as `<group>/scripts/<name>.py` by default; resolvable relative paths can also be used for cross-group reuse.
4. Whether a tool's result is written to history is controlled by `record_event`.
5. If a tool is only for sub-sessions, do not include it in a main-session group; constrain visibility via `always_enable_subagent` and routing scope.
6. If a capability group needs to publish extension events asynchronously, declare `event_types` at the top of the manifest; both short string lists and objects with `description` / `schema` are supported.
7. The corresponding hook stage is `event:<event_type>`; publish via `publish_runtime_event("<event_type>", ...)`.

### 10.1 SourcedResearch Capability Group

`sourced_research/` provides a three-tool citation pipeline for sub-agents:
- `find_evidence_sources` (step 1): DuckDuckGo search; returns numbered candidate URLs.
- `extract_evidence_excerpt` (step 2): Fetches one URL and returns focused body text as citation evidence.
- `format_answer_with_citations` (step 3): Validates that every citation has a non-empty URL + excerpt, then returns a Markdown answer with a Sources section.

Visibility: `always_enable_planner: false`, `always_enable_subagent: false`. The `ToolRouter` selects it when a delegated task description indicates verifiable, source-backed research is required. Infrastructure shared with `web_tools` lives in `fairyclaw/infrastructure/web/` (`ddgs_client.py`, `page_text.py`).

### 10.2 Context Compression and Memory Capability Group Conventions

- `compression_hooks`
  - Responsible for token budget control; compresses only `history_items`, then rebuilds `llm_messages` via `LlmMessageAssembler`.
  - Group-level config lives in `compression_hooks/config.yaml`.
- `rag_hooks`
  - Queries vector memory and injects extra system messages during `before_llm_call`.
  - Vector store logic must be encapsulated within the capability group; do not push it into core/infrastructure abstractions.
  - Group-level config lives in `rag_hooks/config.yaml`.
- `memory_hooks`
  - `memory_pre_context`: injects session summary anchors.
  - `memory_extraction`: extracts long-term facts during `after_llm_response` and writes them to `rag_chunks`.
  - Initial `memory_extraction` uses heuristic extraction, not LLM extraction.
  - Group-level config lives in `memory_hooks/config.yaml`.
- Model config goes in `config/llm_endpoints.yaml`:
  - `embedding` profile: local embedding model.
  - `compaction_summarizer` profile: summary generation.
- Do not add capability-group-specific parameters to `fairyclaw.env` / `settings.py` beyond what already exists.

## 11. Rules for Core Modifications

- Async-first: all I/O, DB, LLM, and event publishing must use `async/await`.
- Do not break dual-track event semantics: user-visible conversation writes to `session`; tool calls write to `operation`.
- Do not bypass the event chain: never drive Planner recursion directly from inside a tool.
- Do not allow sub-task results to cross batch boundaries: all batch aggregation logic must have a clear boundary.
- Do not expose sub-session tools to the main session (or vice versa).
- Uniform authentication on gateway interfaces: `Sessions` / `Chat` / `Files` all require `require_auth`.
- File read interfaces must enforce session-scope validation: `file_id` queries must carry and verify `session_id` (proxied by the Gateway via WS `file_get` to Business).
- Do not let hook payloads degrade to `dict`; stage payloads must remain explicitly typed objects.
- Do not introduce `from_legacy(...)` style v1 compatibility shims.

## 12. Recent Key Fixes and Behavioral Changes

1. Fixed `RuntimeEventEnvelope` missing fields causing `wakeup_requested` processing exceptions.
2. Added internal follow-up events after tool execution, resolving "stuck after one tool call".
3. Completed the Sub-Agent barrier aggregation path; the main session is now woken up only after all sub-tasks reach terminal state.
4. Fixed sub-task batch cross-contamination; new issues can no longer mix into old task summaries.
5. Split capability groups into `AgentTools` and `SubAgentTools`, achieving true main/sub tool isolation.
6. Main Planner changed to "always_enable_planner auto-enable"; Sub-Agent changed to "fast-return delegation + one routing before first dispatch + reuse afterward + always_enable_subagent baseline".
7. Rewrote the Planner system prompt to make "delegation is the default and required path" explicit.
8. Execution capability group name unified as `CoreOperations`; enabled per sub-session based on routing results.
9. Introduced the five-stage Hook pipeline and typed payload protocol, allowing the capability system to intercept prompt construction, retrieval, compression, routing, and post-tool processing.
10. `MemoryProvider` tightened to typed IR boundary: `get_history()` returns `ChatHistoryItem`; `query_memory` has been removed.
11. `TurnRequest.from_legacy(...)` removed; callers construct typed `TurnRequest` directly.
12. Main/sub Planner differences extracted to `turn_policy.py`, reducing `_is_sub_session(...)` branch proliferation.
13. `TurnContextBuilder` now explicitly extracts `user_turn`; the current-turn user input is no longer implicitly at the tail of `history_items`.
14. `LlmMessageAssembler` collapses consecutive tool rounds into a single assistant tool-call message and preserves assistant text accompanying tool calls.
15. Core session scheduling events are handled by `core/events/session_scheduler.py`; extension runtime events are dispatched via the plugin executor (`event:*` hook stage).
16. Context compression redesigned as budget-driven four-level progressive compression: large-item truncation, recent-window preservation, tool-history pruning, emergency trimming.
17. `memory_pre_context` generates/reads `memory_compactions` summaries on demand and injects them as extra system messages.
18. `memory_extraction` is now connected to the `after_llm_response` stage, using heuristic extraction and writing to `rag_chunks` + local vector store.
19. `rag_retrieval` is connected to local vector retrieval; session-scoped memory facts are recalled and injected into the prompt.
20. Hooks can now silently short-circuit a turn via `force_finish`: the runtime publishes `force_finish_requested`, which does not trigger a follow-up and does not enter the next inference round.
21. Capability groups can declare custom runtime events: event types allow `EventType | str`; the scheduler only sends the three core events to the mailbox/wakeup path; all other declared events go through `EventPluginDispatcher`.

## 13. Operational Advice for AI Assistants

- Determine whether the current session is a main session or a sub-session before diagnosing tool visibility issues.
- For "stuck" investigations, check three things first: whether a follow-up event was published, whether the mailbox has events, and whether `wakeup_queued`/`inflight` states are consistent.
- For "duplicate aggregation" issues, check whether the `global_state` batch boundary was reset.
- For "high router cost" issues, check whether routing is performed only once before the sub-session's first dispatch, and whether `enabled_groups` persistence is working correctly.
- When debugging `before_llm_call` behavior, distinguish the three layers:
  - Is `history_items` correct?
  - Is `user_turn` correct?
  - Is `llm_messages` simply the correct derivation from the IR?
- After modifying core scheduling, always run `python3 -m compileall fairyclaw` to validate, and write a minimal reproduction script to verify event chain integrity.

## 14. Gateway / Business Process Boundary

The user-facing entry point has been migrated to a standalone Gateway process; the Business process focuses solely on runtime and planning:

- **Business process**: `SessionEventBus` / `RuntimeSessionScheduler` / `Planner` / `PersistentMemory` / DB
  - Only exposes: `/healthz` + internal `WebSocket Bridge` (default `GET /internal/gateway/ws`)
- **Gateway process**: external HTTP API + OneBot adapter
  - Exposes: `/v1/sessions` / `/v1/sessions/{session_id}/chat` / `/v1/files` / `/v1/files/{file_id}` etc.
  - Communicates with Business via `WsBridgeClient`; protocol documented in [`docs/GATEWAY_ENVELOPE.md`](docs/GATEWAY_ENVELOPE.md)

Important constraints:
- The Business process no longer implements `callback_url` or any external HTTP callback delivery.
- The Business process no longer exposes external `Chat/Files/Sessions` HTTP APIs; those belong to the Gateway.
