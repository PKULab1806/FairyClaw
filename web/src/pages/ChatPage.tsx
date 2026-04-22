import { useCallback, useState } from 'react'

import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './ChatPage.css'

function formatTime(ts: number, locale: string): string {
  try {
    return new Date(ts).toLocaleTimeString(locale === 'zh' ? 'zh-CN' : 'en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return '—'
  }
}

export function ChatPage() {
  const { locale, t } = useLocale()
  const {
    sessionId,
    messageText,
    setMessageText,
    messagesBySession,
    pendingFilesBySession,
    uploadError,
    isSendingMessage,
    isWaitingAssistant,
    isUploadingFile,
    canSendMessage,
    downloadSessionFile,
    sendMessage,
    uploadFile,
    removePendingFile,
    selectedSubtaskChildId,
    selectedSubtaskLabel,
    subtaskLogsByChildId,
    clearSubtaskMonitor,
  } = useGateway()

  const [copiedId, setCopiedId] = useState<string | null>(null)
  const messages = messagesBySession[sessionId] || []
  const subtaskMessages =
    selectedSubtaskChildId != null ? (subtaskLogsByChildId[selectedSubtaskChildId] || []) : []
  const pendingFiles = pendingFilesBySession[sessionId] || []

  const copyText = useCallback(
    async (id: string, text: string) => {
      try {
        await navigator.clipboard.writeText(text)
        setCopiedId(id)
        window.setTimeout(() => setCopiedId(null), 2000)
      } catch {
        // ignore
      }
    },
    [],
  )

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void sendMessage()
    }
  }

  const renderTimeline = (
    list: typeof messages,
    options?: { showWaiting?: boolean; emptyTitle?: string; emptyHint?: string },
  ) => {
    const showWaiting = Boolean(options?.showWaiting)
    return (
      <div className="chat-messages">
        {list.length === 0 ? (
          <div className="chat-empty">
            <h2 className="chat-empty__title">{options?.emptyTitle || t('chat.emptyTitle')}</h2>
            <p className="chat-empty__hint">{options?.emptyHint || t('chat.emptyHint')}</p>
          </div>
        ) : (
          list.map((message) => {
            const time = formatTime(message.ts, locale)
            const roleLabel = t(`chat.role.${message.role}`)
            const bubbleClass =
              message.role === 'user'
                ? 'msg-bubble msg-bubble--user'
                : message.role === 'assistant'
                  ? 'msg-bubble msg-bubble--assistant'
                  : 'msg-bubble msg-bubble--system'

            return (
              <div key={message.id} className={`msg-row msg-row--${message.role}`}>
                <article className={bubbleClass}>
                  <header className="msg-bubble__head">
                    <span className="msg-bubble__role">{roleLabel}</span>
                    <time className="msg-bubble__time" dateTime={new Date(message.ts).toISOString()}>
                      {time}
                    </time>
                    {'text' in message && message.text && (
                      <button
                        type="button"
                        className="msg-bubble__icon"
                        aria-label={t('chat.copy')}
                        onClick={() => void copyText(message.id, message.text || '')}
                      >
                        {copiedId === message.id ? t('chat.copied') : t('chat.copy')}
                      </button>
                    )}
                  </header>
                  {'text' in message && message.text && <p className="msg-bubble__body">{message.text}</p>}
                  {'kind' in message && message.kind === 'tool_call' && (
                    <div className="msg-bubble__tool">
                      <p className="msg-bubble__tool-head">
                        {t('chat.toolCall', { name: message.toolName || message.toolCallId })}
                      </p>
                      <pre className="msg-bubble__tool-args">{message.argumentsJson}</pre>
                    </div>
                  )}
                  {'kind' in message && message.kind === 'tool_result' && (
                    <div className="msg-bubble__tool">
                      <p className="msg-bubble__tool-head">
                        {t('chat.toolResult', {
                          name: message.toolName || message.toolCallId,
                          status: message.ok ? t('chat.toolOk') : t('chat.toolErr'),
                        })}
                      </p>
                      <pre className="msg-bubble__tool-args">{message.detail}</pre>
                    </div>
                  )}
                  {'kind' in message && message.kind === 'timer_tick' && (
                    <div className="msg-bubble__timer">
                      <p className="msg-bubble__timer-head">
                        {t('chat.timerTick', {
                          mode: message.mode,
                          run: String(message.runIndex),
                        })}
                      </p>
                      <p className="msg-bubble__timer-meta">
                        {t('chat.timerJobId', { id: message.jobId || '-' })}
                      </p>
                      {message.payload ? (
                        <pre className="msg-bubble__tool-args">{message.payload}</pre>
                      ) : null}
                    </div>
                  )}
                  {'files' in message && message.files.length > 0 && (
                    <ul className="msg-bubble__files">
                      {message.files.map((file) => (
                        <li key={file.fileId}>
                          <button
                            type="button"
                            className="msg-bubble__link"
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
                      className="msg-bubble__link"
                      onClick={() => {
                        const f = message.file
                        if (!f) {
                          return
                        }
                        void downloadSessionFile(message.sessionId, f.fileId, f.filename)
                      }}
                    >
                      {t('chat.download')} {message.file.filename}
                    </button>
                  )}
                </article>
              </div>
            )
          })
        )}
        {showWaiting && (
          <div className="msg-row msg-row--assistant" role="status" aria-live="polite">
            <article className="msg-bubble msg-bubble--assistant chat-waiting-bubble">
              <span className="chat-waiting__spinner" aria-hidden />
              <span>{t('chat.sending')}</span>
            </article>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="chat-page">
      {selectedSubtaskChildId ? (
        <div className="chat-split">
          <section className="chat-pane chat-pane--main">{renderTimeline(messages, { showWaiting: isWaitingAssistant })}</section>
          <section className="chat-pane chat-pane--subtask">
            <header className="chat-pane__head">
              <div className="chat-pane__title-wrap">
                <span className="chat-pane__title">{t('chat.subtaskPanelTitle')}</span>
                <span className="chat-pane__subtitle">
                  {selectedSubtaskLabel || selectedSubtaskChildId}
                </span>
              </div>
              <button type="button" className="btn btn--ghost chat-pane__back-btn" onClick={clearSubtaskMonitor}>
                {t('chat.backToMain')}
              </button>
            </header>
            {renderTimeline(subtaskMessages, {
              emptyTitle: t('chat.subtaskEmptyTitle'),
              emptyHint: t('chat.subtaskEmptyHint'),
            })}
          </section>
        </div>
      ) : (
        renderTimeline(messages, { showWaiting: isWaitingAssistant })
      )}

      <div className="chat-composer">
        <textarea
          className="chat-composer__input"
          value={messageText}
          onChange={(e) => setMessageText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t('chat.placeholder')}
          rows={4}
        />
        <div className="chat-composer__toolbar">
          <label className="chat-composer__upload">
            <input
              type="file"
              disabled={!sessionId.trim() || isUploadingFile}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) {
                  void uploadFile(file)
                  event.target.value = ''
                }
              }}
            />
            <span>{isUploadingFile ? t('chat.uploading') : t('chat.fileUpload')}</span>
          </label>
          <span className="chat-composer__meta">
            {t('chat.segments', {
              text: messageText.trim() ? '1' : '0',
              files: String(pendingFiles.length),
            })}
          </span>
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canSendMessage || isSendingMessage}
            onClick={() => void sendMessage()}
          >
            {isSendingMessage ? (
              <span className="chat-send-loading">
                <span className="chat-send-loading__spinner" aria-hidden />
                {t('chat.sending')}
              </span>
            ) : (
              t('chat.send')
            )}
          </button>
        </div>
        {pendingFiles.length > 0 && (
          <div className="chat-pending">
            <span className="chat-pending__label">{t('chat.pendingFiles')}</span>
            <ul className="chat-pending__list">
              {pendingFiles.map((file) => (
                <li key={file.fileId} className="chat-pending__item">
                  <span>{file.filename}</span>
                  <button type="button" className="btn btn--ghost" onClick={() => removePendingFile(file.fileId)}>
                    {t('chat.remove')}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {uploadError && <p className="chat-error">{uploadError}</p>}
      </div>
    </div>
  )
}
