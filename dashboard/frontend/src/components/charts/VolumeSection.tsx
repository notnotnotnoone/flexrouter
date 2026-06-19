import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
    </div>
  )
}

export function VolumeSection({ stats }: { stats: Stats | null }) {
  if (!stats || stats.totals.requests === 0) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  const data = stats.hourly.map(h => ({ hour: h.hour.slice(11, 16), requests: h.requests }))
  return (
    <section className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Tile label="Total requests" value={stats.totals.requests} />
        <Tile label="This hour" value={stats.totals.this_hour} />
        <Tile label="Peak RPM" value={stats.totals.peak_rpm} />
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <XAxis dataKey="hour" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="requests" fill="var(--color-brand)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
