import { BarChart, Bar, XAxis, YAxis, Tooltip, ScatterChart, Scatter, ZAxis, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

export function EfficiencySection({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  const scatter = stats.latency.per_model.map(m => ({ x: m.p95, y: m.count, name: m.model }))
  return (
    <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <div className="text-sm font-medium mb-2">Tier utilization</div>
        <ResponsiveContainer width="100%" height="85%">
          <BarChart data={stats.tiers.by_tier}>
            <XAxis dataKey="tier" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="requests" fill="var(--color-brand)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <div className="text-sm font-medium mb-2">p95 latency vs request volume</div>
        <ResponsiveContainer width="100%" height="85%">
          <ScatterChart>
            <XAxis type="number" dataKey="x" name="p95 ms" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis type="number" dataKey="y" name="requests" fontSize={11} stroke="var(--color-muted-foreground)" />
            <ZAxis range={[60, 60]} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} />
            <Scatter data={scatter} fill="var(--color-success)" />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
