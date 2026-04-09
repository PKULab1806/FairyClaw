import { useCallback, useEffect, useState } from 'react'

import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './SkillsPage.css'

type GroupRow = {
  name: string
  description: string
  always_enable_planner: boolean
  always_enable_subagent: boolean
}

export function SkillsPage() {
  const { t } = useLocale()
  const { wsState, sendWsOp } = useGateway()
  const [groups, setGroups] = useState<GroupRow[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (wsState !== 'connected') {
      return
    }
    setError('')
    try {
      const body = await sendWsOp('capabilities.list', {})
      const raw = body.groups
      if (!Array.isArray(raw)) {
        setGroups([])
        return
      }
      const next: GroupRow[] = []
      for (const item of raw) {
        if (!item || typeof item !== 'object') {
          continue
        }
        const o = item as Record<string, unknown>
        next.push({
          name: String(o.name || ''),
          description: String(o.description || ''),
          always_enable_planner: Boolean(o.always_enable_planner),
          always_enable_subagent: Boolean(o.always_enable_subagent),
        })
      }
      setGroups(next.filter((g) => g.name))
    } catch {
      setError(t('skills.loadError'))
      setGroups([])
    }
  }, [sendWsOp, t, wsState])

  useEffect(() => {
    void load()
  }, [load])

  const patchGroup = async (name: string, patch: Record<string, boolean>) => {
    setBusy(name)
    setError('')
    try {
      await sendWsOp('capabilities.put', { group_name: name, patch })
      await load()
    } catch (e) {
      setError(t('skills.saveError', { detail: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="skills-page">
      <h1 className="skills-page__title">{t('skills.title')}</h1>
      <p className="skills-page__hint">{t('skills.hint')}</p>
      {error && <p className="skills-page__error">{error}</p>}
      {groups.length === 0 && !error ? (
        <p className="skills-page__hint">{t('skills.empty')}</p>
      ) : (
        <ul className="skills-list">
          {groups.map((g) => (
            <li key={g.name} className="skills-list__item">
              <div className="skills-list__head">
                <div className="skills-list__name">{g.name}</div>
                <div className="skills-list__desc">{g.description}</div>
              </div>
              <div className="skills-list__toggles">
                <label className="skills-toggle">
                  <input
                    type="checkbox"
                    checked={g.always_enable_planner}
                    disabled={wsState !== 'connected' || busy === g.name}
                    onChange={() =>
                      void patchGroup(g.name, { always_enable_planner: !g.always_enable_planner })
                    }
                  />
                  <span>{t('skills.plannerVisible')}</span>
                </label>
                <label className="skills-toggle">
                  <input
                    type="checkbox"
                    checked={g.always_enable_subagent}
                    disabled={wsState !== 'connected' || busy === g.name}
                    onChange={() =>
                      void patchGroup(g.name, { always_enable_subagent: !g.always_enable_subagent })
                    }
                  />
                  <span>{t('skills.subagentVisible')}</span>
                </label>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
