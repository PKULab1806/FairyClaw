import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import {
  SESSION_HISTORY_LIMIT,
  STORAGE_KEY,
  effectiveApiToken,
  inferGatewayBaseUrl,
  makeId,
  toWebGatewayWsUrl,
} from '../constants'
import { useLocale } from './LocaleContext'
import type {
  LogEntry,
  PendingFileRef,
  ServerSessionRow,
  SessionEventMessage,
  SessionUsageView,
  SubagentTaskRow,
  TelemetrySnapshotView,
  UploadResult,
  WsConnectionState,
} from '../types/chat'

type GatewayContextValue = {
  /** Resolved gateway HTTP origin (same as WebSocket host). Read-only. */
  gatewayBaseUrl: string
  /** True when `VITE_API_TOKEN` is set (required for WS). */
  hasConfiguredToken: boolean
  sessionTitle: string
  setSessionTitle: (v: string) => void
  sessionId: string
  setSessionId: (v: string) => void
  recentSessions: string[]
  setRecentSessions: React.Dispatch<React.SetStateAction<string[]>>
  messageText: string
  setMessageText: (v: string) => void
  pendingFilesBySession: Record<string, PendingFileRef[]>
  setPendingFilesBySession: React.Dispatch<React.SetStateAction<Record<string, PendingFileRef[]>>>
  messagesBySession: Record<string, LogEntry[]>
  setMessagesBySession: React.Dispatch<React.SetStateAction<Record<string, LogEntry[]>>>
  wsState: WsConnectionState
  uploadError: string
  setUploadError: (v: string) => void
  isCreatingSession: boolean
  isSendingMessage: boolean
  isWaitingAssistant: boolean
  isUploadingFile: boolean
  canSendMessage: boolean
  authHeaders: () => HeadersInit
  downloadSessionFile: (targetSessionId: string, fileId: string, filename: string) => Promise<void>
  createSession: () => Promise<boolean>
  sendMessage: () => Promise<void>
  uploadFile: (file: File) => Promise<void>
  removePendingFile: (fileId: string) => void
  telemetry: TelemetrySnapshotView | null
  sessionUsage: SessionUsageView | null
  subagentTasksBySession: Record<string, SubagentTaskRow[]>
  serverSessions: ServerSessionRow[]
  loadServerSessions: () => Promise<void>
  sendWsOp: (op: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>
  restoreSession: (sessionId: string) => Promise<void>
  selectedSubtaskChildId: string | null
  selectedSubtaskLabel: string | null
  subtaskLogsByChildId: Record<string, LogEntry[]>
  selectSubtaskMonitor: (childSessionId: string, label?: string) => void
  clearSubtaskMonitor: () => void
}

const GatewayContext = createContext<GatewayContextValue | null>(null)

type WsAckBody = Record<string, unknown>

const SYSTEM_NOTIFICATION_PREFIX = '[System Notification]'
const TIMER_TICK_PREFIX = '[TIMER_TICK]'
const TIMER_PAYLOAD_PREFIX = '[TIMER_PAYLOAD]'

function isSystemNotificationText(text: string): boolean {
  return text.trimStart().startsWith(SYSTEM_NOTIFICATION_PREFIX)
}

function parseTimerTickText(text: string): { mode: string; jobId: string; runIndex: number; payload: string } | null {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length === 0 || !lines[0].startsWith(TIMER_TICK_PREFIX)) {
    return null
  }
  const head = lines[0]
  const modeMatch = head.match(/\bmode=([^\s]+)/)
  const jobIdMatch = head.match(/\bjob_id=([^\s]+)/)
  const runIndexMatch = head.match(/\brun_index=(\d+)/)
  const payloadLine = lines.find((line) => line.startsWith(TIMER_PAYLOAD_PREFIX))
  const payload = payloadLine ? payloadLine.slice(TIMER_PAYLOAD_PREFIX.length).trim() : ''
  return {
    mode: modeMatch ? modeMatch[1] : 'heartbeat',
    jobId: jobIdMatch ? jobIdMatch[1] : '',
    runIndex: runIndexMatch ? Number.parseInt(runIndexMatch[1], 10) || 1 : 1,
    payload,
  }
}

