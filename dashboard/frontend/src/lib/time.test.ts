import { describe, it, expect } from 'vitest'
import { timeAgo } from './time'

describe('timeAgo', () => {
  const now = 1_000_000_000_000
  it('shows just now under 2s', () => expect(timeAgo(now - 500, now)).toBe('just now'))
  it('shows seconds', () => expect(timeAgo(now - 3000, now)).toBe('3s ago'))
  it('shows minutes', () => expect(timeAgo(now - 120000, now)).toBe('2m ago'))
  it('shows hours', () => expect(timeAgo(now - 3600000, now)).toBe('1h ago'))
})
