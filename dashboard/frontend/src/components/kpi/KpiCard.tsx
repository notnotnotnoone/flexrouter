import './kpi.css'
import type { ReactNode } from 'react'

export function KpiCard({ label, value, children }: { label: string; value: ReactNode; children: ReactNode }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-card p-5 min-h-[102px]">
      {children}
      <div className="relative z-10">
        <div className="text-sm text-muted-foreground font-medium">{label}</div>
        <div className="text-2xl font-bold tabular-nums tracking-tight truncate">{value}</div>
      </div>
    </div>
  )
}
