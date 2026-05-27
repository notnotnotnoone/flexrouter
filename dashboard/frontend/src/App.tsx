import { useState } from 'react'
import { LiveTelemetry } from './tabs/LiveTelemetry'
import { Chat } from './tabs/Chat'
import { RequestLogs } from './tabs/RequestLogs'
import { AccountStatus } from './tabs/AccountStatus'
import { Settings } from './tabs/Settings'
import { Setup } from './tabs/Setup'

const TABS = [
  { id: 'telemetry', label: 'Live Telemetry', component: LiveTelemetry },
  { id: 'chat', label: 'Chat', component: Chat },
  { id: 'logs', label: 'Request Logs', component: RequestLogs },
  { id: 'accounts', label: 'Account Status', component: AccountStatus },
  { id: 'settings', label: 'Settings', component: Settings },
  { id: 'setup', label: 'Setup', component: Setup },
]

export default function App() {
  const [activeTab, setActiveTab] = useState(
    window.location.hash === '#setup' ? 'setup' : 'telemetry'
  )
  const [dark, setDark] = useState(false)
  const Tab = TABS.find(t => t.id === activeTab)?.component ?? LiveTelemetry

  return (
    <div className={dark ? 'dark' : ''}>
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
        <div className="max-w-6xl mx-auto px-6 py-10">
          <div className="flex justify-between items-start mb-8">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-blue-500 mb-1">Router Control Center</p>
              <h1 className="text-3xl font-bold">flexrouter</h1>
              <p className="text-gray-500 text-sm mt-1">Live model telemetry, provider health, and routing controls.</p>
            </div>
            <button onClick={() => setDark(d => !d)} className="text-xl p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">
              {dark ? '☀️' : '🌙'}
            </button>
          </div>

          <div className="flex gap-1 border-b border-gray-200 dark:border-gray-800 mb-6">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === t.id
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <Tab />
        </div>
      </div>
    </div>
  )
}
