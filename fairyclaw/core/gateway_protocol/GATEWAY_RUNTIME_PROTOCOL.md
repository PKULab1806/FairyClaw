# Gateway runtime protocol (envelope types)

## Versioning

- `BridgeFrame.v` / `PROTOCOL_VERSION` in `models.py` (currently 2).
- Gateway hello `supports.gateway_control_envelope_version` (integer, currently 2).

## Control envelope types

Python module: `fairyclaw.core.gateway_protocol.control_envelope`.

| Type | Purpose |
|------|---------|
| `HeartbeatInfo` | `status`, `server_time_ms`, optional `message` |
| `TelemetrySnapshot` | Monthly token usage + `heartbeat` + optional Reins fields |
| `SubagentTaskState` | One row for background sub-agent tasks (`event_count`, `last_event_at_ms` optional aggregates from persisted events) |
| `MessagePreviewLine` | `role`, `text`, optional `ts_ms` |
| `SessionSummary` | `session_id`, `title`, `updated_at_ms`, `preview_messages` |
| `CapabilityGroupPolicy` | Skill group flags for planner visibility |
| `ToolCallEnvelope` | Before tool execution: `tool_call_id`, `tool_name`, `arguments` (object) |
| `ToolResultEnvelope` | After execution: `tool_call_id`, `tool_name`, `ok`, optional `result`, `error_message`, `duration_ms` |
| `TimerTickEnvelope` | Timer trigger delivered to planner: `job_id`, `mode`, `owner_session_id`, `creator_session_id`, `run_index`, optional `payload`, `next_fire_at_ms` |

`event_type` for these includes `tool_call` (`EVENT_TYPE_TOOL_CALL`), `tool_result` (`EVENT_TYPE_TOOL_RESULT`), and `timer_tick` (`EVENT_TYPE_TIMER_TICK`).

`LlmEndpointsDocument` is the JSON tree matching `config/llm_endpoints.yaml` (`default_profile`, `fallback_profile`, `profiles`).

`SystemEnvironmentSlice` is a string map with keys restricted to `SYSTEM_ENV_WHITELIST` in `control_envelope.py` (FAIRYCLAW_* lines 1–24 scope).

## `GatewayOutboundMessage` events

- `kind`: `event` (`OUTBOUND_KIND_EVENT`).
- `content` must include `event_type` (see `EVENT_TYPE_*` in `control_envelope.py`).

## Web gateway WebSocket (`/v1/ws`)

- Query: `token` must equal `FAIRYCLAW_API_TOKEN`.
- Client sends JSON: `{ "op": string, "id": string, "body": object }`.
- Server responds: `{ "op": "ack", "id": same, "ok": true, "body": object }` or `{ "op": "error", "id": same, "ok": false, "message": string }`.
- Server pushes assistant messages: `{ "op": "push", "body": { "session_id", "kind", "content", "meta" } }`. Besides `kind` `text` / `file`, the web adapter may push `kind` `event` with `content.event_type` (including `tool_call` / `tool_result` for tool lifecycle UI).
- When `session_id` is the sentinel `__fc_broadcast__`, the gateway delivers the push to **every** connected web client (no `session.bind` required). Used for `event_type=telemetry` (`TelemetrySnapshot`).

### Sub-session push routing (Web gateway)

Browsers normally `session.bind` only the **main** (web) session id. Sub-agent runs under a **child** `session_id` (contains `_sub_` per server convention). Business still emits `GatewayOutboundMessage` with that **child** `session_id` for assistant text, tool lifecycle events, and file notifications.

**Web `WebGatewayAdapter` behavior:**

1. If there is at least one WebSocket subscribed to `body.session_id`, the push is delivered only to those subscribers (same as main session).
2. If there are **no** subscribers for that id, the gateway loads `parent_session_id` from `GatewaySessionRouteModel` and delivers the **same** JSON to subscribers of the **parent** session id instead.
3. **`body.session_id` stays the child session id** in both cases so the client can route UI: e.g. render in the subtask detail timeline when `body.session_id` equals the selected subtask’s `child_session_id`, and render in the main timeline when it equals the bound main session id.
4. When delivery uses the parent fallback (step 2), the gateway sets **`meta.fc_parent_session_id`** to the parent session id string if not already present. Clients may ignore it if they already know the bind context.

