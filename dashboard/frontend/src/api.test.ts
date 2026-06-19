import { describe, it, expect, vi, afterEach } from 'vitest'
import { fetchJSON, FetchError } from './api'

afterEach(() => vi.restoreAllMocks())

describe('fetchJSON', () => {
  it('returns parsed json on ok', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"a":1}', { status: 200 })))
    expect(await fetchJSON('/x')).toEqual({ a: 1 })
  })

  it('throws FetchError with status + url on non-ok', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 503 })))
    await expect(fetchJSON('/api/stats')).rejects.toMatchObject({
      name: 'FetchError', status: 503, url: '/api/stats',
    })
  })

  it('throws a timeout FetchError when the request hangs', async () => {
    vi.stubGlobal('fetch', vi.fn((_url: string, opts: any) =>
      new Promise((_res, rej) => {
        opts.signal.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
      })))
    await expect(fetchJSON('/slow', { timeoutMs: 10 })).rejects.toMatchObject({
      name: 'FetchError', status: 0,
    })
  })
})
