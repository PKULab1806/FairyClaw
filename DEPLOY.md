# Deployment Guide

FairyClaw runs as **two separate processes** that share a single configuration file:

- **Business** (`fairyclaw/main.py`) — the agent runtime, event bus, planner, and bridge server. Default port `8000`. Should only be reachable from the Gateway process (internal network or same host).
- **Gateway** (`fairyclaw/gateway/main.py`) — user-facing adapters (HTTP API, OneBot/NapCat, etc.). Default port `8081`. This is the port you expose externally.

```
User / Bot client
     │
     ▼
[Gateway :8081]
     │  WebSocket Bridge (ws://localhost:8000/internal/gateway/ws)
     ▼
[Business :8000]
     │
     ▼
[LLM API / tools / DB]
```

---

## 1. Prerequisites

- Python 3.10 or newer
- (Optional) Docker + Docker Compose for containerized deployment
- (Optional) Node.js 18+ to build the web UI

---

## 2. Configuration

Both processes read from **one shared env file**: `config/fairyclaw.env`.

```bash
cp config/fairyclaw.env.example config/fairyclaw.env
# Then edit config/fairyclaw.env to set required values
```

Also copy the LLM endpoints config:

```bash
cp config/llm_endpoints.yaml.example config/llm_endpoints.yaml
# Edit to point at your LLM provider
```

### 2.1 Required settings (must change before production)

| Variable | Description |
|---|---|
| `FAIRYCLAW_API_TOKEN` | Bearer token for the Gateway's HTTP API. Replace the placeholder with a strong random string. |
| `FAIRYCLAW_BRIDGE_TOKEN` | Shared secret between Business and Gateway. Must match on both sides. |

Generate strong tokens with:

```bash
openssl rand -hex 32
```

### 2.2 Business settings

| Variable | Default | Description |
|---|---|---|
| `FAIRYCLAW_HOST` | `0.0.0.0` | Business bind address. |
| `FAIRYCLAW_PORT` | `8000` | Business port. Keep internal — do not expose directly. |
| `FAIRYCLAW_DATABASE_URL` | `sqlite+aiosqlite:///./data/fairyclaw.db` | SQLite default. Set a PostgreSQL URL for production. |
| `FAIRYCLAW_DATA_DIR` | `./data` | Directory for DB, logs, and uploaded files. |
| `FAIRYCLAW_LLM_ENDPOINTS_CONFIG_PATH` | `./config/llm_endpoints.yaml` | Path to LLM provider config. |
| `FAIRYCLAW_FILESYSTEM_ROOT_DIR` | *(unset)* | Root directory that file-system tools can access. Required when using `fs_read`, `fs_write`, etc. |
| `FAIRYCLAW_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `FAIRYCLAW_LOG_FILE_PATH` | `./data/logs/fairyclaw.log` | Log file path. |
| `FAIRYCLAW_LOG_TO_STDOUT` | `false` | Also print logs to stdout (useful for Docker). |
| `FAIRYCLAW_ENABLE_HOOK_RUNTIME` | `false` | Enable the five-stage Hook pipeline for capabilities. |
| `FAIRYCLAW_CONTEXT_TOKEN_BUDGET` | `0` | Token budget for context compression hooks (0 = disabled). |
| `FAIRYCLAW_BRIDGE_WS_PATH` | `/internal/gateway/ws` | WebSocket path Business listens on for Gateway connections. |

### 2.3 Gateway settings

| Variable | Default | Description |
|---|---|---|
| `FAIRYCLAW_GATEWAY_HOST` | `0.0.0.0` | Gateway bind address. |
| `FAIRYCLAW_GATEWAY_PORT` | `8081` | Gateway port. Expose this to users / reverse proxy. |
| `FAIRYCLAW_GATEWAY_ID` | `gw_local` | Unique identifier for this Gateway instance. |
| `FAIRYCLAW_GATEWAY_BRIDGE_URL` | `ws://127.0.0.1:8000/internal/gateway/ws` | URL Gateway uses to connect to Business. |

### 2.4 OneBot adapter settings

These variables are read by the Gateway process. They can live in `config/fairyclaw.env` (loaded via Pydantic Settings) or be exported as normal environment variables before launching:

| Variable | Default | Description |
|---|---|---|
| `ONEBOT_API_BASE` | `http://localhost:3000` | Base URL of your OneBot implementation's HTTP API. |
| `ONEBOT_ACCESS_TOKEN` | *(empty)* | Access token for the OneBot HTTP API (optional). |
| `ONEBOT_ALLOWED_USER` | *(empty)* | If set, only accept private messages from this QQ user ID. |
| `ONEBOT_SESSION_CMD_PREFIX` | `/sess` | Command prefix for session management commands. |

