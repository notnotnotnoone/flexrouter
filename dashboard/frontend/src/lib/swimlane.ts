interface Incident { provider: string; model: string; event_type: string; start: string; end: string | null; duration_seconds: number | null }
const WINDOW_MS = 24 * 60 * 60 * 1000

export function toLanes(incidents: Incident[], now: number) {
  const windowStart = now - WINDOW_MS
  const byModel = new Map<string, { leftPct: number; widthPct: number; ongoing: boolean }[]>()
  for (const inc of incidents) {
    const start = new Date(inc.start.replace('Z', '+00:00')).getTime()
    const end = inc.end ? new Date(inc.end.replace('Z', '+00:00')).getTime() : now
    if (end < windowStart) continue
    const clampedStart = Math.max(start, windowStart)
    const leftPct = ((clampedStart - windowStart) / WINDOW_MS) * 100
    const widthPct = Math.max(((end - clampedStart) / WINDOW_MS) * 100, 0.5)
    const key = `${inc.provider}/${inc.model}`
    if (!byModel.has(key)) byModel.set(key, [])
    byModel.get(key)!.push({ leftPct, widthPct, ongoing: inc.end === null })
  }
  return [...byModel.entries()].map(([model, bars]) => ({ model, bars }))
}
