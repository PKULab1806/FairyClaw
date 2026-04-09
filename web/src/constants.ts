/** Fallback when `window` is unavailable (e.g. tests). */
export const DEFAULT_BASE_URL = import.meta.env.VITE_GATEWAY_BASE_URL || 'http://127.0.0.1:8000'
export const DEFAULT_API_TOKEN = import.meta.env.VITE_API_TOKEN || ''
export const STORAGE_KEY = 'fairyclaw-web-ui-state'
export const SESSION_HISTORY_LIMIT = 12
export const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'FairyClaw'
export const APP_VERSION = import.meta.env.VITE_APP_VERSION || '0.1.7'

/** Whitelist subset matching `fairyclaw.env` lines 1–24 (`SYSTEM_ENV_WHITELIST`), excluding API token (server/build only). */
export const SYSTEM_ENV_UI_KEYS: readonly string[] = [
  'FAIRYCLAW_DATABASE_URL',
  'FAIRYCLAW_DATA_DIR',
  'FAIRYCLAW_HOST',
  'FAIRYCLAW_PORT',
  'FAIRYCLAW_LLM_ENDPOINTS_CONFIG_PATH',
  'FAIRYCLAW_FILESYSTEM_ROOT_DIR',
  'FAIRYCLAW_LOG_LEVEL',
  'FAIRYCLAW_LOG_FILE_PATH',
  'FAIRYCLAW_LOG_TO_STDOUT',
  'FAIRYCLAW_CAPABILITIES_DIR',
  'FAIRYCLAW_EVENT_BUS_WORKER_COUNT',
  'FAIRYCLAW_PLANNER_HEARTBEAT_SECONDS',
  'FAIRYCLAW_PLANNER_WAKEUP_DEBOUNCE_MS',
  'FAIRYCLAW_ROUTER_PROFILE_NAME',
  'FAIRYCLAW_HOOK_DEFAULT_TIMEOUT_MS',
  'FAIRYCLAW_ENABLE_HOOK_RUNTIME',
  'FAIRYCLAW_ENABLE_RAG_PIPELINE',
  'FAIRYCLAW_REINS_ENABLED',
  'FAIRYCLAW_REINS_BUDGET_DAILY_USD',
  'FAIRYCLAW_REINS_ON_EXCEED',
]

export function gatewayApiBaseUrl(raw: string): string {
  let s = raw.trim().replace(/\/+$/, '')
  if (s.endsWith('/app')) {
    s = s.slice(0, -4)
  }
  s = s.replace(/\/+$/, '')
  return s || gatewayApiBaseUrl(DEFAULT_BASE_URL)
}

/** Prefer same-origin (gateway serves UI); optional `VITE_GATEWAY_BASE_URL` override for dev. */
export function inferGatewayBaseUrl(): string {
  const v = import.meta.env.VITE_GATEWAY_BASE_URL
  if (typeof v === 'string' && v.trim()) {
    return gatewayApiBaseUrl(v)
  }
  if (typeof window !== 'undefined') {
    return gatewayApiBaseUrl(window.location.origin)
  }
  return gatewayApiBaseUrl(DEFAULT_BASE_URL)
}

/** Token is internal alignment only — set `VITE_API_TOKEN` at build time to match `FAIRYCLAW_API_TOKEN`. */
export function effectiveApiToken(): string {
  return DEFAULT_API_TOKEN.trim()
}

export function makeId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`
}

/** Unified web gateway WebSocket (token query); no per-session URL. */
export function toWebGatewayWsUrl(baseUrl: string, token: string): string {
  const url = new URL(gatewayApiBaseUrl(baseUrl))
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = '/v1/ws'
  url.searchParams.set('token', token)
  return url.toString()
}
