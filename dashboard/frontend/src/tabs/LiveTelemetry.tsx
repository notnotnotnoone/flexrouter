import { useState } from 'react'
import { fetchStatus } from '../api'
import { usePolling } from '@/hooks/usePolling'
import { RateBar } from '../components/RateBar'
import { StatusDot } from '../components/StatusDot'

export function LiveTelemetry() {
  const { data: status, status: pollStatus, error, inFlight } = usePolling<any>(fetchStatus, { intervalMs: 2000, endpoint: '/api/status' })
  const [paused, setPaused] = useState(false)
  const [search, setSearch] = useState('')

  const models: [string, any][] = Object.entries(status?.models ?? {})
  const filtered = models.filter(([key]) => key.toLowerCase().includes(search.toLowerCase()))

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <input
          type="text"
          placeholder="Search models..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="border border-border rounded-lg px-3 py-1.5 text-sm bg-card w-64"
        />
        <button
          onClick={() => setPaused(p => !p)}
          className="text-xs px-3 py-1.5 rounded-lg border border-border"
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        {pollStatus === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {pollStatus === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale</span>}
        {inFlight && <span className="text-xs text-muted-foreground">refreshing…</span>}
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground uppercase border-b border-border">
            <th className="pb-2 pr-4">Model</th>
            <th className="pb-2 pr-4">RPM</th>
            <th className="pb-2 pr-4">TPM</th>
            <th className="pb-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(([key, info]) => {
            const penalized = !!info.penalty_until
            const dotStatus = penalized ? 'penalized' : 'up'
            return (
              <tr key={key} className="border-b border-border/50 hover:bg-muted/30">
                <td className="py-2 pr-4 font-mono font-medium">{key}</td>
                <td className="py-2 pr-4">
                  <RateBar current={info.rpm_current ?? 0} limit={60} label={`${info.rpm_current ?? 0}`} />
                </td>
                <td className="py-2 pr-4">
                  <RateBar current={info.tpm_current ?? 0} limit={60000} label={`${((info.tpm_current ?? 0) / 1000).toFixed(0)}k`} />
                </td>
                <td className="py-2">
                  <div className="flex items-center gap-1.5">
                    <StatusDot status={dotStatus} />
                    <span className="text-xs capitalize">{dotStatus}</span>
                  </div>
                </td>
              </tr>
            )
          })}
          {filtered.length === 0 && (
            <tr><td colSpan={4} className="py-8 text-center text-muted-foreground text-sm">No data yet. Run router.generate() first.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
