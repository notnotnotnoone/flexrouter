import { useEffect, useState } from 'react'
import { Activity, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { timeAgo } from '@/lib/time'
import type { PollState } from '@/hooks/usePolling'

export function StatusBar({ status, version, onOpenDiagnostics, diagnosticsCount }: {
  status: PollState<unknown>; version: string; onOpenDiagnostics: () => void; diagnosticsCount: number
}) {
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const dot = status.unreachable || status.status === 'error' ? 'bg-[var(--color-destructive)]'
    : status.status === 'stale' ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-success)]'
  const label = status.unreachable ? 'Backend unreachable'
    : status.status === 'error' ? 'Error'
    : status.status === 'stale' ? 'Stale' : 'Connected'

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-border bg-card text-xs text-muted-foreground tabular-nums">
      <span className="flex items-center gap-1.5 font-medium text-foreground">
        <span className={cn('w-2 h-2 rounded-full', dot, status.inFlight && 'animate-pulse')} />
        {label}
      </span>
      {status.lastUpdated && <span>synced {timeAgo(status.lastUpdated)}</span>}
      <span className="flex items-center gap-1"><Activity className="w-3 h-3" />{new Date().toLocaleTimeString()}</span>
      <span className="ml-auto">v{version}</span>
      <button onClick={onOpenDiagnostics} className="flex items-center gap-1 hover:text-foreground">
        <AlertTriangle className="w-3 h-3" />Diagnostics{diagnosticsCount > 0 && ` (${diagnosticsCount})`}
      </button>
    </div>
  )
}
