const BASE = ''

export class FetchError extends Error {
  status: number
  url: string
  constructor(message: string, status: number, url: string) {
    super(message)
    this.name = 'FetchError'
    this.status = status
    this.url = url
  }
}

export async function fetchJSON(url: string, opts: { timeoutMs?: number } = {}): Promise<any> {
  const timeoutMs = opts.timeoutMs ?? 8000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url, { signal: controller.signal })
    if (!res.ok) throw new FetchError(`HTTP ${res.status}`, res.status, url)
    return await res.json()
  } catch (err: any) {
    if (err instanceof FetchError) throw err
    if (err?.name === 'AbortError') throw new FetchError(`Timed out after ${timeoutMs}ms`, 0, url)
    throw new FetchError(err?.message || 'Network error', 0, url)
  } finally {
    clearTimeout(timer)
  }
}

async function postJSON(url: string, body: object): Promise<any> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new FetchError(`HTTP ${res.status}`, res.status, url)
  return res.json()
}

export const fetchStatus = () => fetchJSON(`${BASE}/api/status`)
export const fetchLogs = (n = 50) => fetchJSON(`${BASE}/api/logs?n=${n}`)
export const fetchConfig = () => fetchJSON(`${BASE}/api/config`)
export const fetchStats = () => fetchJSON(`${BASE}/api/stats`)
export const fetchUptime = () => fetchJSON(`${BASE}/api/uptime`)
export const fetchConfigValidation = () => fetchJSON(`${BASE}/api/config/validate`)
export const fetchHealthCurrent = () => fetchJSON(`${BASE}/api/health/current`)
export const postConfig = (cfg: object) => postJSON(`${BASE}/api/config`, cfg)
export const chatCompletion = (messages: object[], model: string) =>
  postJSON('/v1/chat/completions', { model, messages })
