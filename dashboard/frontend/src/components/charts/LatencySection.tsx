import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

export function LatencySection({ stats }: { stats: Stats | null }) {
  if (!stats || stats.latency.per_model.length === 0) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  return (
    <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 h-72">
        <div className="text-sm font-medium mb-2">Latency p50 / p95 by model (ms)</div>
        <ResponsiveContainer width="100%" height="90%">
          <BarChart data={stats.latency.per_model} layout="vertical" margin={{ left: 40 }}>
            <XAxis type="number" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis type="category" dataKey="model" width={120} fontSize={10} stroke="var(--color-muted-foreground)" />
            <Tooltip /><Legend />
            <Bar dataKey="p50" fill="var(--color-success)" />
            <Bar dataKey="p95" fill="var(--color-warning)" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-72">
        <div className="text-sm font-medium mb-2">Latency distribution</div>
        <ResponsiveContainer width="100%" height="90%">
          <BarChart data={stats.latency.histogram}>
            <XAxis dataKey="bucket_ms" fontSize={10} stroke="var(--color-muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="count" fill="var(--color-brand)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
