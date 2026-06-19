import { fetchStatus } from '../api'
import { usePolling } from '@/hooks/usePolling'

export function AccountStatus() {
  const { data: status, status: pollStatus, error } = usePolling<any>(fetchStatus, { intervalMs: 5000, endpoint: '/api/status' })
  const providers = status?.providers ?? {}
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="font-semibold">Provider Accounts</h2>
        {pollStatus === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {pollStatus === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale</span>}
      </div>
      {Object.entries(providers).map(([name, info]: any) => (
        <div key={name} className="border border-border rounded-xl p-4">
          <div className="flex justify-between items-center">
            <span className="font-medium">{name}</span>
            <span className="text-sm text-muted-foreground">${info.daily_cost_usd?.toFixed(4) ?? '0.0000'} today</span>
          </div>
          {info.budget_usd && <div className="mt-2 text-xs text-muted-foreground">Budget: ${info.budget_usd}/day</div>}
        </div>
      ))}
      {Object.keys(providers).length === 0 && <p className="text-muted-foreground text-sm">No provider data yet.</p>}
    </div>
  )
}
