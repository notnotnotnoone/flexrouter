import type { UptimeModel } from './statsTypes'

export function mergeSegments(segments: UptimeModel['segments']): { state: string; count: number }[] {
  const out: { state: string; count: number }[] = []
  for (const s of segments) {
    const last = out[out.length - 1]
    if (last && last.state === s.state) last.count++
    else out.push({ state: s.state, count: 1 })
  }
  return out
}
