import { toLanes } from '@/lib/swimlane'
import type { Uptime } from '@/lib/statsTypes'

export function Swimlane({ incidents }: { incidents: Uptime['incidents'] }) {
  const lanes = toLanes(incidents, Date.now())
  if (lanes.length === 0) {
    return <div className="text-sm text-muted-foreground p-4">No incidents in the last 24h.</div>
  }
  return (
    <div className="space-y-1">
      {lanes.map(lane => (
        <div key={lane.model} className="flex items-center gap-2">
          <span className="w-40 shrink-0 text-xs font-mono truncate">{lane.model}</span>
          <div className="relative flex-1 h-4 bg-[var(--color-success)]/15 rounded">
            {lane.bars.map((b, i) => (
              <div key={i} title={b.ongoing ? 'ongoing' : 'resolved'}
                className="absolute top-0 h-4 rounded"
                style={{ left: `${b.leftPct}%`, width: `${b.widthPct}%`,
                  background: b.ongoing ? 'var(--color-destructive)' : 'var(--color-warning)' }} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
