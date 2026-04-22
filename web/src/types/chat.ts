export type Segment =
  | { type: 'text'; content: string }
  | { type: 'file'; file_id: string; mime_type?: string }

export type SessionEventMessage =
  | {
      session_id: string
      kind: 'text' | 'file'
      content: {
        text?: string
        file_id?: string
        filename?: string | null
        mime_type?: string | null
      }
      meta: Record<string, unknown>
    }
  | {
      session_id: string
      kind: 'event'
      content: {
        event_type: string
        tool_call_id?: string
        tool_name?: string
        arguments?: Record<string, unknown>
        ok?: boolean
        result?: unknown
        error_message?: string | null
        duration_ms?: number | null
        [key: string]: unknown
      }
      meta: Record<string, unknown>
    }

export type UploadResult = {
  file_id: string
  filename: string
  size: number
  created_at: number
}

export type SessionResult = {
  session_id: string
  title: string | null
  created_at: number
}

export type ChatResult = {
  status: string
  message: string
}

export type PendingFileRef = {
  fileId: string
  filename: string
}

export type LogEntry =
  | { id: string; role: 'system'; text: string; sessionId: string; ts: number }
  | {
      id: string
      role: 'system'
      kind: 'timer_tick'
      jobId: string
      mode: string
      runIndex: number
      payload: string
      sessionId: string
      ts: number
    }
  | {
      id: string
      role: 'system'
      kind: 'tool_call'
      toolCallId: string
      toolName: string
      argumentsJson: string
      sessionId: string
      ts: number
    }
  | {
      id: string
      role: 'system'
      kind: 'tool_result'
      toolCallId: string
      toolName: string
      ok: boolean
      detail: string
      sessionId: string
      ts: number
    }
  | { id: string; role: 'user'; text: string; files: PendingFileRef[]; sessionId: string; ts: number }
  | { id: string; role: 'assistant'; text?: string; file?: PendingFileRef; sessionId: string; ts: number }

export type WsConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error' | 'closed'

export type TelemetrySnapshotView = {
  heartbeatStatus?: string
  serverTimeMs?: number
  reinsEnabled?: boolean | null
}

export type SessionUsageView = {
  sessionTokensUsed: number
  sessionPromptTokensUsed: number
  sessionCompletionTokensUsed: number
  monthTokensUsed: number
  monthPromptTokensUsed: number
  monthCompletionTokensUsed: number
}

export type ServerSessionRow = {
  session_id: string
  title: string | null
  created_at: number
  last_activity_at: number
  event_count: number
}

export type SubagentTaskRow = {
  task_id: string
  parent_session_id: string
  label: string
  status: string
  status_display?: string
  task_type?: string
  instruction?: string
  updated_at_ms: number
  child_session_id?: string | null
  event_count?: number
  last_event_at_ms?: number
}
