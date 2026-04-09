import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { GatewayProvider } from './contexts/GatewayContext'
import { LocaleProvider } from './contexts/LocaleContext'
import { AppShell } from './layout/AppShell'
import { AgentsPage } from './pages/AgentsPage'
import { ChannelsPage } from './pages/ChannelsPage'
import { ChatPage } from './pages/ChatPage'
import { SessionsPage } from './pages/SessionsPage'
import { SettingsPage } from './pages/SettingsPage'
import { SkillsPage } from './pages/SkillsPage'

import './App.css'

const routerBasename = typeof window !== 'undefined' && window.location.pathname.startsWith('/app') ? '/app' : undefined

export default function App() {
  return (
    <BrowserRouter basename={routerBasename}>
      <LocaleProvider>
        <GatewayProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<Navigate to="/chat" replace />} />
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/sessions" element={<SessionsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/channels" element={<ChannelsPage />} />
            </Route>
          </Routes>
        </GatewayProvider>
      </LocaleProvider>
    </BrowserRouter>
  )
}
