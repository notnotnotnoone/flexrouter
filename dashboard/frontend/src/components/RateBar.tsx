type Props = { current: number; limit: number; label?: string }

export function RateBar({ current, limit, label }: Props) {
  const pct = limit > 0 ? Math.min((current / limit) * 100, 100) : 0
  const color = pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-amber-400' : 'bg-blue-500'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      {label && <span className="text-xs text-gray-500 dark:text-gray-400 font-mono">{label}</span>}
    </div>
  )
}
