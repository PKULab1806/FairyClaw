import { useCallback, useEffect, useState } from 'react'

import { SYSTEM_ENV_UI_KEYS } from '../constants'
import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './SettingsPage.css'

export function SettingsPage() {
  const { t } = useLocale()
  const { wsState, sendWsOp, gatewayBaseUrl, hasConfiguredToken } = useGateway()
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [tokenPresent, setTokenPresent] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    if (wsState !== 'connected') {
      return
    }
    setError('')
    try {
      const body = await sendWsOp('config.system_env.get', {})
      const env = body.env as Record<string, unknown> | undefined
      const next: Record<string, string> = {}
      for (const key of SYSTEM_ENV_UI_KEYS) {
        const v = env?.[key]
        next[key] = v != null ? String(v) : ''
      }
      setEnvValues(next)
      const tok = env?.FAIRYCLAW_API_TOKEN
      setTokenPresent(typeof tok === 'string' && tok.length > 0)
    } catch (e) {
      setError(t('settings.envLoadError'))
      setEnvValues({})
    }
  }, [sendWsOp, t, wsState])

  useEffect(() => {
    void load()
  }, [load])

  const onSave = async () => {
    if (wsState !== 'connected') {
      return
    }
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await sendWsOp('config.system_env.put', { env: envValues })
      setMessage(t('settings.envSaved'))
      void load()
    } catch (e) {
      setError(t('settings.envSaveError', { detail: e instanceof Error ? e.message : String(e) }))
    } finally {
      setSaving(false)
    }
  }

  const onChange = (key: string, value: string) => {
    setEnvValues((prev) => ({ ...prev, [key]: value }))
  }

  return (
    <div className="settings-page">
      <h1 className="settings-page__title">{t('settings.title')}</h1>
      <p className="settings-page__hint">{t('settings.hint')}</p>
      <p className="settings-page__origin">
        <span className="settings-page__origin-label">{t('settings.gatewayOrigin')}</span>
        <code className="settings-page__origin-value">{gatewayBaseUrl}</code>
      </p>
      <p className="settings-page__token-hint">
        {hasConfiguredToken ? t('settings.tokenBuildOk') : t('settings.tokenBuildMissing')}
      </p>
      {tokenPresent && (
        <p className="settings-page__token-server">{t('settings.tokenServerNote')}</p>
      )}

      {error && <p className="settings-page__error">{error}</p>}
      {message && <p className="settings-page__ok">{message}</p>}

      <section className="settings-env-card">
        <h2 className="settings-page__subtitle">{t('settings.envSection')}</h2>
        <div className="settings-env-grid">
          {SYSTEM_ENV_UI_KEYS.map((key) => (
            <label key={key} className="settings-env-field">
              <span className="settings-env-field__label">{key}</span>
              <input
                className="settings-env-field__input"
                value={envValues[key] ?? ''}
                onChange={(e) => onChange(key, e.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
          ))}
        </div>
        <button
          type="button"
          className="btn btn--primary settings-env-save"
          disabled={wsState !== 'connected' || saving}
          onClick={() => void onSave()}
        >
          {saving ? t('settings.saving') : t('settings.saveEnv')}
        </button>
      </section>
    </div>
  )
}
