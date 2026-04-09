# Gateway–Business Bridge Protocol

FairyClaw runs as two separate processes that communicate over a persistent WebSocket connection:

- **Business process** (default port `8000`) — the agent runtime, event bus, and planner. The WebSocket endpoint is implemented by [`UserGateway`](../fairyclaw/bridge/user_gateway.py) (`fairyclaw.bridge.user_gateway`).
- **Gateway process** (default port `8081`) — user-facing adapters (Web UI WebSocket, OneBot, etc.).

The Gateway initiates the connection to Business at `GET /internal/gateway/ws`.

---

## 1. Envelope (`BridgeFrame`)

Every message in both directions is a JSON text frame with this shape:

```json
{
  "v":      1,
  "type":   "<frame_type>",
  "id":     "frm_<hex>",
  "ts_ms":  1710000000000,
  "payload": {},
  "trace":  null
}
```

| Field | Type | Description |
|---|---|---|
| `v` | `int` | Protocol version. Currently `1`. |
| `type` | `string` | Frame type (see below). |
| `id` | `string` | Globally unique frame identifier for this sender. Used for acknowledgements and deduplication. |
| `ts_ms` | `int` | Unix timestamp in milliseconds at frame creation. |
| `payload` | `object` | Frame-specific data. Schema varies by `type`. |
| `trace` | `object \| null` | Optional diagnostic metadata. Ignored by recipients that don't understand it. |

Implementation: [`fairyclaw/core/gateway_protocol/models.py`](../fairyclaw/core/gateway_protocol/models.py) — `BridgeFrame`.

---

## 2. Connection Lifecycle

```
Gateway                              Business
   |                                    |
   |---- hello -----------------------> |
   |<--- hello_ack --------------------|
   |---- resume ----------------------> |  (re-connect: send last ack ids)
   |                                    |
   | <--- session_open_ack ------------ |  (async, whenever needed)
   | ---- session_open ------------->   |
   |                                    |
   |==== steady-state exchange =====|
   | ---- inbound ------------------>  |  user → agent
   | <--- outbound ------------------  |  agent → user
   | ---- ack ----------------------->  |  acknowledge outbound delivery
   | <--- ack -----------------------   |  acknowledge inbound receipt
   | <--> heartbeat -----------------  |  keepalive (both directions)
   |                                    |
   |==== file transfer (upload) =====|
   | ---- file_put_init ------------>   |
   | <--- file_put_ack (ok) ----------  |  upload_id assigned
   | ---- file_put_chunk (×N) ------->  |
   | <--- file_put_ack (chunk) -------  |  per-chunk ack
   | ---- file_put_commit ----------->  |
   | <--- file_put_ack (done) --------  |  file_id assigned
   |                                    |
   |==== file transfer (download) ===|
   | <--- outbound (kind=file) -------  |  Business sends file_id
   | ---- file_get ----------------->   |  Gateway requests bytes
   | <--- file_get_chunk (×N) --------  |  streamed chunks
   | ---- file_get_ack ------------->   |  Gateway confirms last chunk
```

### 2.1 Handshake frames

**`hello`** — Gateway → Business on connect:

```json
{
  "gateway_id": "gw_local",
  "token": "<FAIRYCLAW_BRIDGE_TOKEN>",
  "adapters": [
    { "adapter_key": "http",   "kind": "web_ws",      "version": "1" },
    { "adapter_key": "onebot", "kind": "onebot_v11",  "version": "1" }
  ],
  "supports": { "resume": true }
}
```

**`hello_ack`** — Business → Gateway:

```json
{ "ok": true, "connection_id": "<uuid>", "limits": {} }
```

**`resume`** — Gateway → Business after reconnect:

```json
{
  "gateway_id": "gw_local",
  "last_ack_inbound_id":  "<frame_id or null>",
  "last_ack_outbound_id": "<frame_id or null>"
}
```

---

## 3. Session Management

Before exchanging messages, a Business session must be opened.

**`session_open`** — Gateway → Business:

```json
{
  "adapter_key": "onebot",
  "platform":    "onebot",
  "title":       "My Chat",
  "meta":        {},
  "session_id":  null
}
```

Pass a non-null `session_id` to reattach to an existing session instead of creating a new one.

**`session_open_ack`** — Business → Gateway:

```json
{ "ok": true, "session_id": "<uuid>" }
```

---

## 4. Message Exchange

### 4.1 Inbound (`inbound`) — Gateway → Business

