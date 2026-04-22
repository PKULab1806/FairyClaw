import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './SessionsPage.css'

const TIMER_TICK_PREFIX = '[TIMER_TICK]'
const TIMER_PAYLOAD_PREFIX = '[TIMER_PAYLOAD]'

function parseTimerTickPreview(text: string): { head: string; payload: string } | null {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length === 0 || !lines[0].startsWith(TIMER_TICK_PREFIX)) {
    return null
  }
  const payloadLine = lines.find((line) => line.startsWith(TIMER_PAYLOAD_PREFIX))
  return {
    head: lines[0],
    payload: payloadLine ? payloadLine.slice(TIMER_PAYLOAD_PREFIX.length).trim() : '',
  }
}

export function SessionsPage() {
  const { t } = useLocale()
  const navigate = useNavigate()
  const {
    sessionTitle,
    setSessionTitle,
    recentSessions,
    uploadError,
    isCreatingSession,
    createSession,
    wsState,
    serverSessions,
    loadServerSessions,
    restoreSession,
    sendWsOp,
  } = useGateway()
  const [previewSessionId, setPreviewSessionId] = useState('')
  const [previewEvents, setPreviewEvents] = useState<Array<Record<string, unknown>>>([])
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')

  useEffect(() => {
    if (wsState !== 'connected') {
      return
    }
    void loadServerSessions()
  }, [wsState, loadServerSessions])

  const onCreate = async () => {
    const ok = await createSession()
    if (ok) {
      navigate('/chat')
    }
  }

  const onRestore = async (sid: string) => {
    await restoreSession(sid)
    navigate('/chat')
  }

  const loadPreview = async (sid: string) => {
    setPreviewSessionId(sid)
    setIsPreviewLoading(true)
    setPreviewError('')
    try {
      const body = await sendWsOp('sessions.history', { session_id: sid, limit: 200 })
      const events = body.events
      setPreviewEvents(Array.isArray(events) ? events.filter((x): x is Record<string, unknown> => Boolean(x) && typeof x === 'object') : [])
    } catch (error) {
      setPreviewEvents([])
      setPreviewError(
        t('sessions.previewLoadError', {
          detail: error instanceof Error ? error.message : String(error),
        }),
      )
    } finally {
      setIsPreviewLoading(false)
    }
  }

  return (
    <div className="sessions-page">
      <div className="sessions-main">
        <h1 className="sessions-page__title">{t('sessions.title')}</h1>
        <p className="sessions-page__hint">{t('sessions.hint')}</p>

        <section className="sessions-card">
          <label className="field">
            <span className="field__label">{t('sessions.newTitle')}</span>
            <input
              className="field__input"
              value={sessionTitle}
              onChange={(e) => setSessionTitle(e.target.value)}
              placeholder={t('sessions.defaultTitle')}
            />
          </label>
          <button type="button" className="btn btn--primary" disabled={isCreatingSession} onClick={() => void onCreate()}>
            {isCreatingSession ? t('sessions.creating') : t('sessions.create')}
          </button>
        </section>

        <section className="sessions-card">
          <h2 className="sessions-page__subtitle">{t('sessions.webList')}</h2>
          {serverSessions.length === 0 ? (
            <p className="sessions-page__hint">{t('sessions.serverListEmpty')}</p>
          ) : (
            <ul className="sessions-server-list">
              {serverSessions.map((row) => (
                <li key={row.session_id} className="sessions-server-list__item">
                  <div className="sessions-server-list__main">
                    <span className="sessions-server-list__title">{row.title || row.session_id}</span>
                    <span className="sessions-server-list__meta">
                      {row.event_count} {t('sessions.events')} · {row.session_id.slice(0, 14)}…
                    </span>
                  </div>
                  <div className="sessions-server-list__actions">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={wsState !== 'connected'}
                      onClick={() => void loadPreview(row.session_id)}
                    >
                      {t('sessions.viewHistory')}
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={wsState !== 'connected'}
                      onClick={() => void onRestore(row.session_id)}
                    >
                      {t('sessions.restore')}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {recentSessions.length > 0 && (
          <section className="sessions-card">
            <h2 className="sessions-page__subtitle">{t('sessions.recent')}</h2>
            <div className="sessions-chips">
              <div className="sessions-chips__list">
                {recentSessions.map((item) => (
                  <button key={item} type="button" className="chip" onClick={() => void onRestore(item)}>
                    {item.slice(0, 16)}…
                  </button>
                ))}
              </div>
            </div>
          </section>
        )}

        {uploadError && <p className="sessions-error">{uploadError}</p>}
      </div>

      <aside className={`sessions-drawer${previewSessionId ? ' sessions-drawer--open' : ''}`}>
        <div className="sessions-preview-head">
          <h2 className="sessions-page__subtitle">{t('sessions.previewTitle')}</h2>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => {
              setPreviewSessionId('')
              setPreviewEvents([])
              setPreviewError('')
            }}
            disabled={!previewSessionId}
          >
            {t('sessions.previewClose')}
          </button>
        </div>
        {previewSessionId ? (
          <>
            <span className="sessions-preview-head__id">{previewSessionId}</span>
            {isPreviewLoading ? (
              <p className="sessions-page__hint">{t('sessions.previewLoading')}</p>
            ) : previewError ? (
              <p className="sessions-error">{previewError}</p>
            ) : previewEvents.length === 0 ? (
              <p className="sessions-page__hint">{t('sessions.previewEmpty')}</p>
            ) : (
              <ul className="sessions-preview-list">
                {previewEvents.map((event, idx) => {
                  const kind = String(event.kind || '')
                  const ts = typeof event.ts_ms === 'number' ? new Date(event.ts_ms).toLocaleString() : ''
                  if (kind === 'session_event') {
                    const role = String(event.role || 'assistant')
                    const text = String(event.text || '')
                    const timer = parseTimerTickPreview(text)
                    return (
                      <li key={`preview_${idx}`} className="sessions-preview-item">
                        <div className="sessions-preview-item__meta">
                          <span className="sessions-preview-item__role">{timer ? 'timer_tick' : role}</span>
                          {ts ? <time>{ts}</time> : null}
                        </div>
                        <pre className="sessions-preview-item__body">
                          {timer ? `${timer.head}${timer.payload ? `\n${timer.payload}` : ''}` : (text || ' ')}
                        </pre>
                      </li>
                    )
                  }
                  if (kind === 'operation_event') {
                    const toolName = String(event.tool_name || 'tool')
                    const detail = event.result_preview != null ? String(event.result_preview) : ''
                    return (
                      <li key={`preview_${idx}`} className="sessions-preview-item">
                        <div className="sessions-preview-item__meta">
                          <span className="sessions-preview-item__role">tool:{toolName}</span>
                          {ts ? <time>{ts}</time> : null}
                        </div>
                        <pre className="sessions-preview-item__body">{detail || ' '}</pre>
                      </li>
                    )
                  }
                  return null
                })}
              </ul>
            )}
          </>
        ) : (
          <p className="sessions-page__hint">{t('sessions.previewSelect')}</p>
        )}
      </aside>
    </div>
  )
}
