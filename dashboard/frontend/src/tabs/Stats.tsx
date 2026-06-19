import { usePolling } from '@/hooks/usePolling'
import { fetchStats } from '@/api'
import type { Stats } from '@/lib/statsTypes'
import { colorFor } from '@/lib/heatmap'
import { VolumeSection } from '@/components/charts/VolumeSection'
import { LatencySection } from '@/components/charts/LatencySection'
import { DistributionSection } from '@/components/charts/DistributionSection'
import { ReliabilitySection } from '@/components/charts/ReliabilitySection'
import { EfficiencySection } from '@/components/charts/EfficiencySection'
import { Heatmap } from '@/components/charts/Heatmap'

function buildHeatmapCells(hourly: Stats['hourly']): { hour: string; day: string; value: number }[] {
  const max = Math.max(...hourly.map(h => h.requests), 1)
  return hourly.map(h => {
    const dt = new Date(h.hour)
    return {
      hour: String(dt.getUTCHours()),
      day: dt.toLocaleDateString('en', { weekday: 'short' }),
      value: colorFor(h.requests, max) as unknown as number,
      raw: h.requests,
    }
  }) as any
}

export function StatsTab() {
  const { data, status } = usePolling<Stats>(fetchStats, { intervalMs: 5000, endpoint: '/api/stats' })

  return (
    <div className="space-y-6 p-4">
      {status === 'loading' && !data && (
        <div className="text-sm text-muted-foreground">Loading stats…</div>
      )}
      <VolumeSection stats={data} />
      <section>
        <h3 className="text-sm font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Request heatmap</h3>
        <Heatmap cells={buildHeatmapCells(data?.hourly ?? [])} />
      </section>
      <LatencySection stats={data} />
      <DistributionSection stats={data} />
      <ReliabilitySection stats={data} />
      <section>
        <h3 className="text-sm font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Tier utilization</h3>
        <EfficiencySection stats={data} />
      </section>
    </div>
  )
}
