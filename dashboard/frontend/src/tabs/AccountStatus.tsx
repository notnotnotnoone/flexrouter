import { useEffect, useState } from 'react'
import { fetchStatus } from '../api'

export function AccountStatus() {
  const [status, setStatus] = useState<any>(null)
  useEffect(() => { fetchStatus().then(setStatus) }, [])
  const providers = status?.providers ?? {}
  return (
    <div className="space-y-4">
      <h2 className="font-semibold">Provider Accounts</h2>
      {Object.entries(providers).map(([name, info]: any) => (
        <div key={name} className="border border-gray-100 dark:border-gray-800 rounded-xl p-4">
          <div className="flex justify-between items-center">
            <span className="font-medium">{name}</span>
            <span className="text-sm text-gray-500">${info.daily_cost_usd?.toFixed(4) ?? '0.0000'} today</span>
          </div>
          {info.budget_usd && (
            <div className="mt-2 text-xs text-gray-400">Budget: ${info.budget_usd}/day</div>
          )}
        </div>
      ))}
      {Object.keys(providers).length === 0 && <p className="text-gray-400 text-sm">No provider data yet.</p>}
    </div>
  )
}