Carries one user message into the agent runtime.

```json
{
  "session_id":     "<uuid>",
  "adapter_key":    "onebot",
  "sender":         { "platform": "onebot", "user_id": "12345" },
  "segments":       [{ "type": "text", "text": "Hello" }],
  "trigger_turn":   true,
  "task_type":      null,
  "enabled_groups": null,
  "meta":           {}
}
```

- `trigger_turn`: if `true`, the Business runtime wakes the planner for this session.
- `task_type`: optional routing hint (`"general"`, `"image"`, `"code"`).
- `enabled_groups`: optional capability group whitelist for this turn.
- `segments`: array of `ContentSegment` objects.

### 4.2 Outbound (`outbound`) — Business → Gateway

Carries one agent response toward the user.

```json
{
  "session_id": "<uuid>",
  "kind":       "text",
  "content":    { "text": "Hello back!" },
  "meta":       {}
}
```

`kind` values:

| `kind` | `content` shape | Description |
|---|---|---|
| `text` | `{ "text": "..." }` | Plain text response. |
| `file` | `{ "file_id": "..." }` | File handle; use `file_get` to retrieve bytes. |
| `segments` | `{ "segments": [...] }` | Multi-part content. |
| `event` | `{ "event_type": "...", ... }` | System or plugin event notification. |

### 4.3 Acknowledgement (`ack`)

Either side sends `ack` to confirm delivery of a frame:

```json
{ "ref_type": "inbound", "ref_id": "<frame_id>", "status": "ok" }
```

`status` values: `ok`, `failed`, `duplicate`, `invalid`.

### 4.4 Heartbeat (`heartbeat`)

```json
{ "seq": 42 }
```

Both sides send heartbeats at a configurable interval. A missing heartbeat triggers reconnect.

---

## 5. File Transfer

### 5.1 Upload (Gateway → Business)

Used when the user attaches a file.

1. `file_put_init` — declare file metadata; Business responds with `upload_id`.
2. `file_put_chunk` (×N) — send base64-encoded chunks; Business acks each.
3. `file_put_commit` — finalize; Business responds with `file_id`.

**`file_put_init` payload:**

```json
{
  "session_id":  "<uuid>",
  "adapter_key": "onebot",
  "message_id":  "<msg_id>",
  "filename":    "report.pdf",
  "mime_type":   "application/pdf",
  "size_bytes":  204800,
  "sha256_hex":  "<hex>"
}
```

**`file_put_chunk` payload:**

```json
{ "upload_id": "<id>", "seq": 0, "data_b64": "<base64>", "chunk_bytes": 262144 }
```

**`file_put_ack` payload (on commit):**

```json
{ "status": "ok", "upload_id": "<id>", "seq": null, "file_id": "file_<hex>" }
```

Chunk size limit: `FAIRYCLAW_BRIDGE_MAX_CHUNK_BYTES` (default 256 KiB).

### 5.2 Download (Business → Gateway)

Business sends an `outbound` frame with `kind=file` and a `file_id`. The Gateway then pulls bytes:

1. `file_get` — `{ "session_id": ..., "file_id": ..., "request_id": ... }`
2. `file_get_chunk` (×N) — streamed base64 chunks from Business; `is_last: true` on final.
3. `file_get_ack` — Gateway confirms receipt.

---

## 6. Error Frame

Business sends `error` when it cannot process a frame:

```json
{ "code": "auth_failed", "message": "Invalid bridge token", "details": null }
```

---

## 7. Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `FAIRYCLAW_BRIDGE_TOKEN` | `fairyclaw-bridge-dev-token` | Shared secret between Gateway and Business. Must match on both sides. |
| `FAIRYCLAW_BRIDGE_WS_PATH` | `/internal/gateway/ws` | Business WebSocket endpoint path. |
| `FAIRYCLAW_GATEWAY_BRIDGE_URL` | `ws://127.0.0.1:8000/internal/gateway/ws` | URL Gateway uses to connect to Business. |
| `FAIRYCLAW_BRIDGE_MAX_FILE_BYTES` | `26214400` (25 MiB) | Maximum single file size. |
| `FAIRYCLAW_BRIDGE_MAX_CHUNK_BYTES` | `262144` (256 KiB) | Maximum chunk size per `file_put_chunk`. |
| `FAIRYCLAW_BRIDGE_OUTBOUND_BACKLOG_SIZE` | `512` | Per-session outbound queue depth. |
