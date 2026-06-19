# Dashboard Overhaul — Plan 2: Frontend Observability Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the consumer-style frontend with an operational dashboard whose every data source surfaces loading/stale/error states, never hides an error, and never looks frozen — plus the ported animated KPI cards and a config-health banner.

**Architecture:** One shared `usePolling` hook drives every async source; a module-level diagnostics store captures every error; global chrome (status bar with ticking clock, diagnostics console, watchdog banner) proves liveness at all times. A small backend endpoint exposes the current health snapshot for the KPI cards.

**Tech Stack:** React 18 + TypeScript + Vite 5, Tailwind v4 (CSS-first `@theme`, shadcn oklch tokens), shadcn/ui + Radix, lucide-react, Geist. Vitest + @testing-library/react for logic; run-the-app verification for visual chrome. Backend task: Python 3 stdlib + pytest.

## Global Constraints

- Use shadcn semantic tokens for color: `bg-card`, `text-muted-foreground`, `border-border`, `bg-destructive`, etc. Add only `--color-success` / `--color-warning` (operational green/amber) — see Task 4.
- Icons from `lucide-react` only. Class composition via `cn()` from `@/lib/utils`.
- Import paths use the `@/` alias (→ `src/`).
- Frontend commands run from `dashboard/frontend/`.
- No silent error handling. A bare `catch {}` or `.catch(() => {})` anywhere is a defect. Every caught error reaches the diagnostics store.
- All fetches use an `AbortController` timeout (default 8000 ms). A hung request must surface as a timeout error, never an infinite spinner.
- Staleness threshold: data older than 2× the poll interval is `stale`. Watchdog threshold: 3 consecutive failed polls → `unreachable`.
- Backend (Task 1) follows Plan 1 conventions: stdlib only, atomic writes, ISO-8601 UTC `Z` timestamps.

---

## File Structure

- Modify `flexrouter/dashboard/api.py` + `server.py` + `flexrouter/health_history.py` — expose latest health snapshot (Task 1).
- Modify `dashboard/frontend/package.json`, add `dashboard/frontend/vitest.config.ts`, `dashboard/frontend/src/test/setup.ts` — test harness (Task 2).
- Modify `dashboard/frontend/src/api.ts` — typed client + timeouts + new endpoints (Task 3).
- Create `dashboard/frontend/src/lib/diagnostics.ts` — global error log (Task 5).
- Create `dashboard/frontend/src/hooks/usePolling.ts` — the core hook (Tasks 4, 6).
- Modify `dashboard/frontend/src/index.css` — success/warning tokens (Task 7).
- Create `dashboard/frontend/src/components/StatusBar.tsx`, `DiagnosticsConsole.tsx`, `UnreachableBanner.tsx`, `ConfigHealthBanner.tsx` (Tasks 8-11).
- Create `dashboard/frontend/src/components/kpi/CurrentModelCard.tsx`, `ModelsOnlineCard.tsx`, `ProvidersOnlineCard.tsx` + `kpi/kpi.css` (Task 12).
- Modify `dashboard/frontend/src/App.tsx` and the existing tabs (Task 13).
- Tests co-located as `*.test.ts(x)`.

---

## Task 1: Backend — expose current health snapshot

**Files:**
- Modify: `flexrouter/health_history.py` (add `latest()`)
- Modify: `flexrouter/dashboard/api.py` (add `get_health_current`)
- Modify: `flexrouter/dashboard/server.py` (route `/api/health/current`)
- Test: `tests/test_health_current.py`

**Why:** The KPI cards need *current* per-model/per-provider up-counts. `/api/status` (health.json) only carries cost. The last line of `health_history.jsonl` is the current snapshot; expose it.

**Interfaces:**
- Produces: `HealthHistory.latest() -> dict | None` (last sample or None); `api.get_health_current(state_dir) -> dict` returning the latest sample or `{"models": {}, "providers": {}}`; route `GET /api/health/current`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health_current.py
from flexrouter.health_history import HealthHistory
from flexrouter.dashboard import api

def test_latest_returns_last_sample(tmp_path):
    hh = HealthHistory(str(tmp_path))
    hh.record({"models": {"groq/llama": {"status": "up"}}, "providers": {"groq": {"models_up": 1, "models_total": 1}}})
    hh.record({"models": {"groq/llama": {"status": "penalized"}}, "providers": {"groq": {"models_up": 0, "models_total": 1}}})
    assert hh.latest()["models"]["groq/llama"]["status"] == "penalized"

def test_latest_none_when_empty(tmp_path):
    assert HealthHistory(str(tmp_path)).latest() is None

