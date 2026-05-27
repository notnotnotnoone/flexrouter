const BASE = ''

async function fetchJSON(url: string): Promise<any> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`)
  return res.json()
}

async function postJSON(url: string, body: object): Promise<any> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`)
  return res.json()
}

export async function fetchStatus() {
  return fetchJSON(`${BASE}/api/status`)
}

export async function fetchLogs(n = 50) {
  return fetchJSON(`${BASE}/api/logs?n=${n}`)
}

export async function fetchConfig() {
  return fetchJSON(`${BASE}/api/config`)
}

export async function postConfig(cfg: object) {
  return postJSON(`${BASE}/api/config`, cfg)
}

export async function chatCompletion(messages: object[], model: string) {
  return postJSON('/v1/chat/completions', { model, messages })
}
