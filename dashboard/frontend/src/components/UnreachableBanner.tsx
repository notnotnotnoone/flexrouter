import { WifiOff } from 'lucide-react'
import type { FetchError } from '../api'

export function UnreachableBanner({ error, onRetry }: { error: FetchError | null; onRetry: () => void }) {
  if (!error) return null
  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-[var(--color-destructive)] text-white text-sm">
      <WifiOff className="w-4 h-4 shrink-0" />
      <span className="font-semibold">BACKEND UNREACHABLE</span>
      <span className="font-mono text-xs opacity-90 truncate">{error.url}: {error.message}</span>
      <button onClick={onRetry} className="ml-auto px-2 py-0.5 rounded bg-white/20 hover:bg-white/30 text-xs font-medium">
        Retry
      </button>
    </div>
  )
}