def test_get_health_current_defaults(tmp_path):
    out = api.get_health_current(str(tmp_path))
    assert out == {"models": {}, "providers": {}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health_current.py -v`
Expected: FAIL with `AttributeError: 'HealthHistory' object has no attribute 'latest'`

- [ ] **Step 3: Implement**

Add to `flexrouter/health_history.py`:

```python
    def latest(self) -> Optional[dict]:
        samples = self.read()
        return samples[-1] if samples else None
```

Add to `flexrouter/dashboard/api.py`:

```python
from flexrouter.health_history import HealthHistory


def get_health_current(state_dir: str) -> dict:
    latest = HealthHistory(state_dir).latest()
    if not latest:
        return {"models": {}, "providers": {}}
    return {"models": latest.get("models", {}), "providers": latest.get("providers", {})}
```

In `flexrouter/dashboard/server.py`, add to the import list and a route in `_handle_api_get`:

```python
            elif self.path == "/api/health/current":
                self._send_json(get_health_current(state))
```

(Add `get_health_current` to the `from flexrouter.dashboard.api import (...)` line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_health_current.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/health_history.py flexrouter/dashboard/api.py flexrouter/dashboard/server.py tests/test_health_current.py
git commit -m "feat: /api/health/current exposes latest health snapshot"
```

---

## Task 2: Vitest + Testing Library harness

**Files:**
- Modify: `dashboard/frontend/package.json`
- Create: `dashboard/frontend/vitest.config.ts`
- Create: `dashboard/frontend/src/test/setup.ts`
- Test: `dashboard/frontend/src/test/smoke.test.ts`

**Interfaces:**
- Produces: an `npm test` script running Vitest in jsdom with `@testing-library/jest-dom` matchers and the `@/` alias.

- [ ] **Step 1: Install dev dependencies**

Run from `dashboard/frontend/`:
```bash
npm install -D vitest@^2 jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

- [ ] **Step 2: Add the test script to `package.json`**

In `"scripts"` add:
```json
    "test": "vitest run",
    "test:watch": "vitest"
```

- [ ] **Step 3: Create `vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'
import { resolve } from 'path'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': resolve(__dirname, './src') } },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
```

- [ ] **Step 4: Create `src/test/setup.ts`**

```ts
import '@testing-library/jest-dom/vitest'
```

- [ ] **Step 5: Write and run a smoke test**

```ts
// src/test/smoke.test.ts
import { describe, it, expect } from 'vitest'

describe('harness', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2)
  })
})
```

Run: `npm test`
Expected: PASS (1 test). If it fails, fix config before proceeding.

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/package.json dashboard/frontend/package-lock.json dashboard/frontend/vitest.config.ts dashboard/frontend/src/test/
git commit -m "test: add Vitest + Testing Library harness"
```

---

## Task 3: Typed API client with timeouts

**Files:**
- Modify: `dashboard/frontend/src/api.ts`
- Test: `dashboard/frontend/src/api.test.ts`

**Interfaces:**
- Produces: `class FetchError extends Error { status: number; url: string }`; `fetchJSON(url, { timeoutMs }) -> Promise<any>` (throws `FetchError` on non-ok or timeout); existing `fetchStatus/fetchLogs/fetchConfig/postConfig/chatCompletion` plus new `fetchStats()`, `fetchUptime()`, `fetchConfigValidation()`, `fetchHealthCurrent()`.

- [ ] **Step 1: Write the failing test**

```ts
// src/api.test.ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- api`
Expected: FAIL — `FetchError` not exported / no timeout behavior.

- [ ] **Step 3: Rewrite `src/api.ts`**

```ts
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- api`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/api.ts dashboard/frontend/src/api.test.ts
git commit -m "feat: typed API client with AbortController timeouts + new endpoints"
```

---

## Task 4: Diagnostics store

**Files:**
- Create: `dashboard/frontend/src/lib/diagnostics.ts`
- Test: `dashboard/frontend/src/lib/diagnostics.test.ts`

**Interfaces:**
- Produces: `recordDiagnostic(entry: { endpoint: string; status: number; message: string })`; `getDiagnostics(): DiagEntry[]`; `subscribeDiagnostics(fn): () => void`; `clearDiagnostics()`. `DiagEntry = { id: number; ts: string; endpoint: string; status: number; message: string }`. Module-level store, newest-last, capped at 200.

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/diagnostics.test.ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- diagnostics`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/lib/diagnostics.ts
export interface DiagEntry {
  id: number
  ts: string
  endpoint: string
  status: number
  message: string
}

const MAX = 200
let entries: DiagEntry[] = []
let nextId = 1
const listeners = new Set<() => void>()

export function recordDiagnostic(e: { endpoint: string; status: number; message: string }): void {
  entries = [...entries, { id: nextId++, ts: new Date().toISOString(), ...e }].slice(-MAX)
  listeners.forEach(fn => fn())
}

export function getDiagnostics(): DiagEntry[] {
  return entries
}

export function clearDiagnostics(): void {
  entries = []
  listeners.forEach(fn => fn())
}

