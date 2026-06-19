import { useSyncExternalStore } from 'react'
import { X, Trash2 } from 'lucide-react'
import { getDiagnostics, subscribeDiagnostics, clearDiagnostics } from '../lib/diagnostics'

export function DiagnosticsConsole({ open, onClose }: { open: boolean; onClose: () => void }) {
  const entries = useSyncExternalStore(subscribeDiagnostics, getDiagnostics)
  if (!open) return null
  const ordered = [...entries].reverse()
  return (
    <div className="fixed inset-y-0 right-0 w-[420px] max-w-full bg-card border-l border-border shadow-xl z-50 flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="font-semibold text-sm">Diagnostics ({entries.length})</span>
        <div className="flex items-center gap-2">
          <button onClick={clearDiagnostics} className="text-muted-foreground hover:text-foreground"><Trash2 className="w-4 h-4" /></button>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="w-4 h-4" /></button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1 font-mono text-xs">
        {ordered.length === 0 && <p className="text-muted-foreground p-4">No errors recorded.</p>}
        {ordered.map(e => (
          <div key={e.id} className="border border-border rounded-md p-2">
            <div className="flex justify-between text-muted-foreground">
              <span>{e.endpoint}</span>
              <span className="text-[var(--color-destructive)]">{e.status === 0 ? 'NET' : e.status}</span>
            </div>
            <div className="text-foreground break-words">{e.message}</div>
            <div className="text-muted-foreground/70">{e.ts}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
