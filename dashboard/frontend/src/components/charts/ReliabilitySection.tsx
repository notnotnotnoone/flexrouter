import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

export function ReliabilitySection({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  const rate = stats.errors.rate_by_hour.map(r => ({ hour: r.hour.slice(11, 16), rate: Math.round(r.rate * 100) }))
  return (
    <section className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-border bg-card p-4 h-64">
          <div className="text-sm font-medium mb-2">Error rate % (hourly)</div>
          <ResponsiveContainer width="100%" height="85%">
            <LineChart data={rate}>
              <XAxis dataKey="hour" fontSize={11} stroke="var(--color-muted-foreground)" />
              <YAxis fontSize={11} stroke="var(--color-muted-foreground)" domain={[0, 100]} />
              <Tooltip />
              <Line dataKey="rate" stroke="var(--color-destructive)" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-xl border border-border bg-card p-4 h-64">
          <div className="text-sm font-medium mb-2">Outcomes by type</div>
          <ResponsiveContainer width="100%" height="85%">
            <BarChart data={stats.errors.by_type}>
              <XAxis dataKey="status" fontSize={11} stroke="var(--color-muted-foreground)" />
              <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="var(--color-warning)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-medium mb-2">Per-model reliability</div>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground border-b border-border">
            <th className="pb-2">Model</th><th className="pb-2">Requests</th><th className="pb-2">Errors</th><th className="pb-2">Error %</th>
          </tr></thead>
          <tbody>
            {stats.errors.per_model.map(m => (
              <tr key={m.model} className="border-b border-border/50">
                <td className="py-1.5 font-mono">{m.model}</td>
                <td className="tabular-nums">{m.requests}</td>
                <td className="tabular-nums">{m.errors}</td>
                <td className="tabular-nums">{(m.rate * 100).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
