import { useState } from 'react'
import { fetchLogs } from '../api'
import { usePolling } from '@/hooks/usePolling'

export function RequestLogs() {
  const { data: logs, status: pollStatus, error } = usePolling<any[]>(() => fetchLogs(100), { intervalMs: 3000, endpoint: '/api/logs' })
  const [paused, setPaused] = useState(false)

  const rows = paused ? (logs ?? []) : (logs ?? [])

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h2 className="font-semibold">Recent Requests</h2>
        <button onClick={() => setPaused(p => !p)} className="text-xs px-3 py-1 rounded border border-border">
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        {pollStatus === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {pollStatus === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale</span>}
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-left text-muted-foreground border-b border-border">
            {['Time', 'Tier', 'Model', 'In', 'Out', 'Cost', 'ms', 'Status'].map(h => (
              <th key={h} className="pb-2 pr-3 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...rows].reverse().map((row, i) => (
            <tr key={i} className="border-b border-border/50">
              <td className="py-1.5 pr-3 text-muted-foreground">{row.timestamp?.slice(11, 19)}</td>
              <td className="py-1.5 pr-3"><span className="px-1.5 py-0.5 bg-[var(--color-brand)]/10 text-[var(--color-brand)] rounded">{row.tier}</span></td>
              <td className="py-1.5 pr-3">{row.provider}/{row.model}</td>
              <td className="py-1.5 pr-3">{row.prompt_tokens}</td>
              <td className="py-1.5 pr-3">{row.completion_tokens}</td>
              <td className="py-1.5 pr-3">${Number(row.cost_usd).toFixed(5)}</td>
              <td className="py-1.5 pr-3">{row.latency_ms}</td>
              <td className="py-1.5"><span className={row.status === 'ok' ? 'text-[var(--color-success)]' : 'text-[var(--color-destructive)]'}>{row.status}</span></td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={8} className="py-8 text-center text-muted-foreground">No requests yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
