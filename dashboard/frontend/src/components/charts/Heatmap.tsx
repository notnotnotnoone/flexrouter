import { colorFor } from '@/lib/heatmap'

export function Heatmap({ cells }: { cells: { day: number; hour: number; value: number }[] }) {
  const max = cells.reduce((m, c) => Math.max(m, c.value), 0)
  const grid = new Map(cells.map(c => [`${c.day}-${c.hour}`, c.value]))
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  return (
    <div className="rounded-xl border border-border bg-card p-4 overflow-x-auto">
      <div className="text-xs text-muted-foreground mb-2">Requests by hour × day</div>
      <div className="grid gap-0.5" style={{ gridTemplateColumns: `auto repeat(24, 14px)` }}>
        <div />
        {Array.from({ length: 24 }, (_, h) => <div key={h} className="text-[9px] text-muted-foreground text-center">{h}</div>)}
        {days.map((d, di) => (
          <div key={d} style={{ display: 'contents' }}>
            <div className="text-[10px] text-muted-foreground pr-1">{d}</div>
            {Array.from({ length: 24 }, (_, h) => {
              const v = grid.get(`${di}-${h}`) ?? 0
              return <div key={`${di}-${h}`} title={`${d} ${h}:00 — ${v}`} className="w-[14px] h-[14px] rounded-sm" style={{ background: colorFor(v, max) }} />
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
