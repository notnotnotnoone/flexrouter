import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { usePolling } from './usePolling'
import { FetchError } from '../api'
import { clearDiagnostics, getDiagnostics } from '../lib/diagnostics'

beforeEach(() => clearDiagnostics())
afterEach(() => vi.useRealTimers())

describe('usePolling', () => {
  it('loads then becomes ok with data', async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }))
    const { result } = renderHook(() => usePolling(fetcher, { intervalMs: 1000, endpoint: '/api/x' }))
    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current.status).toBe('ok'))
    expect(result.current.data).toEqual({ n: 1 })
    expect(result.current.lastUpdated).toBeTruthy()
  })

  it('keeps last data and records diagnostics on error', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockRejectedValue(new FetchError('HTTP 503', 503, '/api/x'))
    const { result } = renderHook(() => usePolling(fetcher, { intervalMs: 100, endpoint: '/api/x' }))
    await waitFor(() => expect(result.current.status).toBe('ok'))
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.data).toEqual({ n: 1 }) // last good data retained
    expect(result.current.error?.status).toBe(503)
    expect(getDiagnostics().length).toBeGreaterThan(0)
  })

  it('marks unreachable after failureThreshold consecutive failures', async () => {
    const fetcher = vi.fn(async () => { throw new FetchError('down', 0, '/api/x') })
    const { result } = renderHook(() =>
      usePolling(fetcher, { intervalMs: 5, endpoint: '/api/x', failureThreshold: 3 }))
    await waitFor(() => expect(result.current.unreachable).toBe(true), { timeout: 1000 })
    expect(result.current.consecutiveFailures).toBeGreaterThanOrEqual(3)
  })
})
