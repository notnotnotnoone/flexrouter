import { useEffect, useRef, useState, useCallback } from 'react'
import { FetchError } from '../api'
import { recordDiagnostic } from '../lib/diagnostics'

export type PollStatus = 'loading' | 'ok' | 'stale' | 'error'

export interface PollState<T> {
  data: T | null
  status: PollStatus
  error: FetchError | null
  lastUpdated: number | null
  inFlight: boolean
  consecutiveFailures: number
  unreachable: boolean
  refresh: () => void
}

interface Opts {
  intervalMs: number
  endpoint: string
  staleMultiplier?: number
  failureThreshold?: number
}

export function usePolling<T>(fetcher: () => Promise<T>, opts: Opts): PollState<T> {
  const { intervalMs, endpoint, staleMultiplier = 2, failureThreshold = 3 } = opts
  const [data, setData] = useState<T | null>(null)
  const [status, setStatus] = useState<PollStatus>('loading')
  const [error, setError] = useState<FetchError | null>(null)
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [inFlight, setInFlight] = useState(false)
  const [failures, setFailures] = useState(0)
  const [tick, setTick] = useState(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const load = useCallback(async () => {
    setInFlight(true)
    try {
      const result = await fetcherRef.current()
      setData(result)
      setLastUpdated(Date.now())
      setError(null)
      setFailures(0)
      setStatus('ok')
    } catch (err) {
      const fe = err instanceof FetchError ? err : new FetchError(String(err), 0, endpoint)
      setError(fe)
      setFailures(f => f + 1)
      setStatus('error')
      recordDiagnostic({ endpoint, status: fe.status, message: fe.message })
    } finally {
      setInFlight(false)
    }
  }, [endpoint])

  useEffect(() => {
    load()
    const id = setInterval(load, intervalMs)
    return () => clearInterval(id)
  }, [load, intervalMs])

  // Staleness clock: re-evaluate on an interval so 'ok' can decay to 'stale'.
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), Math.max(intervalMs, 1000))
    return () => clearInterval(id)
  }, [intervalMs])

  let effective: PollStatus = status
  if (status === 'ok' && lastUpdated && Date.now() - lastUpdated > intervalMs * staleMultiplier) {
    effective = 'stale'
  }
  void tick // consumed to force recompute on clock tick

  return {
    data, status: effective, error, lastUpdated, inFlight,
    consecutiveFailures: failures, unreachable: failures >= failureThreshold,
    refresh: load,
  }
}
