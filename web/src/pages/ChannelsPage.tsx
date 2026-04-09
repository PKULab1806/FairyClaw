import { useCallback, useEffect, useState } from 'react'

import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './ChannelsPage.css'

export function ChannelsPage() {
  const { t } = useLocale()
  const { wsState, sendWsOp } = useGateway()
  const [apiBase, setApiBase] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [allowedUser, setAllowedUser] = useState('')
  const [cmdPrefix, setCmdPrefix] = useState('/sess')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (wsState !== 'connected') {
      return
    }
    setError('')
    try {
      const body = await sendWsOp('config.onebot.get', {})
      const s = body.settings as Record<string, string> | undefined
      if (s && typeof s === 'object') {
        setApiBase(s.ONEBOT_API_BASE || '')
        setAccessToken(s.ONEBOT_ACCESS_TOKEN || '')
        setAllowedUser(s.ONEBOT_ALLOWED_USER || '')
        setCmdPrefix(s.ONEBOT_SESSION_CMD_PREFIX || '/sess')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [sendWsOp, wsState])

  useEffect(() => {
    void load()
  }, [load])

  const onSave = async () => {
    setError('')
    setMessage('')
    try {
      await sendWsOp('config.onebot.put', {
        ONEBOT_API_BASE: apiBase,
        ONEBOT_ACCESS_TOKEN: accessToken,
        ONEBOT_ALLOWED_USER: allowedUser,
        ONEBOT_SESSION_CMD_PREFIX: cmdPrefix,
      })
      setMessage(t('channels.saved'))
    } catch (e) {
      setError(t('channels.error', { detail: e instanceof Error ? e.message : String(e) }))
    }
  }

  return (
    <div className="channels-layout">
      <aside className="channels-rail" aria-label={t('channels.railLabel')}>
        <div className="channels-rail__title">{t('channels.railTitle')}</div>
        <ul className="channels-rail__list">
          <li>
            <button type="button" className="channels-rail__item channels-rail__item--active">
              <span className="channels-rail__icon" aria-hidden>
                ◆
              </span>
              <span className="channels-rail__name">OneBot</span>
              <span className="channels-rail__badge">{t('channels.badgeIm')}</span>
            </button>
          </li>
        </ul>
        <p className="channels-rail__foot">{t('channels.railFoot')}</p>
      </aside>

      <div className="channels-main">
        <header className="channels-main__head">
          <h1 className="channels-main__title">{t('channels.title')}</h1>
          <p className="channels-main__hint">{t('channels.hint')}</p>
        </header>

        {error && <p className="channels-page__error">{error}</p>}
        {message && <p className="channels-page__ok">{message}</p>}

        <section className="channels-panel">
          <h2 className="channels-panel__legend">{t('channels.sectionConnection')}</h2>
          <label className="channels-field">
            <span className="channels-field__label">ONEBOT_API_BASE</span>
            <input className="channels-field__input" value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
          </label>
          <label className="channels-field">
            <span className="channels-field__label">ONEBOT_ACCESS_TOKEN</span>
            <input
              className="channels-field__input"
              type="password"
              autoComplete="off"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
            />
          </label>

          <h2 className="channels-panel__legend channels-panel__legend--spaced">{t('channels.sectionSession')}</h2>
          <label className="channels-field">
            <span className="channels-field__label">ONEBOT_ALLOWED_USER</span>
            <input className="channels-field__input" value={allowedUser} onChange={(e) => setAllowedUser(e.target.value)} />
          </label>
          <label className="channels-field">
            <span className="channels-field__label">ONEBOT_SESSION_CMD_PREFIX</span>
            <input className="channels-field__input" value={cmdPrefix} onChange={(e) => setCmdPrefix(e.target.value)} />
          </label>

          <div className="channels-panel__actions">
            <button type="button" className="btn btn--primary" disabled={wsState !== 'connected'} onClick={() => void onSave()}>
              {t('channels.save')}
            </button>
            <button type="button" className="btn btn--ghost" disabled={wsState !== 'connected'} onClick={() => void load()}>
              {t('channels.reload')}
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
