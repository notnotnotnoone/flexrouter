import { describe, it, expect } from 'vitest'
import { mergeSegments } from './timeline'

describe('mergeSegments', () => {
  it('run-length merges adjacent equal states', () => {
    const merged = mergeSegments([
      { start: 'a', end: 'a', state: 'up' }, { start: 'b', end: 'b', state: 'up' },
      { start: 'c', end: 'c', state: 'down' }, { start: 'd', end: 'd', state: 'up' },
    ] as any)
    expect(merged).toEqual([
      { state: 'up', count: 2 }, { state: 'down', count: 1 }, { state: 'up', count: 1 },
    ])
  })
  it('handles empty', () => expect(mergeSegments([])).toEqual([]))
})