function mapHistoryToLogEntries(sessionId: string, events: unknown[]): LogEntry[] {
  const out: LogEntry[] = []
  let idx = 0
  for (const raw of events) {
    if (!raw || typeof raw !== 'object') {
      continue
    }
    const e = raw as Record<string, unknown>
    const kind = String(e.kind || '')
    const ts = typeof e.ts_ms === 'number' ? e.ts_ms : Date.now()
    if (kind === 'session_event') {
      const roleRaw = String(e.role || 'assistant')
      const text = String(e.text || '')
      const r =
        roleRaw === 'user' || roleRaw === 'assistant' || roleRaw === 'system' ? roleRaw : 'assistant'
      if (isSystemNotificationText(text)) {
        idx++
        continue
      }
      const timerTick = parseTimerTickText(text)
      if (timerTick != null) {
        out.push({
          id: makeId(`hist_${idx}`),
          role: 'system',
          kind: 'timer_tick',
          jobId: timerTick.jobId,
          mode: timerTick.mode,
          runIndex: timerTick.runIndex,
          payload: timerTick.payload,
          sessionId,
          ts,
        })
      } else if (r === 'user') {
        out.push({
          id: makeId(`hist_${idx}`),
          role: 'user',
          text,
          files: [],
          sessionId,
          ts,
        })
      } else if (r === 'assistant') {
        out.push({
          id: makeId(`hist_${idx}`),
          role: 'assistant',
          text,
          sessionId,
          ts,
        })
      } else {
        out.push({
          id: makeId(`hist_${idx}`),
          role: 'system',
          text,
          sessionId,
          ts,
        })
      }
    } else if (kind === 'operation_event') {
      const toolName = String(e.tool_name || 'tool')
      const detail = e.result_preview != null ? String(e.result_preview) : ''
      out.push({
        id: makeId(`hist_${idx}`),
        role: 'system',
        kind: 'tool_result',
        toolCallId: `hist_${idx}`,
        toolName,
        ok: true,
        detail,
        sessionId,
        ts,
      })
    }
    idx++
  }
  return out
}