**`fc_*` meta:** Only **`fc_parent_session_id`** is defined for this flow; there is no `fc_mirrored_sub_session` flag.

**Initial subtask history:** Use `sessions.history` with `session_id` set to the child id; response shape matches the main session. **Security note:** this build does **not** require that the Web client has bound the parent before reading a child history (single-tenant assumption: one user per deployment, protection is `FAIRYCLAW_API_TOKEN` / WebSocket `token`). Harden with bind checks if you share one deployment across users.

### Operations

| `op` | `body` | `ack.body` |
|------|--------|------------|
| `ping` | `{}` | `{ "pong": true }` |
| `session.create` | `platform`, optional `title`, `meta` | `{ session_id, title, created_at }` |
| `session.bind` | `{ session_id }` | `{ bound, session_id }` (replays backlog) |
| `chat.send` | `{ session_id, segments }` | `{ status, message }` (ack message is transport/debug info; Web UI does not render it as a chat bubble) |
| `file.upload` | `{ session_id, filename, content_base64, mime_type? }` | `{ file_id, filename, size, created_at }` |
| `file.download` | `{ session_id, file_id }` | `{ file_id, filename, mime_type, content_base64 }` |
| `config.llm.get` | `{}` | LLM document snapshot (YAML as JSON tree) |
| `config.llm.put` | LLM document tree | `{ "ok": true }` |
| `config.system_env.get` | `{}` | `{ "env": { FAIRYCLAW_*: string } }` (whitelist subset) |
| `config.system_env.put` | `{ "env": { ... } }` | `{ "ok": true }` |
| `config.onebot.get` | `{}` | OneBot settings (Gateway-only) |
| `config.onebot.put` | `{ ONEBOT_* fields }` | `{ "ok": true }` |
| `sessions.list` | `{}` | `{ "sessions": [...] }` — Web gateway reads shared DB and returns **`platform == "web"`** sessions only (filter stays in Gateway adapter, not Business) |
| `sessions.history` | `{ "session_id", "limit"? }` | `{ "session_id", "events": [...] }` — Business DB via Bridge `gateway_control` (`sessions.history`); each event is `session_event` (role, text, ts_ms) or `operation_event` (tool_name, result_preview, ts_ms). Works for **child** sub-agent session ids as well as the main web session. |
| `sessions.subagent_tasks` | `{ "session_id" }` | `{ "session_id", "tasks": [...] }` — Business runtime subtask snapshot for the main session (labels/status for running & terminal sub-agents) |
| `sessions.usage` | `{ "session_id" }` | `{ "session_tokens_used", "session_prompt_tokens_used", "session_completion_tokens_used", "month_tokens_used", ... }` aggregated from persisted event usage fields |
| `capabilities.list` | `{}` | `{ "groups": [...] }` |
| `capabilities.put` | `{ "group_name", patch fields }` | `{ "ok": true }` |

OneBot (`config.onebot.*`) is handled in the **Gateway** process only; it does not use shared `control_envelope` types.

Web UI rendering note: if a persisted `session_event` arrives with `role=user` but text starts with `[System Notification]`, frontend maps it to **system-style display only** (storage semantics unchanged).

## Bridge (gateway ↔ business)

- Chat/file frames unchanged; `outbound` frames carry `GatewayOutboundMessage` (including `kind=event`).
- **Bridge control (same WebSocket, envelope only—not HTTP)**: `gateway_control` / `gateway_control_ack` frames for Business-owned data (LLM YAML, system env whitelist, capability manifests, **session event history**). Payload: `{ "op": string, "body": object }`; ack: `{ "request_id": string, "ok": bool, "body"?: object, "error"?: object }`. No separate RPC or HTTP control plane; Gateway ↔ Business use only `BridgeFrame` on the internal bridge WebSocket.

On the Business process, [`fairyclaw/bridge/user_gateway.py`](../../../fairyclaw/bridge/user_gateway.py) (`UserGateway`) owns the bridge WebSocket and enqueues outbound frames.
