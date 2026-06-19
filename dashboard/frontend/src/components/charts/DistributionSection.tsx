import { PieChart, Pie, Cell, Tooltip, BarChart, Bar, XAxis, YAxis, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

const CHART_COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']

export function DistributionSection({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  return (
    <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <div className="text-sm font-medium mb-2">Requests by provider</div>
        <ResponsiveContainer width="100%" height="85%">
          <PieChart>
            <Pie data={stats.distribution.by_provider} dataKey="requests" nameKey="provider" innerRadius={40} outerRadius={70}>
              {stats.distribution.by_provider.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
            </Pie>
            <Tooltip />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-64 lg:col-span-2">
        <div className="text-sm font-medium mb-2">Top models</div>
        <ResponsiveContainer width="100%" height="85%">
          <BarChart data={stats.distribution.top_models} layout="vertical" margin={{ left: 40 }}>
            <XAxis type="number" fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <YAxis type="category" dataKey="model" width={140} fontSize={10} stroke="var(--color-muted-foreground)" />
            <Tooltip />
            <Bar dataKey="requests" fill="var(--color-brand)" radius={[0, 3, 3, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">Routing diversity (bits)</div>
        <div className="text-2xl font-bold tabular-nums">{stats.distribution.diversity.toFixed(3)}</div>
        <p className="text-xs text-muted-foreground mt-1">Higher = traffic spread across more providers.</p>
      </div>
    </section>
  )
}
