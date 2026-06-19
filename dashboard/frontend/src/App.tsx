import { useState, useSyncExternalStore } from 'react'
import { LiveTelemetry } from './tabs/LiveTelemetry'
import { Chat } from './tabs/Chat'
import { RequestLogs } from './tabs/RequestLogs'
import { AccountStatus } from './tabs/AccountStatus'
import { Settings } from './tabs/Settings'
import { Setup } from './tabs/Setup'
import { StatusBar } from './components/StatusBar'
import { UnreachableBanner } from './components/UnreachableBanner'
import { DiagnosticsConsole } from './components/DiagnosticsConsole'
import { ConfigHealthBanner } from './components/ConfigHealthBanner'
import { ModelsOnlineCard } from './components/kpi/ModelsOnlineCard'
import { ProvidersOnlineCard } from './components/kpi/ProvidersOnlineCard'
import { TopModelCard } from './components/kpi/TopModelCard'
import { usePolling } from '@/hooks/usePolling'
import { fetchHealthCurrent, fetchStats } from './api'
import { getDiagnostics, subscribeDiagnostics } from './lib/diagnostics'

const TABS = [
  { id: 'telemetry', label: 'Live Telemetry', component: LiveTelemetry },
  { id: 'chat', label: 'Chat', component: Chat },
  { id: 'logs', label: 'Request Logs', component: RequestLogs },
  { id: 'accounts', label: 'Account Status', component: AccountStatus },
  { id: 'settings', label: 'Settings', component: Settings },
  { id: 'setup', label: 'Setup', component: Setup },
]

export default function App() {
  const [activeTab, setActiveTab] = useState(window.location.hash === '#setup' ? 'setup' : 'telemetry')
  const [diagOpen, setDiagOpen] = useState(false)
  const Tab = TABS.find(t => t.id === activeTab)?.component ?? LiveTelemetry
  const health = usePolling<any>(fetchHealthCurrent, { intervalMs: 3000, endpoint: '/api/health/current' })
  const stats = usePolling<any>(fetchStats, { intervalMs: 5000, endpoint: '/api/stats' })
  const diagnostics = useSyncExternalStore(subscribeDiagnostics, getDiagnostics)

  const models = Object.values(health.data?.models ?? {}) as any[]
  const modelsOnline = models.filter(m => m.status === 'up').length
  const providers = Object.values(health.data?.providers ?? {}) as any[]
  const providersOnline = providers.filter(p => (p.models_up ?? 0) > 0).length
  const topModel = stats.data?.distribution?.top_models?.[0]?.model ?? 'None'

  return (
    <div className="min-h-screen bg-background text-foreground">
      <UnreachableBanner error={health.unreachable ? health.error : null} onRetry={health.refresh} />
      <StatusBar status={health} version="0.1.0" diagnosticsCount={diagnostics.length} onOpenDiagnostics={() => setDiagOpen(true)} />
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="mb-2">
          <p className="text-xs font-semibold uppercase tracking-widest text-[var(--color-brand)]">Router Control Center</p>
          <h1 className="text-2xl font-bold">flexrouter</h1>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 my-6">
          <ModelsOnlineCard total={models.length} online={modelsOnline} />
          <ProvidersOnlineCard total={providers.length} online={providersOnline} />
          <TopModelCard label={topModel} active={topModel !== 'None'} />
        </div>
        <ConfigHealthBanner />
        <div className="flex gap-1 border-b border-border mb-6">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setActiveTab(t.id)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === t.id ? 'border-[var(--color-brand)] text-[var(--color-brand)]' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
              {t.label}
            </button>
          ))}
        </div>
        <Tab />
      </div>
      <DiagnosticsConsole open={diagOpen} onClose={() => setDiagOpen(false)} />
    </div>
  )
}