export function GatewayProvider({ children }: { children: ReactNode }) {
  const { t } = useLocale()
  const tRef = useRef(t)
  tRef.current = t

  const gatewayBaseUrl = useMemo(() => inferGatewayBaseUrl(), [])
  const hasConfiguredToken = useMemo(() => Boolean(effectiveApiToken()), [])
  const [sessionTitle, setSessionTitle] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [recentSessions, setRecentSessions] = useState<string[]>([])
  const [messageText, setMessageText] = useState('')
  const [pendingFilesBySession, setPendingFilesBySession] = useState<Record<string, PendingFileRef[]>>({})
  const [messagesBySession, setMessagesBySession] = useState<Record<string, LogEntry[]>>({})
  const [wsState, setWsState] = useState<WsConnectionState>('disconnected')
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const [isSendingMessage, setIsSendingMessage] = useState(false)
  const [waitingAssistantBySession, setWaitingAssistantBySession] = useState<Record<string, boolean>>({})
  const [isUploadingFile, setIsUploadingFile] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [telemetry, setTelemetry] = useState<TelemetrySnapshotView | null>(null)
  const [sessionUsage, setSessionUsage] = useState<SessionUsageView | null>(null)
  const [subagentTasksBySession, setSubagentTasksBySession] = useState<Record<string, SubagentTaskRow[]>>({})
  const [serverSessions, setServerSessions] = useState<ServerSessionRow[]>([])
  const [selectedSubtaskChildId, setSelectedSubtaskChildId] = useState<string | null>(null)
  const [selectedSubtaskLabel, setSelectedSubtaskLabel] = useState<string | null>(null)
  const [subtaskLogsByChildId, setSubtaskLogsByChildId] = useState<Record<string, LogEntry[]>>({})

  const wsRef = useRef<WebSocket | null>(null)
  const pendingRef = useRef(
    new Map<string, { resolve: (v: WsAckBody) => void; reject: (e: Error) => void }>(),
  )
  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId
  const waitingTimeoutRef = useRef<Map<string, number>>(new Map())
  const restoredSessionRef = useRef<string>('')

  const stopWaitingAssistant = useCallback((sid: string) => {
    const timer = waitingTimeoutRef.current.get(sid)
    if (timer != null) {
      window.clearTimeout(timer)
      waitingTimeoutRef.current.delete(sid)
    }
    setWaitingAssistantBySession((prev) => {
      if (!prev[sid]) {
        return prev
      }
      return { ...prev, [sid]: false }
    })
  }, [])

  const startWaitingAssistant = useCallback((sid: string) => {
    stopWaitingAssistant(sid)
    setWaitingAssistantBySession((prev) => ({ ...prev, [sid]: true }))
    const timer = window.setTimeout(() => {
      stopWaitingAssistant(sid)
    }, 90_000)
    waitingTimeoutRef.current.set(sid, timer)
  }, [stopWaitingAssistant])

  const canSendMessage = useMemo(() => {
    const currentPendingFiles = sessionId.trim() ? (pendingFilesBySession[sessionId] || []) : []
    return Boolean(
      wsState === 'connected' &&
        (messageText.trim() || currentPendingFiles.length > 0),
    )
  }, [messageText, pendingFilesBySession, sessionId, wsState])

  const selectSubtaskMonitor = useCallback((childSessionId: string, label?: string) => {
    const target = childSessionId.trim()
    if (!target) {
      return
    }
    setSelectedSubtaskChildId(target)
    setSelectedSubtaskLabel(label?.trim() || null)
  }, [])

  const clearSubtaskMonitor = useCallback(() => {
    setSelectedSubtaskChildId(null)
    setSelectedSubtaskLabel(null)
  }, [])

  const handlePush = useCallback((raw: unknown) => {
    if (!raw || typeof raw !== 'object') {
      return
    }
    const payload = raw as SessionEventMessage & { kind?: string }
    const now = Date.now()
    const targetSession = payload.session_id || sessionIdRef.current
    const isMainSessionPush = targetSession === sessionIdRef.current
    const appendEntry = (entry: LogEntry) => {
      if (isMainSessionPush) {
        setMessagesBySession((current) => ({
          ...current,
          [targetSession]: [...(current[targetSession] || []), entry],
        }))
        return
      }
      setSubtaskLogsByChildId((current) => ({
        ...current,
        [targetSession]: [...(current[targetSession] || []), entry],
      }))
    }
    try {
      if (payload.kind === 'text' && payload.content?.text) {
        const text = payload.content.text
        if (isSystemNotificationText(text)) {
          return
        }
        const entry: LogEntry = {
          id: makeId('assistant'),
          role: 'assistant',
          text,
          sessionId: targetSession,
          ts: now,
        }
        appendEntry(entry)
        if (isMainSessionPush) {
          stopWaitingAssistant(targetSession)
        }
        return
      }
      if (payload.kind === 'file') {
        const mainSid = sessionIdRef.current.trim()
        const parentFromMeta =
          payload.meta && typeof payload.meta.fc_parent_session_id === 'string'
            ? String(payload.meta.fc_parent_session_id).trim()
            : ''
        const mirrorFileToMain =
          Boolean(mainSid) && !isMainSessionPush && parentFromMeta === mainSid

        const appendMirroredToMain = (entry: LogEntry) => {
          if (!mirrorFileToMain) {
            return
          }
          setMessagesBySession((current) => ({
            ...current,
            [mainSid]: [...(current[mainSid] || []), { ...entry, id: makeId('mirror') }],
          }))
        }

        const fileId = payload.content?.file_id || (payload.meta?.file_id as string | undefined)
        if (!fileId) {
          const missing: LogEntry = {
            id: makeId('system'),
            role: 'system',
            text: tRef.current('msg.fileMissingId'),
            sessionId: targetSession,
            ts: now,
          }
          appendEntry(missing)
          appendMirroredToMain(missing)
          return
        }
        const filename = payload.content?.filename || fileId
        const fileEntry: LogEntry = {
          id: makeId('assistant'),
          role: 'assistant',
          file: { fileId, filename },
          sessionId: targetSession,
          ts: now,
        }
        appendEntry(fileEntry)
        appendMirroredToMain(fileEntry)
        if (isMainSessionPush) {
          stopWaitingAssistant(targetSession)
        }
        return
      }
      if (payload.kind === 'event') {
        const et = payload.content?.event_type
        if (et === 'tool_call') {
          const toolCallId = String(payload.content?.tool_call_id || '')
          const toolName = String(payload.content?.tool_name || '')
          const args = payload.content?.arguments
          const argumentsJson =
            args && typeof args === 'object' ? JSON.stringify(args, null, 2) : '{}'
          appendEntry({
            id: makeId('tool'),
            role: 'system',
            kind: 'tool_call',
            toolCallId,
            toolName,
            argumentsJson,
            sessionId: targetSession,
            ts: now,
          })
          return
        }
        if (et === 'tool_result') {
          const toolCallId = String(payload.content?.tool_call_id || '')
          const toolName = String(payload.content?.tool_name || '')
          const ok = Boolean(payload.content?.ok)
          let detail = ''
          if (ok) {
            const r = payload.content?.result
            detail = typeof r === 'string' ? r : JSON.stringify(r ?? '', null, 2)
          } else {
            detail = String(payload.content?.error_message || '')
          }
          appendEntry({
            id: makeId('tool'),
            role: 'system',
            kind: 'tool_result',
            toolCallId,
            toolName,
            ok,
            detail,
            sessionId: targetSession,
            ts: now,
          })
          return
        }
        if (et === 'timer_tick') {
          appendEntry({
            id: makeId('timer'),
            role: 'system',
            kind: 'timer_tick',
            jobId: String(payload.content?.job_id || ''),
            mode: String(payload.content?.mode || 'heartbeat'),
            runIndex: Number(payload.content?.run_index || 1),
            payload: String(payload.content?.payload || ''),
            sessionId: targetSession,
            ts: now,
          })
          return
        }
        if (et === 'telemetry') {
          const hb = payload.content?.heartbeat as Record<string, unknown> | undefined
          setTelemetry({
            heartbeatStatus: typeof hb?.status === 'string' ? hb.status : undefined,
            serverTimeMs: typeof hb?.server_time_ms === 'number' ? hb.server_time_ms : undefined,
            reinsEnabled:
              typeof payload.content?.reins_enabled === 'boolean'
                ? payload.content.reins_enabled
                : null,
          })
          return
        }
        if (et === 'subagent_tasks') {
          const rawTasks = payload.content?.tasks
          const parentFromMeta =
            payload.meta &&
            typeof payload.meta.fc_parent_session_id === 'string'
              ? payload.meta.fc_parent_session_id
              : null
          const parent = String(parentFromMeta || payload.session_id || sessionIdRef.current)
          if (!Array.isArray(rawTasks)) {
            return
          }
          const parsed: SubagentTaskRow[] = []
          for (const row of rawTasks) {
            if (!row || typeof row !== 'object') {
              continue
            }
            const o = row as Record<string, unknown>
            const taskId = String(o.task_id || '')
            if (!taskId) {
              continue
            }
            const child = o.child_session_id
            parsed.push({
              task_id: taskId,
              parent_session_id: String(o.parent_session_id || parent),
              label: String(o.label || ''),
              status: String(o.status || ''),
              status_display:
                typeof o.status_display === 'string' ? o.status_display : undefined,
              task_type:
                typeof o.task_type === 'string' ? o.task_type : undefined,
              instruction:
                typeof o.instruction === 'string' ? o.instruction : undefined,
              updated_at_ms: typeof o.updated_at_ms === 'number' ? o.updated_at_ms : now,
              child_session_id: child != null ? String(child) : null,
              event_count: typeof o.event_count === 'number' ? o.event_count : undefined,
              last_event_at_ms:
                typeof o.last_event_at_ms === 'number' ? o.last_event_at_ms : undefined,
            })
          }
          setSubagentTasksBySession((prev) => ({ ...prev, [parent]: parsed }))
          return
        }
      }
    } catch {
      // ignore
    }
  }, [stopWaitingAssistant])

  const handlePushRef = useRef(handlePush)
  handlePushRef.current = handlePush

  useEffect(() => {
    return () => {
      for (const timer of waitingTimeoutRef.current.values()) {
        window.clearTimeout(timer)
      }
      waitingTimeoutRef.current.clear()
    }
  }, [])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (!raw) {
        return
      }
      const parsed = JSON.parse(raw) as {
        sessionId?: string
        recentSessions?: string[]
        sessionTitle?: string
      }
      if (typeof parsed.sessionId === 'string') {
        setSessionId(parsed.sessionId)
      }
      if (Array.isArray(parsed.recentSessions)) {
        setRecentSessions(parsed.recentSessions.filter((item): item is string => typeof item === 'string'))
      }
      if (typeof parsed.sessionTitle === 'string') {
        setSessionTitle(parsed.sessionTitle)
      }
    } catch {
      // Ignore malformed browser state and fall back to defaults.
    }
  }, [])

  useEffect(() => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sessionId,
        recentSessions,
        sessionTitle,
      }),
    )
  }, [recentSessions, sessionId, sessionTitle])

  useEffect(() => {
    const token = effectiveApiToken()
    if (!token) {
      setWsState('disconnected')
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      return
    }

    const url = toWebGatewayWsUrl(gatewayBaseUrl, token)
    const ws = new WebSocket(url)
    wsRef.current = ws
    setWsState('connecting')

    ws.onopen = () => {
      if (wsRef.current !== ws) {
        return
      }
      setWsState('connected')
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(String(event.data)) as {
          op?: string
          id?: string
          body?: unknown
          message?: string
          ok?: boolean
        }
        if (msg.op === 'ack' && msg.id != null) {
          const p = pendingRef.current.get(msg.id)
          if (p) {
            pendingRef.current.delete(msg.id)
            const b =
              msg.body && typeof msg.body === 'object' ? (msg.body as WsAckBody) : ({} as WsAckBody)
            p.resolve(b)
          }
          return
        }
        if (msg.op === 'error' && msg.id != null) {
          const p = pendingRef.current.get(msg.id)
          if (p) {
            pendingRef.current.delete(msg.id)
            p.reject(new Error(String(msg.message || 'error')))
          }
          return
        }
        if (msg.op === 'push' && msg.body && typeof msg.body === 'object') {
          handlePushRef.current(msg.body as SessionEventMessage)
        }
      } catch {
        // ignore
      }
    }
    ws.onerror = () => {
      if (wsRef.current !== ws) {
        return
      }
      setWsState('error')
    }
    ws.onclose = () => {
      if (wsRef.current !== ws) {
        return
      }
      setWsState('closed')
    }

    return () => {
      ws.close()
      if (wsRef.current === ws) {
        wsRef.current = null
      }
    }
  }, [gatewayBaseUrl, hasConfiguredToken])

  const sendOp = useCallback((op: string, body: Record<string, unknown>): Promise<WsAckBody> => {
    return new Promise((resolve, reject) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'))
        return
      }
      const id = makeId('req')
      pendingRef.current.set(id, { resolve, reject })
      ws.send(JSON.stringify({ op, id, body }))
    })
  }, [])

  const loadSessionUsage = useCallback(
    async (sid: string) => {
      const target = sid.trim()
      if (!target || wsState !== 'connected') {
        return
      }
      try {
        const body = await sendOp('sessions.usage', { session_id: target })
        setSessionUsage({
          sessionTokensUsed:
            typeof body.session_tokens_used === 'number' ? body.session_tokens_used : 0,
          sessionPromptTokensUsed:
            typeof body.session_prompt_tokens_used === 'number' ? body.session_prompt_tokens_used : 0,
          sessionCompletionTokensUsed:
            typeof body.session_completion_tokens_used === 'number'
              ? body.session_completion_tokens_used
              : 0,
          monthTokensUsed: typeof body.month_tokens_used === 'number' ? body.month_tokens_used : 0,
          monthPromptTokensUsed:
            typeof body.month_prompt_tokens_used === 'number' ? body.month_prompt_tokens_used : 0,
          monthCompletionTokensUsed:
            typeof body.month_completion_tokens_used === 'number'
              ? body.month_completion_tokens_used
              : 0,
        })
      } catch {
        // ignore usage refresh failures
      }
    },
    [sendOp, wsState],
  )

  const loadSubagentTasks = useCallback(
    async (sid: string) => {
      const target = sid.trim()
      if (!target || wsState !== 'connected') {
        return
      }
      try {
        const body = await sendOp('sessions.subagent_tasks', { session_id: target })
        const rawTasks = body.tasks
        if (!Array.isArray(rawTasks)) {
          setSubagentTasksBySession((prev) => ({ ...prev, [target]: [] }))
          return
        }
        const now = Date.now()
        const parsed: SubagentTaskRow[] = []
        for (const row of rawTasks) {
          if (!row || typeof row !== 'object') {
            continue
          }
          const o = row as Record<string, unknown>
          const taskId = String(o.task_id || '')
          if (!taskId) {
            continue
          }
          const child = o.child_session_id
          parsed.push({
            task_id: taskId,
            parent_session_id: String(o.parent_session_id || target),
            label: String(o.label || ''),
            status: String(o.status || ''),
            status_display:
              typeof o.status_display === 'string' ? o.status_display : undefined,
            task_type:
              typeof o.task_type === 'string' ? o.task_type : undefined,
            instruction:
              typeof o.instruction === 'string' ? o.instruction : undefined,
            updated_at_ms: typeof o.updated_at_ms === 'number' ? o.updated_at_ms : now,
            child_session_id: child != null ? String(child) : null,
            event_count: typeof o.event_count === 'number' ? o.event_count : undefined,
            last_event_at_ms:
              typeof o.last_event_at_ms === 'number' ? o.last_event_at_ms : undefined,
          })
        }
        setSubagentTasksBySession((prev) => ({ ...prev, [target]: parsed }))
      } catch {
        // ignore snapshot refresh failures
      }
    },
    [sendOp, wsState],
  )

  const loadServerSessions = useCallback(async () => {
    const body = await sendOp('sessions.list', {})
    const rows = body.sessions
    if (!Array.isArray(rows)) {
      setServerSessions([])
      return
    }
    setServerSessions(
      rows
        .map((item) => {
          if (!item || typeof item !== 'object') {
            return null
          }
          const r = item as Record<string, unknown>
          return {
            session_id: String(r.session_id || ''),
            title: typeof r.title === 'string' ? r.title : null,
            created_at: typeof r.created_at === 'number' ? r.created_at : 0,
            last_activity_at: typeof r.last_activity_at === 'number' ? r.last_activity_at : 0,
            event_count: typeof r.event_count === 'number' ? r.event_count : 0,
          }
        })
        .filter((x): x is ServerSessionRow => x !== null && Boolean(x.session_id)),
    )
  }, [sendOp])

  useEffect(() => {
    if (!sessionId.trim() || wsState !== 'connected') {
      return
    }
    void sendOp('session.bind', { session_id: sessionId }).catch(() => {
      // bind may fail if session not yet on server; ignore
    })
  }, [sessionId, sendOp, wsState])

  useEffect(() => {
    if (!sessionId.trim() || wsState !== 'connected') {
      return
    }
    void loadSessionUsage(sessionId)
    void loadSubagentTasks(sessionId)
  }, [loadSessionUsage, loadSubagentTasks, sessionId, wsState, messagesBySession[sessionId]?.length])

  useEffect(() => {
    clearSubtaskMonitor()
  }, [clearSubtaskMonitor, sessionId])

  useEffect(() => {
    const childId = selectedSubtaskChildId?.trim() || ''
    if (!childId || wsState !== 'connected') {
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const body = await sendOp('sessions.history', { session_id: childId, limit: 200 })
        const events = body.events
        const entries = Array.isArray(events) ? mapHistoryToLogEntries(childId, events) : []
        if (cancelled) {
          return
        }
        setSubtaskLogsByChildId((prev) => ({ ...prev, [childId]: entries }))
      } catch {
        // ignore child history refresh failures
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedSubtaskChildId, sendOp, wsState])

  const authHeaders = useCallback((): HeadersInit => {
    const token = effectiveApiToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }, [])

  const downloadSessionFile = useCallback(
    async (targetSessionId: string, fileId: string, filename: string): Promise<void> => {
      const token = effectiveApiToken()
      if (!token) {
        setUploadError(t('errors.downloadNeedToken'))
        return
      }
      setUploadError('')
      try {
        const body = await sendOp('file.download', { session_id: targetSessionId, file_id: fileId })
        const b64 = body.content_base64
        if (typeof b64 !== 'string') {
          throw new Error('missing content_base64')
        }
        const bin = atob(b64)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) {
          bytes[i] = bin.charCodeAt(i)
        }
        const blob = new Blob([bytes])
        const objectUrl = URL.createObjectURL(blob)
        const anchor = document.createElement('a')
        anchor.href = objectUrl
        anchor.download = filename || fileId
        anchor.rel = 'noreferrer'
        anchor.click()
        URL.revokeObjectURL(objectUrl)
      } catch (error) {
        setUploadError(
          t('errors.downloadFailed', {
            detail: error instanceof Error ? error.message : String(error),
          }),
        )
      }
    },
    [sendOp, t],
  )

  const createSession = useCallback(async (): Promise<boolean> => {
    if (wsState !== 'connected') {
      setUploadError('WebSocket not connected')
      return false
    }
    setIsCreatingSession(true)
    setUploadError('')
    try {
      const body = await sendOp('session.create', {
        platform: 'web',
        title: sessionTitle.trim() || t('sessions.defaultTitle'),
        meta: { source: 'web_ui' },
      })
      const sid = typeof body.session_id === 'string' ? body.session_id : ''
      if (!sid) {
        throw new Error('missing session_id')
      }
      const now = Date.now()
      setSessionId(sid)
      sessionIdRef.current = sid
      setRecentSessions((current) => {
        const next = [sid, ...current.filter((item) => item !== sid)]
        return next.slice(0, SESSION_HISTORY_LIMIT)
      })
      setMessagesBySession((current) => ({
        ...current,
        [sid]: [
          {
            id: makeId('system'),
            role: 'system',
            text: t('msg.sessionCreated', { id: sid }),
            sessionId: sid,
            ts: now,
          },
        ],
      }))
      setPendingFilesBySession((current) => ({
        ...current,
        [sid]: [],
      }))
      return true
    } catch (error) {
      setUploadError(
        t('errors.createFailed', {
          detail: error instanceof Error ? error.message : String(error),
        }),
      )
      return false
    } finally {
      setIsCreatingSession(false)
    }
  }, [sendOp, sessionTitle, t, wsState])

  const sendMessage = useCallback(async (): Promise<void> => {
    if (!canSendMessage) {
      return
    }
    let targetSessionId = sessionId.trim()
    if (!targetSessionId) {
      const created = await createSession()
      if (!created) {
        return
      }
      targetSessionId = sessionIdRef.current.trim()
      if (!targetSessionId) {
        setUploadError(t('errors.needSession'))
        return
      }
    }
    const trimmedText = messageText.trim()
    const pendingFiles = pendingFilesBySession[targetSessionId] || []
    const segments: { type: string; content?: string; file_id?: string }[] = []
    if (trimmedText) {
      segments.push({ type: 'text', content: trimmedText })
    }
    for (const file of pendingFiles) {
      segments.push({ type: 'file', file_id: file.fileId })
    }

    setIsSendingMessage(true)
    setUploadError('')
    try {
      await sendOp('chat.send', {
        session_id: targetSessionId,
        segments,
      })
      const now = Date.now()
      setMessagesBySession((current) => ({
        ...current,
        [targetSessionId]: [
          ...(current[targetSessionId] || []),
          {
            id: makeId('user'),
            role: 'user',
            text: trimmedText || t('msg.onlyFiles'),
            files: pendingFiles,
            sessionId: targetSessionId,
            ts: now,
          },
        ],
      }))
      setMessageText('')
      setPendingFilesBySession((current) => ({
        ...current,
        [targetSessionId]: [],
      }))
      if (trimmedText) {
        startWaitingAssistant(targetSessionId)
      }
    } catch (error) {
      setUploadError(
        t('errors.sendFailed', {
          detail: error instanceof Error ? error.message : String(error),
        }),
      )
    } finally {
      setIsSendingMessage(false)
    }
  }, [canSendMessage, createSession, messageText, pendingFilesBySession, sendOp, sessionId, startWaitingAssistant, t])

  const uploadFile = useCallback(
    async (file: File): Promise<void> => {
      if (!sessionId.trim()) {
        setUploadError(t('errors.needSession'))
        return
      }
      if (wsState !== 'connected') {
        setUploadError('WebSocket not connected')
        return
      }

      setIsUploadingFile(true)
      setUploadError('')
      try {
        const dataUrl = await new Promise<string>((resolve, reject) => {
          const fr = new FileReader()
          fr.onload = () => resolve(String(fr.result))
          fr.onerror = () => reject(new Error('read failed'))
          fr.readAsDataURL(file)
        })
        const comma = dataUrl.indexOf(',')
        const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl
        const body = (await sendOp('file.upload', {
          session_id: sessionId,
          filename: file.name,
          content_base64: b64,
          mime_type: file.type || undefined,
        })) as unknown as UploadResult
        const now = Date.now()
        setPendingFilesBySession((current) => ({
          ...current,
          [sessionId]: [
            ...(current[sessionId] || []),
            {
              fileId: body.file_id,
              filename: body.filename,
            },
          ],
        }))
        setMessagesBySession((current) => ({
          ...current,
          [sessionId]: [
            ...(current[sessionId] || []),
            {
              id: makeId('system'),
              role: 'system',
              text: t('msg.fileUploaded', { name: body.filename }),
              sessionId,
              ts: now,
            },
          ],
        }))
      } catch (error) {
        setUploadError(
          t('errors.uploadFailed', {
            detail: error instanceof Error ? error.message : String(error),
          }),
        )
      } finally {
        setIsUploadingFile(false)
      }
    },
    [sendOp, sessionId, t, wsState],
  )

  const removePendingFile = useCallback(
    (fileId: string) => {
      setPendingFilesBySession((current) => ({
        ...current,
        [sessionId]: (current[sessionId] || []).filter((item) => item.fileId !== fileId),
      }))
    },
    [sessionId],
  )

  const restoreSession = useCallback(
    async (sid: string) => {
      const trimmed = sid.trim()
      if (!trimmed) {
        return
      }
      if (wsState !== 'connected') {
        setUploadError(t('errors.wsNotConnected'))
        return
      }
      setUploadError('')
      setSessionId(trimmed)
      restoredSessionRef.current = trimmed
      try {
        await sendOp('session.bind', { session_id: trimmed })
      } catch {
        // bind may fail for stale ids; still try to show history
      }
      try {
        const body = await sendOp('sessions.history', { session_id: trimmed, limit: 200 })
        const events = body.events
        const entries = Array.isArray(events) ? mapHistoryToLogEntries(trimmed, events) : []
        setMessagesBySession((prev) => ({ ...prev, [trimmed]: entries }))
        setPendingFilesBySession((prev) => ({ ...prev, [trimmed]: [] }))
        setRecentSessions((cur) => {
          const next = [trimmed, ...cur.filter((x) => x !== trimmed)]
          return next.slice(0, SESSION_HISTORY_LIMIT)
        })
        void loadSubagentTasks(trimmed)
      } catch (error) {
        setUploadError(
          t('errors.historyFailed', {
            detail: error instanceof Error ? error.message : String(error),
          }),
        )
      }
    },
    [loadSubagentTasks, sendOp, t, wsState],
  )

  useEffect(() => {
    const sid = sessionId.trim()
    if (wsState !== 'connected' || !sid) {
      return
    }
    if (restoredSessionRef.current === sid) {
      return
    }
    restoredSessionRef.current = sid
    void restoreSession(sid)
  }, [restoreSession, sessionId, wsState])

  const value = useMemo(
    () => ({
      gatewayBaseUrl,
      hasConfiguredToken,
      sessionTitle,
      setSessionTitle,
      sessionId,
      setSessionId,
      recentSessions,
      setRecentSessions,
      messageText,
      setMessageText,
      pendingFilesBySession,
      setPendingFilesBySession,
      messagesBySession,
      setMessagesBySession,
      wsState,
      uploadError,
      setUploadError,
      isCreatingSession,
      isSendingMessage,
      isWaitingAssistant: Boolean(waitingAssistantBySession[sessionId]),
      isUploadingFile,
      canSendMessage,
      authHeaders,
      downloadSessionFile,
      createSession,
      sendMessage,
      uploadFile,
      removePendingFile,
      telemetry,
      sessionUsage,
      subagentTasksBySession,
      serverSessions,
      loadServerSessions,
      sendWsOp: sendOp,
      restoreSession,
      selectedSubtaskChildId,
      selectedSubtaskLabel,
      subtaskLogsByChildId,
      selectSubtaskMonitor,
      clearSubtaskMonitor,
    }),
    [
      authHeaders,
      canSendMessage,
      gatewayBaseUrl,
      hasConfiguredToken,
      createSession,
      downloadSessionFile,
      isCreatingSession,
      isSendingMessage,
      waitingAssistantBySession,
      isUploadingFile,
      messageText,
      messagesBySession,
      pendingFilesBySession,
      recentSessions,
      removePendingFile,
      sendMessage,
      sessionId,
      sessionTitle,
      uploadError,
      uploadFile,
      wsState,
      telemetry,
      sessionUsage,
      subagentTasksBySession,
      serverSessions,
      loadServerSessions,
      restoreSession,
      sendOp,
      selectedSubtaskChildId,
      selectedSubtaskLabel,
      subtaskLogsByChildId,
      selectSubtaskMonitor,
      clearSubtaskMonitor,
    ],
  )

  return <GatewayContext.Provider value={value}>{children}</GatewayContext.Provider>
}

export function useGateway(): GatewayContextValue {
  const ctx = useContext(GatewayContext)
  if (!ctx) {
    throw new Error('useGateway must be used within GatewayProvider')
  }
  return ctx
}
