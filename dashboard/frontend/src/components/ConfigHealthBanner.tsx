import { AlertCircle, AlertTriangle } from 'lucide-react'
import { fetchConfigValidation } from '../api'
import { usePolling } from '@/hooks/usePolling'

interface Validation { errors: string[]; warnings: string[] }

export function ConfigHealthBanner() {
  const { data } = usePolling<Validation>(fetchConfigValidation, { intervalMs: 10000, endpoint: '/api/config/validate' })
  const errors = data?.errors ?? []
  const warnings = data?.warnings ?? []
  if (errors.length === 0 && warnings.length === 0) return null
  return (
    <div className="space-y-2 mb-4">
      {errors.length > 0 && (
        <div className="rounded-lg border border-[var(--color-destructive)] bg-[var(--color-destructive)]/10 p-3">
          <div className="flex items-center gap-2 font-semibold text-[var(--color-destructive)] text-sm mb-1">
            <AlertCircle className="w-4 h-4" />Config errors ({errors.length})
          </div>
          <ul className="text-xs font-mono space-y-0.5 text-foreground">
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-lg border border-[var(--color-warning)] bg-[var(--color-warning)]/10 p-3">
          <div className="flex items-center gap-2 font-semibold text-[var(--color-warning)] text-sm mb-1">
            <AlertTriangle className="w-4 h-4" />Config warnings ({warnings.length})
          </div>
          <ul className="text-xs font-mono space-y-0.5 text-foreground">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
