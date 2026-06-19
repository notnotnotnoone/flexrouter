import { describe, it, expect, beforeEach } from 'vitest'
import { recordDiagnostic, getDiagnostics, clearDiagnostics, subscribeDiagnostics } from './diagnostics'

beforeEach(() => clearDiagnostics())

describe('diagnostics store', () => {
  it('records and reads entries', () => {
    recordDiagnostic({ endpoint: '/api/stats', status: 503, message: 'down' })
    const all = getDiagnostics()
    expect(all).toHaveLength(1)
    expect(all[0]).toMatchObject({ endpoint: '/api/stats', status: 503, message: 'down' })
    expect(all[0].ts).toBeTruthy()
  })

  it('notifies subscribers', () => {
    let count = 0
    const unsub = subscribeDiagnostics(() => { count++ })
    recordDiagnostic({ endpoint: '/x', status: 0, message: 'boom' })
    expect(count).toBe(1)
    unsub()
    recordDiagnostic({ endpoint: '/y', status: 0, message: 'boom2' })
    expect(count).toBe(1)
  })

  it('caps at 200 entries', () => {
    for (let i = 0; i < 250; i++) recordDiagnostic({ endpoint: '/x', status: 0, message: String(i) })
    const all = getDiagnostics()
    expect(all).toHaveLength(200)
    expect(all[all.length - 1].message).toBe('249')
  })
})
