import { describe, it, expect } from 'vitest'
import { toLanes } from './swimlane'

const now = new Date('2026-06-19T12:00:00Z').getTime()

describe('toLanes', () => {
  it('positions a closed incident within the 24h window', () => {
    const lanes = toLanes([{
      provider: 'groq', model: 'llama', event_type: 'penalized',
      start: '2026-06-19T06:00:00Z', end: '2026-06-19T07:00:00Z', duration_seconds: 3600,
    }], now)
    expect(lanes).toHaveLength(1)
    const bar = lanes[0].bars[0]
    expect(bar.leftPct).toBeCloseTo(75, 0)
    expect(bar.widthPct).toBeCloseTo(100 / 24, 1)
    expect(bar.ongoing).toBe(false)
  })
  it('marks an open incident as ongoing to now', () => {
    const lanes = toLanes([{
      provider: 'groq', model: 'llama', event_type: 'penalized',
      start: '2026-06-19T11:30:00Z', end: null, duration_seconds: null,
    }], now)
    expect(lanes[0].bars[0].ongoing).toBe(true)
  })
})