export function subscribeDiagnostics(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- diagnostics`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/diagnostics.ts dashboard/frontend/src/lib/diagnostics.test.ts
git commit -m "feat: global diagnostics store (nothing swallowed)"
```

---

## Task 5: usePolling hook

**Files:**
- Create: `dashboard/frontend/src/hooks/usePolling.ts`
- Test: `dashboard/frontend/src/hooks/usePolling.test.tsx`

**Interfaces:**
- Consumes: `FetchError` (Task 3), `recordDiagnostic` (Task 4).
- Produces:
  ```ts
  type PollStatus = 'loading' | 'ok' | 'stale' | 'error'
  interface PollState<T> {
    data: T | null; status: PollStatus; error: FetchError | null
    lastUpdated: number | null; inFlight: boolean; consecutiveFailures: number; unreachable: boolean
    refresh: () => void
  }
  function usePolling<T>(fetcher: () => Promise<T>, opts: { intervalMs: number; endpoint: string; staleMultiplier?: number; failureThreshold?: number }): PollState<T>
  ```
  On success: `status='ok'`, store data + `lastUpdated`, reset failures. On error: keep previous `data`, `status='error'`, push to diagnostics, increment failures; `unreachable` once failures ≥ threshold (default 3). `stale` derived when `now - lastUpdated > intervalMs * staleMultiplier` (default 2) while otherwise ok.

- [ ] **Step 1: Write the failing test**

```tsx
// src/hooks/usePolling.test.tsx
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
    const { result } = renderHook(() => usePolling(fetcher, { intervalMs: 5, endpoint: '/api/x' }))
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- usePolling`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/hooks/usePolling.ts
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
  void tick // dependency to force recompute

  return {
    data, status: effective, error, lastUpdated, inFlight,
    consecutiveFailures: failures, unreachable: failures >= failureThreshold,
    refresh: load,
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- usePolling`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/hooks/usePolling.ts dashboard/frontend/src/hooks/usePolling.test.tsx
git commit -m "feat: usePolling hook (loading/ok/stale/error, watchdog, diagnostics)"
```

---

## Task 6: Time-ago + staleness helpers

**Files:**
- Create: `dashboard/frontend/src/lib/time.ts`
- Test: `dashboard/frontend/src/lib/time.test.ts`

**Interfaces:**
- Produces: `timeAgo(epochMs: number, now?: number): string` → `"3s ago"`, `"2m ago"`, `"1h ago"`, `"just now"`.

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/time.test.ts
import { describe, it, expect } from 'vitest'
import { timeAgo } from './time'

describe('timeAgo', () => {
  const now = 1_000_000_000_000
  it('shows just now under 2s', () => expect(timeAgo(now - 500, now)).toBe('just now'))
  it('shows seconds', () => expect(timeAgo(now - 3000, now)).toBe('3s ago'))
  it('shows minutes', () => expect(timeAgo(now - 120000, now)).toBe('2m ago'))
  it('shows hours', () => expect(timeAgo(now - 3600000, now)).toBe('1h ago'))
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- time`)

- [ ] **Step 3: Implement**

```ts
// src/lib/time.ts
export function timeAgo(epochMs: number, now: number = Date.now()): string {
  const s = Math.floor((now - epochMs) / 1000)
  if (s < 2) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- time`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/time.ts dashboard/frontend/src/lib/time.test.ts
git commit -m "feat: timeAgo helper"
```

---

## Task 7: Operational color tokens

**Files:**
- Modify: `dashboard/frontend/src/index.css`

- [ ] **Step 1: Add success/warning tokens**

In the `@theme` block (after `--color-brand`):
```css
  --color-success: #16a34a;
  --color-warning: #d97706;
```
In `:root` and `.dark` you may keep these constant (operational colors read identically in both modes). No test — verified by use in later tasks.

- [ ] **Step 2: Verify the build still compiles**

Run: `npm run build`
Expected: build succeeds, output written to `flexrouter/dashboard/static`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/index.css
git commit -m "feat: operational success/warning color tokens"
```

---

## Task 8: StatusBar (ticking clock = liveness)

**Files:**
- Create: `dashboard/frontend/src/components/StatusBar.tsx`
- Test: `dashboard/frontend/src/components/StatusBar.test.tsx`

**Interfaces:**
- Consumes: `PollState` from a status poll, `timeAgo`.
- Produces: `<StatusBar status={pollState} version={string} onOpenDiagnostics={() => void} diagnosticsCount={number} />`. Renders a connection dot (green ok / amber stale / red error|unreachable), `timeAgo(lastUpdated)`, a 1-second ticking clock, version, and a diagnostics button with count.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/StatusBar.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBar } from './StatusBar'

const base = {
  data: {}, error: null, lastUpdated: Date.now(), inFlight: false,
  consecutiveFailures: 0, unreachable: false, refresh: () => {},
}

describe('StatusBar', () => {
  it('shows connected label when ok', () => {
    render(<StatusBar status={{ ...base, status: 'ok' } as any} version="0.1.0" diagnosticsCount={0} onOpenDiagnostics={() => {}} />)
    expect(screen.getByText(/connected/i)).toBeInTheDocument()
  })
  it('shows unreachable when watchdog tripped', () => {
    render(<StatusBar status={{ ...base, status: 'error', unreachable: true } as any} version="0.1.0" diagnosticsCount={2} onOpenDiagnostics={() => {}} />)
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- StatusBar`)

- [ ] **Step 3: Implement**

```tsx
// src/components/StatusBar.tsx
import { useEffect, useState } from 'react'
import { Activity, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { timeAgo } from '@/lib/time'
import type { PollState } from '@/hooks/usePolling'

export function StatusBar({ status, version, onOpenDiagnostics, diagnosticsCount }: {
  status: PollState<unknown>; version: string; onOpenDiagnostics: () => void; diagnosticsCount: number
}) {
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const dot = status.unreachable || status.status === 'error' ? 'bg-[var(--color-destructive)]'
    : status.status === 'stale' ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-success)]'
  const label = status.unreachable ? 'Backend unreachable'
    : status.status === 'error' ? 'Error'
    : status.status === 'stale' ? 'Stale' : 'Connected'

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-border bg-card text-xs text-muted-foreground tabular-nums">
      <span className="flex items-center gap-1.5 font-medium text-foreground">
        <span className={cn('w-2 h-2 rounded-full', dot, status.inFlight && 'animate-pulse')} />
        {label}
      </span>
      {status.lastUpdated && <span>synced {timeAgo(status.lastUpdated)}</span>}
      <span className="flex items-center gap-1"><Activity className="w-3 h-3" />{new Date().toLocaleTimeString()}</span>
      <span className="ml-auto">v{version}</span>
      <button onClick={onOpenDiagnostics} className="flex items-center gap-1 hover:text-foreground">
        <AlertTriangle className="w-3 h-3" />Diagnostics{diagnosticsCount > 0 && ` (${diagnosticsCount})`}
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- StatusBar`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/StatusBar.tsx dashboard/frontend/src/components/StatusBar.test.tsx
git commit -m "feat: StatusBar with connection dot + ticking clock"
```

---

## Task 9: DiagnosticsConsole drawer

**Files:**
- Create: `dashboard/frontend/src/components/DiagnosticsConsole.tsx`
- Test: `dashboard/frontend/src/components/DiagnosticsConsole.test.tsx`

**Interfaces:**
- Consumes: `getDiagnostics`, `subscribeDiagnostics`, `clearDiagnostics`.
- Produces: `<DiagnosticsConsole open={boolean} onClose={() => void} />`. Subscribes to the store; lists entries (newest first) with timestamp, endpoint, status, message; a Clear button. Hidden when `open` is false.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/DiagnosticsConsole.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DiagnosticsConsole } from './DiagnosticsConsole'
import { recordDiagnostic, clearDiagnostics } from '../lib/diagnostics'

beforeEach(() => clearDiagnostics())

describe('DiagnosticsConsole', () => {
  it('lists recorded errors when open', () => {
    recordDiagnostic({ endpoint: '/api/stats', status: 503, message: 'service down' })
    render(<DiagnosticsConsole open onClose={() => {}} />)
    expect(screen.getByText(/service down/)).toBeInTheDocument()
    expect(screen.getByText(/\/api\/stats/)).toBeInTheDocument()
  })
  it('renders nothing when closed', () => {
    recordDiagnostic({ endpoint: '/x', status: 0, message: 'boom' })
    const { container } = render(<DiagnosticsConsole open={false} onClose={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- DiagnosticsConsole`)

- [ ] **Step 3: Implement**

```tsx
// src/components/DiagnosticsConsole.tsx
import { useSyncExternalStore } from 'react'
import { X, Trash2 } from 'lucide-react'
import { getDiagnostics, subscribeDiagnostics, clearDiagnostics } from '../lib/diagnostics'

export function DiagnosticsConsole({ open, onClose }: { open: boolean; onClose: () => void }) {
  const entries = useSyncExternalStore(subscribeDiagnostics, getDiagnostics)
  if (!open) return null
  const ordered = [...entries].reverse()
  return (
    <div className="fixed inset-y-0 right-0 w-[420px] max-w-full bg-card border-l border-border shadow-xl z-50 flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="font-semibold text-sm">Diagnostics ({entries.length})</span>
        <div className="flex items-center gap-2">
          <button onClick={clearDiagnostics} className="text-muted-foreground hover:text-foreground"><Trash2 className="w-4 h-4" /></button>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="w-4 h-4" /></button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1 font-mono text-xs">
        {ordered.length === 0 && <p className="text-muted-foreground p-4">No errors recorded.</p>}
        {ordered.map(e => (
          <div key={e.id} className="border border-border rounded-md p-2">
            <div className="flex justify-between text-muted-foreground">
              <span>{e.endpoint}</span>
              <span className="text-[var(--color-destructive)]">{e.status === 0 ? 'NET' : e.status}</span>
            </div>
            <div className="text-foreground break-words">{e.message}</div>
            <div className="text-muted-foreground/70">{e.ts}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- DiagnosticsConsole`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/DiagnosticsConsole.tsx dashboard/frontend/src/components/DiagnosticsConsole.test.tsx
git commit -m "feat: DiagnosticsConsole drawer (every error visible)"
```

---

## Task 10: UnreachableBanner

**Files:**
- Create: `dashboard/frontend/src/components/UnreachableBanner.tsx`
- Test: `dashboard/frontend/src/components/UnreachableBanner.test.tsx`

**Interfaces:**
- Produces: `<UnreachableBanner error={FetchError | null} onRetry={() => void} />`. Renders a full-width red bar with the raw error message and a Retry button. Renders nothing if `error` is null.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/UnreachableBanner.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UnreachableBanner } from './UnreachableBanner'
import { FetchError } from '../api'

describe('UnreachableBanner', () => {
  it('shows the raw error and retries', async () => {
    const onRetry = vi.fn()
    render(<UnreachableBanner error={new FetchError('Timed out after 8000ms', 0, '/api/status')} onRetry={onRetry} />)
    expect(screen.getByText(/BACKEND UNREACHABLE/i)).toBeInTheDocument()
    expect(screen.getByText(/Timed out after 8000ms/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
  it('renders nothing without an error', () => {
    const { container } = render(<UnreachableBanner error={null} onRetry={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- UnreachableBanner`)

- [ ] **Step 3: Implement**

```tsx
// src/components/UnreachableBanner.tsx
import { WifiOff } from 'lucide-react'
import type { FetchError } from '../api'

export function UnreachableBanner({ error, onRetry }: { error: FetchError | null; onRetry: () => void }) {
  if (!error) return null
  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-[var(--color-destructive)] text-white text-sm">
      <WifiOff className="w-4 h-4 shrink-0" />
      <span className="font-semibold">BACKEND UNREACHABLE</span>
      <span className="font-mono text-xs opacity-90 truncate">{error.url}: {error.message}</span>
      <button onClick={onRetry} className="ml-auto px-2 py-0.5 rounded bg-white/20 hover:bg-white/30 text-xs font-medium">
        Retry
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- UnreachableBanner`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/UnreachableBanner.tsx dashboard/frontend/src/components/UnreachableBanner.test.tsx
git commit -m "feat: UnreachableBanner with raw error + retry"
```

---

## Task 11: ConfigHealthBanner

**Files:**
- Create: `dashboard/frontend/src/components/ConfigHealthBanner.tsx`
- Test: `dashboard/frontend/src/components/ConfigHealthBanner.test.tsx`

**Interfaces:**
- Consumes: `fetchConfigValidation` via `usePolling`.
- Produces: `<ConfigHealthBanner />`. Polls `/api/config/validate` every 10s. Red block listing each error; amber block listing each warning. Renders nothing when both lists are empty.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/ConfigHealthBanner.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ConfigHealthBanner } from './ConfigHealthBanner'
import * as api from '../api'

beforeEach(() => vi.restoreAllMocks())

describe('ConfigHealthBanner', () => {
  it('renders errors and warnings', async () => {
    vi.spyOn(api, 'fetchConfigValidation').mockResolvedValue({
      errors: ["tiers.default[0].provider: 'nope' not defined in providers"],
      warnings: ["tiers.default[8]: 'gpt-oss-safeguard-20b' matches a non-chat name pattern"],
    })
    render(<ConfigHealthBanner />)
    await waitFor(() => expect(screen.getByText(/not defined in providers/)).toBeInTheDocument())
    expect(screen.getByText(/non-chat name pattern/)).toBeInTheDocument()
  })

  it('renders nothing when clean', async () => {
    vi.spyOn(api, 'fetchConfigValidation').mockResolvedValue({ errors: [], warnings: [] })
    const { container } = render(<ConfigHealthBanner />)
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- ConfigHealthBanner`)

- [ ] **Step 3: Implement**

```tsx
// src/components/ConfigHealthBanner.tsx
import { AlertCircle, AlertTriangle } from 'lucide-react'
import { fetchConfigValidation } from '../api'
import { usePolling } from '@/hooks/usePolling'

interface Validation { errors: string[]; warnings: string[] }

export function ConfigHealthBanner() {
  const { data } = usePolling<Validation>(fetchConfigValidation, { intervalMs: 10000, endpoint: '/api/config/validate' })
  const errors = data?.errors ?? []
  const warnings = data?.warnings ?? []
  if (errors.length === 0 && warnings.length === 0) return null
  return (
    <div className="space-y-2 mb-4">
      {errors.length > 0 && (
        <div className="rounded-lg border border-[var(--color-destructive)] bg-[var(--color-destructive)]/10 p-3">
          <div className="flex items-center gap-2 font-semibold text-[var(--color-destructive)] text-sm mb-1">
            <AlertCircle className="w-4 h-4" />Config errors ({errors.length})
          </div>
          <ul className="text-xs font-mono space-y-0.5 text-foreground">
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-lg border border-[var(--color-warning)] bg-[var(--color-warning)]/10 p-3">
          <div className="flex items-center gap-2 font-semibold text-[var(--color-warning)] text-sm mb-1">
            <AlertTriangle className="w-4 h-4" />Config warnings ({warnings.length})
          </div>
          <ul className="text-xs font-mono space-y-0.5 text-foreground">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- ConfigHealthBanner`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/ConfigHealthBanner.tsx dashboard/frontend/src/components/ConfigHealthBanner.test.tsx
git commit -m "feat: ConfigHealthBanner surfaces validation errors + warnings"
```

---

## Task 12: Animated KPI cards (ported from modelrelay)

**Files:**
- Create: `dashboard/frontend/src/components/kpi/kpi.css`
- Create: `dashboard/frontend/src/components/kpi/KpiCard.tsx` (shell)
- Create: `dashboard/frontend/src/components/kpi/ModelsOnlineCard.tsx`
- Create: `dashboard/frontend/src/components/kpi/ProvidersOnlineCard.tsx`
- Create: `dashboard/frontend/src/components/kpi/TopModelCard.tsx`
- Test: `dashboard/frontend/src/components/kpi/kpi.test.tsx`

**Interfaces:**
- Consumes: `/api/health/current` (Task 1) for models/providers counts; `/api/stats` `distribution.top_models[0]` for the top model.
- Produces: three cards. `ModelsOnlineCard` (constellation SVG; bright nodes = online), `ProvidersOnlineCard` (hub-network SVG), `TopModelCard` (spinning hexagon + pulse; shows most-requested model — flexrouter has no single "current" model, so this reframes modelrelay's "Current Model"). Each renders a numeric/string value and a label.

> **Note:** Visual SVG animation is verified by running the app (Task 13 mounts them). The unit test only asserts the value/label render — animation correctness is a run-to-verify concern.

- [ ] **Step 1: Write the failing value-render test**

```tsx
// src/components/kpi/kpi.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { KpiCard } from './KpiCard'

describe('KpiCard', () => {
  it('renders label and value', () => {
    render(<KpiCard label="Models Online" value={42}>{null}</KpiCard>)
    expect(screen.getByText('Models Online')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- kpi`)

- [ ] **Step 3: Implement the shell + CSS, then the three cards**

`src/components/kpi/kpi.css` — port modelrelay's animation classes (constellation, network, hexagon) adapted to theme vars:

```css
.kpi-bg-svg { position:absolute; inset:0; width:100%; height:100%; z-index:0; pointer-events:none; opacity:0.6; }
.star-dim { fill: var(--color-muted-foreground); opacity:0.15; transition: all 1.5s ease-in-out; }
.star-bright { fill: var(--color-brand); animation: twinkle 3s infinite alternate; transition: all 1.5s ease-in-out; }
.constellation-line { stroke: var(--color-brand); stroke-width:0.5; opacity:0.3; transition: all 1.5s ease-in-out; }
.constellation-line-hidden { stroke: var(--color-brand); stroke-width:0.5; opacity:0; transition: all 1.5s ease-in-out; }
.net-node-dim { fill: var(--color-muted-foreground); opacity:0.15; transition: all 1.5s ease-in-out; }
.net-node-bright { fill: var(--color-success); filter: drop-shadow(0 0 2px var(--color-success)); animation: pulse-node 4s infinite alternate; transition: all 1.5s ease-in-out; }
.net-link { stroke: var(--color-muted-foreground); stroke-width:0.5; opacity:0.1; transition: all 1.5s ease-in-out; }
.net-link-active { stroke: var(--color-success); stroke-width:1; opacity:0.6; animation: dash 30s linear infinite; stroke-dasharray:4; transition: all 1.5s ease-in-out; }
.net-hub { fill: var(--color-foreground); filter: drop-shadow(0 0 4px var(--color-foreground)); }
.current-core { fill:none; stroke: var(--color-brand); stroke-width:1; opacity:0.2; transform-origin:center; animation: spin-slow 30s linear infinite; }
.current-pulse { fill: var(--color-brand); opacity:0.1; transform-origin:center; animation: heart-beat 4s ease-in-out infinite alternate; }
@keyframes twinkle { 0%{opacity:0.4} 100%{opacity:1; filter:drop-shadow(0 0 2px var(--color-brand))} }
@keyframes pulse-node { 0%{opacity:0.5; transform:scale(0.9)} 100%{opacity:1; transform:scale(1.1)} }
@keyframes dash { to { stroke-dashoffset:-100 } }
@keyframes spin-slow { 0%{transform:rotate(0)} 100%{transform:rotate(360deg)} }
@keyframes heart-beat { 0%{transform:scale(0.8); opacity:0.05} 100%{transform:scale(1.2); opacity:0.25} }
```

`src/components/kpi/KpiCard.tsx`:

```tsx
import './kpi.css'
import type { ReactNode } from 'react'

export function KpiCard({ label, value, children }: { label: string; value: ReactNode; children: ReactNode }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-card p-5 min-h-[102px]">
      {children}
      <div className="relative z-10">
        <div className="text-sm text-muted-foreground font-medium">{label}</div>
        <div className="text-2xl font-bold tabular-nums tracking-tight truncate">{value}</div>
      </div>
    </div>
  )
}
```

`src/components/kpi/ModelsOnlineCard.tsx` (constellation; port of `drawModelsConstellation`):

```tsx
import { useEffect, useRef } from 'react'
import { KpiCard } from './KpiCard'

export function ModelsOnlineCard({ total, online }: { total: number; online: number }) {
  const ref = useRef<SVGSVGElement>(null)
  useEffect(() => {
    const svg = ref.current; if (!svg) return
    const numStars = Math.min(total, 100), numBright = Math.min(online, numStars)
    if (svg.dataset.total !== String(numStars) || svg.children.length === 0) {
      svg.setAttribute('viewBox', '0 0 240 102'); svg.innerHTML = ''; svg.dataset.total = String(numStars)
      let seed = 42; const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280 }
      const pts = Array.from({ length: numStars }, (_, i) => ({ idx: i, x: 10 + rand() * 220, y: 10 + rand() * 82 }))
      pts.forEach((p, i) => {
        const d = pts.map((q, j) => ({ j, d: Math.hypot(q.x - p.x, q.y - p.y) })).sort((a, b) => a.d - b.d)
        for (let k = 1; k <= 2 && k < d.length; k++) if (d[k].d < 60) {
          const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
          line.setAttribute('x1', String(p.x)); line.setAttribute('y1', String(p.y))
          line.setAttribute('x2', String(pts[d[k].j].x)); line.setAttribute('y2', String(pts[d[k].j].y))
          line.setAttribute('class', 'constellation-line-hidden'); line.dataset.from = String(i); line.dataset.to = String(d[k].j)
          svg.appendChild(line)
        }
      })
      pts.forEach(p => {
        const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
        c.setAttribute('cx', String(p.x)); c.setAttribute('cy', String(p.y)); c.setAttribute('r', '1')
        c.dataset.idx = String(p.idx); c.setAttribute('class', 'star-dim'); svg.appendChild(c)
      })
    }
    const bright = new Set<number>()
    svg.querySelectorAll('circle').forEach(c => {
      const on = Number(c.getAttribute('data-idx')) < numBright
      if (on) bright.add(Number(c.getAttribute('data-idx')))
      c.setAttribute('class', on ? 'star-bright' : 'star-dim'); c.setAttribute('r', on ? '1.5' : '1')
    })
    svg.querySelectorAll('line').forEach(l => {
      const vis = bright.has(Number(l.getAttribute('data-from'))) && bright.has(Number(l.getAttribute('data-to')))
      l.setAttribute('class', vis ? 'constellation-line' : 'constellation-line-hidden')
    })
  }, [total, online])
  return <KpiCard label="Models Online" value={online}><svg ref={ref} className="kpi-bg-svg" preserveAspectRatio="xMidYMid slice" /></KpiCard>
}
```

`src/components/kpi/ProvidersOnlineCard.tsx` (port `drawProvidersNetwork`) and `src/components/kpi/TopModelCard.tsx` (port `drawCurrentModelAnimation` hexagon+pulse, value = top model label) follow the same pattern — port the modelrelay draw function into a `useEffect` keyed on the counts, using the `net-*` / `current-*` classes above. (Reference: `C:\projects\modelrelay\public\index.html` `drawProvidersNetwork` ~line 2607 and `drawCurrentModelAnimation` ~line 2675.)

- [ ] **Step 4: Run test — expect PASS** (`npm test -- kpi`)

- [ ] **Step 5: Build to verify animations compile**

Run: `npm run build`
Expected: success.

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/components/kpi/
git commit -m "feat: animated KPI cards ported from modelrelay"
```

---

## Task 13: Dashboard shell + retrofit existing tabs

**Files:**
- Modify: `dashboard/frontend/src/App.tsx`
- Modify: `dashboard/frontend/src/tabs/LiveTelemetry.tsx`, `AccountStatus.tsx`, `RequestLogs.tsx`
- Test: `dashboard/frontend/src/tabs/AccountStatus.test.tsx`

**Interfaces:**
- Consumes: everything above.
- Produces: `App` renders, top to bottom: `UnreachableBanner`, `StatusBar`, header, KPI card row (above the tabs), `ConfigHealthBanner`, tab nav, active tab, and the `DiagnosticsConsole` drawer. Each retrofitted tab uses `usePolling` (no `.catch(() => {})`), shows last-good data with an error chip on failure, and a "refreshing…" affordance while `inFlight`.

- [ ] **Step 1: Write a failing retrofit test (AccountStatus no longer swallows errors)**

```tsx
// src/tabs/AccountStatus.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AccountStatus } from './AccountStatus'
import * as api from '../api'
import { FetchError } from '../api'
import { clearDiagnostics, getDiagnostics } from '../lib/diagnostics'

beforeEach(() => { vi.restoreAllMocks(); clearDiagnostics() })

describe('AccountStatus', () => {
  it('records a diagnostic instead of swallowing fetch errors', async () => {
    vi.spyOn(api, 'fetchStatus').mockRejectedValue(new FetchError('HTTP 500', 500, '/api/status'))
    render(<AccountStatus />)
    await waitFor(() => expect(getDiagnostics().length).toBeGreaterThan(0))
    expect(getDiagnostics()[0].status).toBe(500)
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- AccountStatus`) — current code uses `.catch(() => {})`, so no diagnostic is recorded.

- [ ] **Step 3: Retrofit `AccountStatus.tsx` to use `usePolling`**

```tsx
import { fetchStatus } from '../api'
import { usePolling } from '@/hooks/usePolling'

export function AccountStatus() {
  const { data: status, status: pollStatus, error } = usePolling<any>(fetchStatus, { intervalMs: 5000, endpoint: '/api/status' })
  const providers = status?.providers ?? {}
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="font-semibold">Provider Accounts</h2>
        {pollStatus === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {pollStatus === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale</span>}
      </div>
      {Object.entries(providers).map(([name, info]: any) => (
        <div key={name} className="border border-border rounded-xl p-4">
          <div className="flex justify-between items-center">
            <span className="font-medium">{name}</span>
            <span className="text-sm text-muted-foreground">${info.daily_cost_usd?.toFixed(4) ?? '0.0000'} today</span>
          </div>
          {info.budget_usd && <div className="mt-2 text-xs text-muted-foreground">Budget: ${info.budget_usd}/day</div>}
        </div>
      ))}
      {Object.keys(providers).length === 0 && <p className="text-muted-foreground text-sm">No provider data yet.</p>}
    </div>
  )
}
```

Apply the same pattern to `LiveTelemetry.tsx` (replace its manual `useEffect`/`setInterval` with `usePolling<any>(fetchStatus, { intervalMs: 2000, endpoint: '/api/status' })`, keep the search/pause UI, add an error chip) and `RequestLogs.tsx` (`usePolling(() => fetchLogs(100), { intervalMs: 3000, endpoint: '/api/logs' })`).

- [ ] **Step 4: Rebuild `App.tsx` as the dashboard shell**

```tsx
import { useState } from 'react'
import { LiveTelemetry } from './tabs/LiveTelemetry'
import { Chat } from './tabs/Chat'
import { RequestLogs } from './tabs/RequestLogs'
import { AccountStatus } from './tabs/AccountStatus'
import { Settings } from './tabs/Settings'
import { Setup } from './tabs/Setup'
import { StatusBar } from './components/StatusBar'
import { UnreachableBanner } from './components/UnreachableBanner'
import { DiagnosticsConsole } from './components/DiagnosticsConsole'
import { ConfigHealthBanner } from './components/ConfigHealthBanner'
import { ModelsOnlineCard } from './components/kpi/ModelsOnlineCard'
import { ProvidersOnlineCard } from './components/kpi/ProvidersOnlineCard'
import { TopModelCard } from './components/kpi/TopModelCard'
import { usePolling } from '@/hooks/usePolling'
import { fetchHealthCurrent, fetchStats } from './api'
import { useSyncExternalStore } from 'react'
import { getDiagnostics, subscribeDiagnostics } from './lib/diagnostics'

const TABS = [
  { id: 'telemetry', label: 'Live Telemetry', component: LiveTelemetry },
  { id: 'chat', label: 'Chat', component: Chat },
  { id: 'logs', label: 'Request Logs', component: RequestLogs },
  { id: 'accounts', label: 'Account Status', component: AccountStatus },
  { id: 'settings', label: 'Settings', component: Settings },
  { id: 'setup', label: 'Setup', component: Setup },
]

export default function App() {
  const [activeTab, setActiveTab] = useState(window.location.hash === '#setup' ? 'setup' : 'telemetry')
  const [diagOpen, setDiagOpen] = useState(false)
  const Tab = TABS.find(t => t.id === activeTab)?.component ?? LiveTelemetry
  const health = usePolling<any>(fetchHealthCurrent, { intervalMs: 3000, endpoint: '/api/health/current' })
  const stats = usePolling<any>(fetchStats, { intervalMs: 5000, endpoint: '/api/stats' })
  const diagnostics = useSyncExternalStore(subscribeDiagnostics, getDiagnostics)

  const models = Object.values(health.data?.models ?? {}) as any[]
  const modelsOnline = models.filter(m => m.status === 'up').length
  const providers = Object.values(health.data?.providers ?? {}) as any[]
  const providersOnline = providers.filter(p => (p.models_up ?? 0) > 0).length
  const topModel = stats.data?.distribution?.top_models?.[0]?.model ?? 'None'

  return (
    <div className="min-h-screen bg-background text-foreground">
      <UnreachableBanner error={health.unreachable ? health.error : null} onRetry={health.refresh} />
      <StatusBar status={health} version="0.1.0" diagnosticsCount={diagnostics.length} onOpenDiagnostics={() => setDiagOpen(true)} />
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="mb-2">
          <p className="text-xs font-semibold uppercase tracking-widest text-brand">Router Control Center</p>
          <h1 className="text-2xl font-bold">flexrouter</h1>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 my-6">
          <ModelsOnlineCard total={models.length} online={modelsOnline} />
          <ProvidersOnlineCard total={providers.length} online={providersOnline} />
          <TopModelCard label={topModel} active={topModel !== 'None'} />
        </div>
        <ConfigHealthBanner />
        <div className="flex gap-1 border-b border-border mb-6">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setActiveTab(t.id)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === t.id ? 'border-brand text-brand' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
              {t.label}
            </button>
          ))}
        </div>
        <Tab />
      </div>
      <DiagnosticsConsole open={diagOpen} onClose={() => setDiagOpen(false)} />
    </div>
  )
}
```

- [ ] **Step 5: Run the retrofit test + full suite**

Run: `npm test`
Expected: PASS (all, including AccountStatus diagnostic test).

- [ ] **Step 6: Run-to-verify the whole dashboard**

Start the backend (`python -m flexrouter.dashboard.server` or the project's run command) and `npm run dev`. Confirm: status bar clock ticks; KPI animations render; killing the backend flips the bar to red "BACKEND UNREACHABLE" with the raw error and a working Retry; the diagnostics drawer lists the errors; no blank/frozen panels.

- [ ] **Step 7: Commit**

```bash
git add dashboard/frontend/src/App.tsx dashboard/frontend/src/tabs/
git commit -m "feat: dashboard shell + retrofit tabs through usePolling (no hidden errors)"
```

---

## Self-Review Notes

- **Spec coverage:** usePolling with loading/stale/error + retained last data (Task 5); diagnostics console / nothing swallowed (Tasks 4, 9, 13); status bar with ticking clock = liveness (Task 8); watchdog → BACKEND UNREACHABLE + retry (Tasks 5, 10); AbortController timeouts (Task 3); config-health banner (Task 11); animated KPI cards above the tab bar (Tasks 12, 13); dashboard aesthetic via shadcn tokens + tabular-nums (Tasks 7, 13). Stats/Uptime *pages* are Plan 3.
- **Type consistency:** `PollState<T>` shape is identical across StatusBar, App, ConfigHealthBanner. `FetchError { status, url }` is the single error type from Task 3 through every consumer. KPI cards consume `/api/health/current` (Task 1) `models`/`providers` shapes, which match Plan 1's `health_snapshot()`.
- **Backend touch:** Task 1 is the only backend change — a thin read-only endpoint, justified because the KPI cards need current state that `/api/status` does not carry.
```
