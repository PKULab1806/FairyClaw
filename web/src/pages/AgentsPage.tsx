import { useCallback, useEffect, useMemo, useState } from 'react'

import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './AgentsPage.css'

type LlmDoc = {
  default_profile: string
  fallback_profile: string | null
  profiles: Record<string, Record<string, unknown>>
}

const FIELD_ORDER = [
  'api_base',
  'api_key_env',
  'model',
  'timeout_seconds',
  'temperature',
  'type',
  'backend',
]

function sortedKeys(profile: Record<string, unknown>): string[] {
  return [...Object.keys(profile)].sort((a, b) => {
    const ai = FIELD_ORDER.indexOf(a)
    const bi = FIELD_ORDER.indexOf(b)
    if (ai >= 0 && bi >= 0) return ai - bi
    if (ai >= 0) return -1
    if (bi >= 0) return 1
    return a.localeCompare(b)
  })
}

function normalizeDoc(raw: unknown): LlmDoc {
  const obj = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const profilesRaw = obj.profiles && typeof obj.profiles === 'object' ? (obj.profiles as Record<string, unknown>) : {}
  const profiles: Record<string, Record<string, unknown>> = {}
  for (const [name, value] of Object.entries(profilesRaw)) {
    if (value && typeof value === 'object') {
      profiles[name] = { ...(value as Record<string, unknown>) }
    }
  }
  const names = Object.keys(profiles)
  const defaultProfile = typeof obj.default_profile === 'string' && obj.default_profile ? obj.default_profile : names[0] || 'main'
  const fallbackProfile = typeof obj.fallback_profile === 'string' && obj.fallback_profile ? obj.fallback_profile : null
  return { default_profile: defaultProfile, fallback_profile: fallbackProfile, profiles }
}

export function AgentsPage() {
  const { t } = useLocale()
  const { wsState, sendWsOp } = useGateway()
  const [doc, setDoc] = useState<LlmDoc>({ default_profile: 'main', fallback_profile: null, profiles: {} })
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const profileNames = useMemo(() => Object.keys(doc.profiles), [doc.profiles])

  const load = useCallback(async () => {
    if (wsState !== 'connected') return
    setLoading(true)
    setError('')
    try {
      const body = await sendWsOp('config.llm.get', {})
      const next = normalizeDoc(body.document)
      setDoc(next)
      setExpanded((prev) => {
        const n: Record<string, boolean> = {}
        for (const name of Object.keys(next.profiles)) {
          n[name] = prev[name] ?? name === next.default_profile
        }
        return n
      })
    } catch {
      setError(t('agents.loadError'))
    } finally {
      setLoading(false)
    }
  }, [sendWsOp, t, wsState])

  useEffect(() => {
    void load()
  }, [load])

  const setProfileField = (profileName: string, key: string, value: string) => {
    setDoc((prev) => {
      const profile = prev.profiles[profileName] || {}
      const old = profile[key]
      let nextVal: unknown = value
      if (typeof old === 'number' || key === 'timeout_seconds' || key === 'temperature') {
        const n = Number(value)
        nextVal = Number.isFinite(n) ? n : value
      } else if (typeof old === 'boolean') {
        nextVal = value === 'true'
      }
      return {
        ...prev,
        profiles: {
          ...prev.profiles,
          [profileName]: {
            ...profile,
            [key]: nextVal,
          },
        },
      }
    })
  }

  const onSave = async () => {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await sendWsOp('config.llm.put', { document: doc })
      setMessage(t('agents.saved'))
      void load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="agents-page">
      <h1 className="agents-page__title">{t('agents.title')}</h1>
      <p className="agents-page__hint">{t('agents.hint')}</p>
      {error && <p className="agents-page__error">{error}</p>}
      {message && <p className="agents-page__ok">{message}</p>}

      <section className="agents-top">
        <label className="agents-field">
          <span className="agents-field__label">default_profile</span>
          <select
            className="agents-field__input"
            value={doc.default_profile}
            onChange={(e) => setDoc((prev) => ({ ...prev, default_profile: e.target.value }))}
          >
            {profileNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="agents-field">
          <span className="agents-field__label">fallback_profile</span>
          <select
            className="agents-field__input"
            value={doc.fallback_profile || ''}
            onChange={(e) => setDoc((prev) => ({ ...prev, fallback_profile: e.target.value || null }))}
          >
            <option value="">(none)</option>
            {profileNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <div className="agents-accordion">
        {profileNames.map((name) => {
          const profile = doc.profiles[name] || {}
          const keys = sortedKeys(profile)
          const open = Boolean(expanded[name])
          return (
            <section key={name} className="agents-card">
              <button
                type="button"
                className="agents-card__head"
                onClick={() => setExpanded((prev) => ({ ...prev, [name]: !open }))}
              >
                <span className="agents-card__left">
                  <span className="agents-card__icon" aria-hidden>
                    ◉
                  </span>
                  <span>
                    <strong>{name}</strong>
                    <span className="agents-card__sub">{keys.length} {t('agents.fields')}</span>
                  </span>
                </span>
                <span className="agents-card__toggle" aria-hidden>{open ? '▾' : '▸'}</span>
              </button>
              {open && (
                <div className="agents-card__body">
                  {keys.map((key) => (
                    <label key={`${name}_${key}`} className="agents-field agents-field--row">
                      <span className="agents-field__label">{key}</span>
                      <input
                        className="agents-field__input"
                        value={String(profile[key] ?? '')}
                        onChange={(e) => setProfileField(name, key, e.target.value)}
                        spellCheck={false}
                      />
                    </label>
                  ))}
                </div>
              )}
            </section>
          )
        })}
      </div>

      <div className="agents-page__toolbar">
        <button type="button" className="btn btn--ghost" disabled={wsState !== 'connected' || loading} onClick={() => void load()}>
          {loading ? t('agents.loading') : t('agents.reload')}
        </button>
        <button
          type="button"
          className="btn btn--primary"
          disabled={wsState !== 'connected' || saving || loading}
          onClick={() => void onSave()}
        >
          {saving ? t('agents.saving') : t('agents.save')}
        </button>
      </div>
    </div>
  )
}