---

## 3. Python (virtualenv) Deployment

```bash
cd /opt/fairyclaw
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Start Business (keep this in the background or managed by systemd):

```bash
uvicorn fairyclaw.main:app --host 0.0.0.0 --port 8000
```

Start Gateway in a second terminal:

```bash
uvicorn fairyclaw.gateway.main:app --host 0.0.0.0 --port 8081
```

### 3.1 systemd units (recommended for production)

**`/etc/systemd/system/fairyclaw-business.service`:**

```ini
[Unit]
Description=FairyClaw Business
After=network.target

[Service]
User=fairyclaw
WorkingDirectory=/opt/fairyclaw
EnvironmentFile=/opt/fairyclaw/config/fairyclaw.env
ExecStart=/opt/fairyclaw/.venv/bin/uvicorn fairyclaw.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/fairyclaw-gateway.service`:**

```ini
[Unit]
Description=FairyClaw Gateway
After=network.target fairyclaw-business.service

[Service]
User=fairyclaw
WorkingDirectory=/opt/fairyclaw
EnvironmentFile=/opt/fairyclaw/config/fairyclaw.env
ExecStart=/opt/fairyclaw/.venv/bin/uvicorn fairyclaw.gateway.main:app --host 0.0.0.0 --port 8081
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now fairyclaw-business fairyclaw-gateway
```

---

## 4. Docker Compose Deployment

```bash
cd /opt/fairyclaw
cp config/fairyclaw.env.example config/fairyclaw.env
# Edit config/fairyclaw.env

docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
```

The Compose file starts the Business process. To run the Gateway in a second container, add a second service pointing at the same image with `CMD ["uvicorn", "fairyclaw.gateway.main:app", "--host", "0.0.0.0", "--port", "8081"]` and the appropriate port mapping.

**Note on proxy variables:** Docker may inject proxy environment variables that break the internal WebSocket bridge. Unset them for the Business container if you see `HTTP 404` on bridge connect:

```bash
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
```

---

## 5. User-Facing Frontend Options

FairyClaw supports two ways for users to interact with the agent.

### 5.1 Built-in Web UI

The `web/` directory contains a React/TypeScript SPA that talks directly to the Gateway HTTP API.

**Build:**

```bash
cd web
cp .env.example .env
# Edit .env: set VITE_GATEWAY_BASE_URL and VITE_API_TOKEN
npm install
npm run build
```

The built assets go to `web/dist/`. The Gateway process automatically mounts them at `/app` when `web/dist/` exists, so navigating to `http://your-host:8081/app` shows the UI.

**Key `web/.env` variables:**

| Variable | Example | Description |
|---|---|---|
| `VITE_GATEWAY_BASE_URL` | `http://127.0.0.1:8081` | Gateway base URL. |
| `VITE_API_TOKEN` | *(strong token)* | Bearer token for API requests. Avoid hardcoding in production. |

### 5.2 OneBot / IM Clients (recommended for personal use)

FairyClaw's Gateway includes a OneBot v11 adapter that works with any compliant bot framework. The **recommended implementation is [NapCat](https://github.com/NapNeko/NapCat)**, a modern headless QQ client with full OneBot v11 support.

**Quick setup with NapCat:**

1. Install and configure NapCat following its documentation.
2. In NapCat, enable the HTTP callback (event push) to point at the Gateway:
   ```
   POST http://<your-host>:8081/onebot/event
   ```
3. Note the NapCat HTTP API base URL and access token.
4. Set in `config/fairyclaw.env`:
   ```env
   ONEBOT_API_BASE=http://127.0.0.1:3000   # NapCat's HTTP API
   ONEBOT_ACCESS_TOKEN=your_napcat_token
   ONEBOT_ALLOWED_USER=your_qq_number      # optional: restrict to yourself
   ```
5. Restart the Gateway process.

**Session management commands** (sent as chat messages, prefix configurable via `ONEBOT_SESSION_CMD_PREFIX`):

| Command | Description |
|---|---|
| `/sess new [title]` | Create and switch to a new session. |
| `/sess ls` | List your sessions. |
| `/sess checkout <id or title>` | Switch active session. |
| `/sess co <...>` | Alias for `checkout`. |
| `/sess rm <id or title>` | Permanently delete a session. |

---

## 6. Reverse Proxy (nginx)

Expose the Gateway publicly with TLS using nginx:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.example.com;

    ssl_certificate     /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

Keep the Business port (`8000`) behind the firewall — only the Gateway needs to reach it.
