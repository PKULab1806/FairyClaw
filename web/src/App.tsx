import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type Segment =
  | { type: 'text'; content: string }
  | { type: 'file'; file_id: string; mime_type?: string }

type SessionEventMessage = {
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

type UploadResult = {
  file_id: string
  filename: string
  size: number
  created_at: number
}

type SessionResult = {
  session_id: string
  title: string | null
  created_at: number
}

type ChatResult = {
  status: string
  message: string
}

type PendingFileRef = {
  fileId: string
  filename: string
}

type LogEntry =
  | { id: string; role: 'system'; text: string; sessionId: string }
  | { id: string; role: 'user'; text: string; files: PendingFileRef[]; sessionId: string }
  | { id: string; role: 'assistant'; text?: string; file?: PendingFileRef; sessionId: string }

const DEFAULT_BASE_URL = import.meta.env.VITE_GATEWAY_BASE_URL || 'http://127.0.0.1:8081'
const DEFAULT_API_TOKEN = import.meta.env.VITE_API_TOKEN || ''
const STORAGE_KEY = 'fairyclaw-web-ui-state'
const SESSION_HISTORY_LIMIT = 12

/** HTTP API lives at {origin}/v1/...; SPA may be served at /app — strip /app so /v1 is not under /app. */
function gatewayApiBaseUrl(raw: string): string {
  let s = raw.trim().replace(/\/+$/, '')
  if (s.endsWith('/app')) {
    s = s.slice(0, -4)
  }
  s = s.replace(/\/+$/, '')
  return s || DEFAULT_BASE_URL.replace(/\/+$/, '')
}

/** Prefer typed token; fall back to Vite env so builds with VITE_API_TOKEN work without localStorage. */
function effectiveApiToken(uiToken: string): string {
  const t = uiToken.trim()
  if (t) {
    return t
  }
  return DEFAULT_API_TOKEN.trim()
}

function makeId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`
}

function toWebSocketUrl(baseUrl: string, sessionId: string): string {
  const url = new URL(baseUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = `/v1/sessions/${sessionId}/ws`
  url.search = ''
  return url.toString()
}

function App() {
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL)
  const [apiToken, setApiToken] = useState(DEFAULT_API_TOKEN)
  const [sessionTitle, setSessionTitle] = useState('Web UI Session')
  const [sessionId, setSessionId] = useState('')
  const [recentSessions, setRecentSessions] = useState<string[]>([])
  const [messageText, setMessageText] = useState('')
  const [pendingFilesBySession, setPendingFilesBySession] = useState<Record<string, PendingFileRef[]>>({})
  const [messagesBySession, setMessagesBySession] = useState<Record<string, LogEntry[]>>({})
  const [status, setStatus] = useState('未连接')
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const [isSendingMessage, setIsSendingMessage] = useState(false)
  const [isUploadingFile, setIsUploadingFile] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const wsRef = useRef<WebSocket | null>(null)

  const canSendMessage = useMemo(() => {
    const currentPendingFiles = pendingFilesBySession[sessionId] || []
    return Boolean(sessionId.trim() && (messageText.trim() || currentPendingFiles.length > 0))
  }, [messageText, pendingFilesBySession, sessionId])

  const pendingFiles = pendingFilesBySession[sessionId] || []
  const messages = messagesBySession[sessionId] || []

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (!raw) {
        return
      }
      const parsed = JSON.parse(raw) as {
        baseUrl?: string
        apiToken?: string
        sessionId?: string
        recentSessions?: string[]
      }
      if (typeof parsed.baseUrl === 'string' && parsed.baseUrl) {
        setBaseUrl(parsed.baseUrl)
      }
      if (typeof parsed.apiToken === 'string') {
        setApiToken(parsed.apiToken)
      }
      if (typeof parsed.sessionId === 'string') {
        setSessionId(parsed.sessionId)
      }
      if (Array.isArray(parsed.recentSessions)) {
        setRecentSessions(parsed.recentSessions.filter((item): item is string => typeof item === 'string'))
      }
    } catch {
      // Ignore malformed browser state and fall back to defaults.
    }
  }, [])

  useEffect(() => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        baseUrl,
        apiToken,
        sessionId,
        recentSessions,
      }),
    )
  }, [apiToken, baseUrl, recentSessions, sessionId])

  useEffect(() => {
    if (!sessionId.trim()) {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      setStatus('未连接')
      return
    }

    const ws = new WebSocket(toWebSocketUrl(gatewayApiBaseUrl(baseUrl), sessionId))
    wsRef.current = ws
    setStatus('正在连接会话 WebSocket...')

    ws.onopen = () => {
      setStatus('已连接，等待助手输出')
    }
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as SessionEventMessage
        if (payload.kind === 'text' && payload.content.text) {
          const targetSession = payload.session_id || sessionId
          setMessagesBySession((current) => ({
            ...current,
            [targetSession]: [
              ...(current[targetSession] || []),
              { id: makeId('assistant'), role: 'assistant', text: payload.content.text, sessionId: targetSession },
            ],
          }))
          return
        }
        if (payload.kind === 'file') {
          const targetSession = payload.session_id || sessionId
          const fileId = payload.content.file_id || (payload.meta?.file_id as string | undefined)
          if (!fileId) {
            setMessagesBySession((current) => ({
              ...current,
              [targetSession]: [
                ...(current[targetSession] || []),
                {
                  id: makeId('system'),
                  role: 'system',
                  text: '收到文件事件但缺少 file_id，无法展示下载链接。',
                  sessionId: targetSession,
                },
              ],
            }))
            return
          }
          const filename = payload.content.filename || fileId
          setMessagesBySession((current) => ({
            ...current,
            [targetSession]: [
              ...(current[targetSession] || []),
              {
                id: makeId('assistant'),
                role: 'assistant',
                file: {
                  fileId,
                  filename,
                },
                sessionId: targetSession,
              },
            ],
          }))
          return
        }
      } catch {
        const targetSession = sessionId
        setMessagesBySession((current) => ({
          ...current,
          [targetSession]: [
            ...(current[targetSession] || []),
            {
              id: makeId('system'),
              role: 'system',
              text: `收到无法解析的 WS 消息：${String(event.data)}`,
              sessionId: targetSession,
            },
          ],
        }))
      }
    }
    ws.onerror = () => {
      setStatus('会话 WebSocket 出错')
    }
    ws.onclose = () => {
      setStatus('会话 WebSocket 已关闭')
    }

    return () => {
      ws.close()
      if (wsRef.current === ws) {
        wsRef.current = null
      }
    }
  }, [baseUrl, sessionId])

  function authHeaders(): HeadersInit {
    const token = effectiveApiToken(apiToken)
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  async function downloadSessionFile(targetSessionId: string, fileId: string, filename: string): Promise<void> {
    const token = effectiveApiToken(apiToken)
    if (!token) {
      setUploadError('下载需要 API Token：在侧栏填写，或在构建时设置 VITE_API_TOKEN（须与网关 FAIRYCLAW_API_TOKEN 一致）。')
      return
    }
    setUploadError('')
    const base = gatewayApiBaseUrl(baseUrl)
    const url = `${base}/v1/files/${encodeURIComponent(fileId)}/content?session_id=${encodeURIComponent(targetSessionId)}`
    try {
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        cache: 'no-store',
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = filename || fileId
      anchor.rel = 'noreferrer'
      anchor.click()
      URL.revokeObjectURL(objectUrl)
    } catch (error) {
      setUploadError(`下载失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  async function createSession(): Promise<void> {
    setIsCreatingSession(true)
    setUploadError('')
    try {
      const response = await fetch(`${gatewayApiBaseUrl(baseUrl)}/v1/sessions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify({
          platform: 'web',
          title: sessionTitle.trim() || 'Web UI Session',
          meta: {
            source: 'web_ui',
          },
        }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = (await response.json()) as SessionResult
      setSessionId(payload.session_id)
      setRecentSessions((current) => {
        const next = [payload.session_id, ...current.filter((item) => item !== payload.session_id)]
        return next.slice(0, SESSION_HISTORY_LIMIT)
      })
      setMessagesBySession((current) => ({
        ...current,
        [payload.session_id]: [
          {
            id: makeId('system'),
            role: 'system',
            text: `会话已创建：${payload.session_id}`,
            sessionId: payload.session_id,
          },
        ],
      }))
      setPendingFilesBySession((current) => ({
        ...current,
        [payload.session_id]: [],
      }))
    } catch (error) {
      setUploadError(`创建会话失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setIsCreatingSession(false)
    }
  }

  async function sendMessage(): Promise<void> {
    if (!canSendMessage) {
      return
    }
    const trimmedText = messageText.trim()
    const segments: Segment[] = []
    if (trimmedText) {
      segments.push({ type: 'text', content: trimmedText })
    }
    for (const file of pendingFiles) {
      segments.push({ type: 'file', file_id: file.fileId })
    }

    setIsSendingMessage(true)
    setUploadError('')
    try {
      const response = await fetch(`${gatewayApiBaseUrl(baseUrl)}/v1/sessions/${sessionId}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify({ segments }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = (await response.json()) as ChatResult
      setMessagesBySession((current) => ({
        ...current,
        [sessionId]: [
          ...(current[sessionId] || []),
          {
            id: makeId('user'),
            role: 'user',
            text: trimmedText || '[仅发送文件]',
            files: pendingFiles,
            sessionId,
          },
          {
            id: makeId('system'),
            role: 'system',
            text: payload.message,
            sessionId,
          },
        ],
      }))
      setMessageText('')
      setPendingFilesBySession((current) => ({
        ...current,
        [sessionId]: [],
      }))
    } catch (error) {
      setUploadError(`发送消息失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setIsSendingMessage(false)
    }
  }

  async function uploadFile(file: File): Promise<void> {
    if (!sessionId.trim()) {
      setUploadError('请先创建或选择一个会话，再上传文件。')
      return
    }

    setIsUploadingFile(true)
    setUploadError('')
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('session_id', sessionId)

      const response = await fetch(`${gatewayApiBaseUrl(baseUrl)}/v1/files`, {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = (await response.json()) as UploadResult
      setPendingFilesBySession((current) => ({
        ...current,
        [sessionId]: [
          ...(current[sessionId] || []),
          {
            fileId: payload.file_id,
            filename: payload.filename,
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
            text: `文件已上传并可作为下一条消息的 file segment 引用：${payload.filename}`,
            sessionId,
          },
        ],
      }))
    } catch (error) {
      setUploadError(`上传文件失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setIsUploadingFile(false)
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="panel">
          <h1>FairyClaw Web UI</h1>
          <p className="muted">基于 HttpGatewayAdapter 的第一方前端客户端。</p>
        </div>

        <div className="panel">
          <h2>连接配置</h2>
          <label className="field">
            <span>Gateway API 根地址</span>
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="http://127.0.0.1:8081"
            />
            <span className="muted small">填网关根地址即可（不要带 /app）；请求会访问 /v1/…</span>
          </label>
          <label className="field">
            <span>API Token</span>
            <input
              value={apiToken}
              onChange={(event) => setApiToken(event.target.value)}
              placeholder="sk-fairyclaw-dev-token"
              type="password"
            />
          </label>
          <p className="status">{status}</p>
        </div>

        <div className="panel">
          <h2>会话</h2>
          <label className="field">
            <span>新会话标题</span>
            <input value={sessionTitle} onChange={(event) => setSessionTitle(event.target.value)} placeholder="Web UI Session" />
          </label>
          <button className="primary" onClick={() => void createSession()} disabled={isCreatingSession}>
            {isCreatingSession ? '创建中...' : '创建会话'}
          </button>

          <label className="field">
            <span>当前 Session ID</span>
            <input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder="sess_xxx" />
          </label>

          {recentSessions.length > 0 && (
            <div className="session-list">
              <span className="section-label">最近会话</span>
              {recentSessions.map((item) => (
                <button key={item} className="session-chip" onClick={() => setSessionId(item)}>
                  {item}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <h2>文件</h2>
          <label className="upload-field">
            <input
              type="file"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) {
                  void uploadFile(file)
                  event.target.value = ''
                }
              }}
            />
            <span>{isUploadingFile ? '上传中...' : '上传文件并添加到下一条消息'}</span>
          </label>

          {pendingFiles.length > 0 && (
            <div className="pending-files">
              <span className="section-label">待发送 file segments</span>
              {pendingFiles.map((file) => (
                <div key={file.fileId} className="pending-file">
                  <span>{file.filename}</span>
                  <button
                    onClick={() => {
                      setPendingFilesBySession((current) => ({
                        ...current,
                        [sessionId]: (current[sessionId] || []).filter((item) => item.fileId !== file.fileId),
                      }))
                    }}
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
          )}
          {uploadError && <p className="error">{uploadError}</p>}
        </div>
      </aside>

      <main className="chat-panel">
        <div className="messages">
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>开始一次会话</h2>
              <p>先创建 Session，再发送文本或上传文件并作为 file segment 引用。</p>
            </div>
          ) : (
            messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <header>{message.role}</header>
                {'text' in message && message.text && <p>{message.text}</p>}
                {'files' in message && message.files.length > 0 && (
                  <ul className="file-list">
                    {message.files.map((file) => (
                      <li key={file.fileId}>
                        <button
                          type="button"
                          className="file-link"
                          onClick={() => void downloadSessionFile(message.sessionId, file.fileId, file.filename)}
                        >
                          {file.filename}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {'file' in message && message.file && (
                  <button
                    type="button"
                    className="file-link"
                    onClick={() => {
                      const f = message.file
                      if (!f) {
                        return
                      }
                      void downloadSessionFile(message.sessionId, f.fileId, f.filename)
                    }}
                  >
                    下载 {message.file.filename}
                  </button>
                )}
              </article>
            ))
          )}
        </div>

        <div className="composer">
          <textarea
            value={messageText}
            onChange={(event) => setMessageText(event.target.value)}
            placeholder="输入文本消息。若已上传文件，可与文本一起作为 segments 发送。"
            rows={5}
          />
          <div className="composer-actions">
            <span className="muted">
              将发送 {messageText.trim() ? 'text' : '0 text'} + {pendingFiles.length} file segment(s)
            </span>
            <button className="primary" onClick={() => void sendMessage()} disabled={!canSendMessage || isSendingMessage}>
              {isSendingMessage ? '发送中...' : '发送消息'}
            </button>
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
