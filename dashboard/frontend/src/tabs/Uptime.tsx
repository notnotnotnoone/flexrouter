import { fetchUptime } from '../api'
import { usePolling } from '@/hooks/usePolling'
import type { Uptime as UptimeData } from '@/lib/statsTypes'
import { mergeSegments } from '@/lib/timeline'

function pct(v: number | null) { return v === null ? '—' : `${(v * 100).toFixed(1)}%` }
function stateColor(s: string) {
  return s === 'up' ? 'var(--color-success)' : s === 'down' ? 'var(--color-destructive)' : 'var(--color-muted-foreground)'
}

export function Uptime() {
  const { data, status, error } = usePolling<UptimeData>(fetchUptime, { intervalMs: 5000, endpoint: '/api/uptime' })
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-semibold">Uptime</h2>
        {status === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {status === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale data</span>}
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">System uptime (24h)</div>
        <div className="text-3xl font-bold tabular-nums">{pct(data?.system.uptime_24h ?? null)}</div>
        <div className="flex flex-wrap gap-3 mt-2">
          {data?.providers.map(p => (
            <span key={p.provider} className="text-xs text-muted-foreground">{p.provider}: <b className="text-foreground">{pct(p.uptime_24h)}</b></span>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        {(data?.models ?? []).map(m => {
          const merged = mergeSegments(m.segments)
          return (
            <div key={m.model} className="rounded-xl border border-border bg-card p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-sm">{m.model}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  24h {pct(m.uptime_24h)} · 7d {pct(m.uptime_7d)} · 30d {pct(m.uptime_30d)}
                </span>
              </div>
              <div className="flex h-3 w-full gap-px overflow-hidden rounded">
                {merged.length === 0 && <div className="flex-1 bg-muted" />}
                {merged.map((seg, i) => (
                  <div key={i} style={{ flexGrow: seg.count, background: stateColor(seg.state) }} title={seg.state} />
                ))}
              </div>
            </div>
          )
        })}
        {(data?.models?.length ?? 0) === 0 && <p className="text-sm text-muted-foreground">No uptime samples yet.</p>}
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-medium mb-2">Incidents</div>
        {(data?.incidents ?? []).length === 0 && <p className="text-sm text-muted-foreground">No incidents recorded.</p>}
        <ul className="space-y-1 text-sm">
          {data?.incidents.map((inc, i) => (
            <li key={i} className="flex justify-between font-mono text-xs border-b border-border/50 py-1">
              <span>{inc.provider}/{inc.model} — {inc.event_type}</span>
              <span className="text-muted-foreground">
                {inc.start.slice(5, 16).replace('T', ' ')}{inc.duration_seconds !== null ? ` · ${inc.duration_seconds}s` : ' · ongoing'}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
