export function timeAgo(epochMs: number, now: number = Date.now()): string {
  const s = Math.floor((now - epochMs) / 1000)
  if (s < 2) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
