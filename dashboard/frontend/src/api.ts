const BASE = ''

export async function fetchStatus() {
  const res = await fetch(`${BASE}/api/status`)
  return res.json()
}

export async function fetchLogs(n = 50) {
  const res = await fetch(`${BASE}/api/logs?n=${n}`)
  return res.json()
}

export async function fetchConfig() {
  const res = await fetch(`${BASE}/api/config`)
  return res.json()
}

export async function postConfig(cfg: object) {
  const res = await fetch(`${BASE}/api/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  return res.json()
}

export async function chatCompletion(messages: object[], model: string) {
  const res = await fetch('/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, messages }),
  })
  return res.json()
}
