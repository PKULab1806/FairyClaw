import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { APP_TITLE, APP_VERSION } from '../constants'
import { useGateway } from '../contexts/GatewayContext'
import { useLocale } from '../contexts/LocaleContext'

import './AppShell.css'

function wsDotClass(state: string): string {
  if (state === 'connected') {
    return 'status-dot status-dot--ok'
  }
  if (state === 'connecting') {
    return 'status-dot status-dot--pending'
  }
  return 'status-dot status-dot--off'
}

function formatTaskStatus(status: string): string {
  const raw = (status || '').trim().toLowerCase()
  if (!raw) {
    return 'running'
  }
  if (raw === 'active') {
    return 'running'
  }
  if (raw.startsWith('running:')) {
    return raw.split(':', 1)[0]
  }
  return raw
}

function formatTaskLabel(task: {
  label: string
  task_type?: string
  instruction?: string
}): string {
  const label = (task.label || '').trim()
  const looksGeneric = /^sub-agent of\s+/i.test(label) || !label
  const instruction = (task.instruction || '').trim()
  const taskType = (task.task_type || 'general').trim() || 'general'
  if (instruction) {
    return `${taskType} | ${instruction}`
  }
  if (looksGeneric) {
    return `${taskType} | ${label || 'task'}`
  }
  return label
}

export function AppShell() {
  const { locale, setLocale, t } = useLocale()
  const { wsState, telemetry, sessionUsage, sessionId, subagentTasksBySession } = useGateway()
  const location = useLocation()

  const wsLabelKey = `ws.${wsState}` as const
  const wsLabel = t(wsLabelKey)

  return (
    <div className="app-layout">
      <header className="app-header">
        <div className="app-header__brand">
          <span className="app-header__title">{APP_TITLE}</span>
          <span className="app-header__subtitle">{t('header.subtitle')}</span>
        </div>
        <div className="app-header__actions">
          <span className="app-header__conn" title={wsLabel}>
            <span className={wsDotClass(wsState)} aria-hidden />
            <span className="app-header__conn-text">{wsLabel}</span>
          </span>
          <div className="lang-toggle" role="group" aria-label="Language">
            <button
              type="button"
              className={`lang-toggle__btn${locale === 'zh' ? ' lang-toggle__btn--active' : ''}`}
              onClick={() => setLocale('zh')}
            >
              {t('header.langZh')}
            </button>
            <button
              type="button"
              className={`lang-toggle__btn${locale === 'en' ? ' lang-toggle__btn--active' : ''}`}
              onClick={() => setLocale('en')}
            >
              {t('header.langEn')}
            </button>
          </div>
        </div>
      </header>

      <div className="app-body">
        <aside className="app-nav">
          <nav className="nav-section">
            <div className="nav-section__label">{t('nav.group.main')}</div>
            <NavLink to="/chat" className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`} end>
              {t('nav.chat')}
            </NavLink>
            <NavLink to="/sessions" className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}>
              {t('nav.sessions')}
            </NavLink>
            <NavLink to="/agents" className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}>
              {t('nav.agents')}
            </NavLink>
            <NavLink to="/skills" className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}>
              {t('nav.skills')}
            </NavLink>
            <NavLink to="/channels" className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}>
              {t('nav.channels')}
            </NavLink>
          </nav>

          <nav className="nav-section">
            <div className="nav-section__label">{t('nav.group.settings')}</div>
            <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}>
              {t('nav.settings')}
            </NavLink>
          </nav>

          <div className="app-nav__footer">
            {t('footer.version')} {APP_VERSION}
          </div>
        </aside>

        <main className="app-main">
          <Outlet key={location.pathname} />
        </main>

        <aside className="app-right" aria-label="Status">
          <div className="right-card right-card--tasks">
            {(subagentTasksBySession[sessionId] || []).length === 0 ? (
              <div className="right-card__placeholder">
                <span className="right-card__check" aria-hidden>
                  ✓
                </span>
                <span>{t('right.tasksEmpty')}</span>
              </div>
            ) : (
              <ul className="right-task-list">
                {(subagentTasksBySession[sessionId] || []).map((task) => (
                  <li key={task.task_id} className="right-task-list__item">
                    <span className="right-task-list__label">{formatTaskLabel(task)}</span>
                    <span className="right-task-list__status">
                      {formatTaskStatus(task.status_display || task.status)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="right-card">
            <div className="right-card__title">{t('right.status')}</div>
            <dl className="right-metrics">
              <div className="right-metrics__row">
                <dt>{t('right.reins')}</dt>
                <dd>{telemetry?.reinsEnabled == null ? t('right.na') : telemetry.reinsEnabled ? 'on' : 'off'}</dd>
              </div>
              <div className="right-metrics__row">
                <dt>{t('right.heartbeat')}</dt>
                <dd>{telemetry?.heartbeatStatus ?? t('right.na')}</dd>
              </div>
              <div className="right-metrics__row">
                <dt>{t('right.serverTime')}</dt>
                <dd>
                  {telemetry?.serverTimeMs != null
                    ? new Date(telemetry.serverTimeMs).toLocaleTimeString(locale === 'zh' ? 'zh-CN' : 'en-US', {
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                      })
                    : t('right.na')}
                </dd>
              </div>
              <div className="right-metrics__row">
                <dt>{t('right.monthTokens')}</dt>
                <dd>{sessionUsage != null ? String(sessionUsage.monthTokensUsed) : t('right.na')}</dd>
              </div>
              <div className="right-metrics__row">
                <dt>{t('right.sessionTokens')}</dt>
                <dd>{sessionUsage != null ? String(sessionUsage.sessionTokensUsed) : t('right.na')}</dd>
              </div>
            </dl>
          </div>
          <div className="app-right__powered">{t('footer.powered')}</div>
        </aside>
      </div>
    </div>
  )
}
