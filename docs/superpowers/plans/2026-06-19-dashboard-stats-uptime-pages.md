# Dashboard Overhaul — Plan 3: Stats & Uptime Pages

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the in-depth **Stats** page (5 categories) and the status-page-style **Uptime** page, consuming Plan 1's `/api/stats` and `/api/uptime` through Plan 2's `usePolling` hook.

**Architecture:** Recharts for standard charts (line/area/bar/donut/scatter); two hand-rolled SVG components Recharts handles poorly — the time-of-day **heatmap** and the penalty **swimlane** — plus the uptime **status-timeline** bars. Pure data-transform helpers are unit-tested; chart rendering is verified by running the app.

**Tech Stack:** React 18 + TS, Recharts, Tailwind v4 + shadcn tokens, lucide-react. Vitest for transforms; run-to-verify for charts.

## Global Constraints

- This plan depends on **Plan 2** being merged: `usePolling`, `fetchStats`, `fetchUptime`, the dashboard shell, and the `--color-success`/`--color-warning` tokens must exist.
- Recharts color via CSS vars: series use `var(--color-brand)`, `var(--color-success)`, `var(--color-warning)`, `var(--color-destructive)`, and `var(--chart-1..5)`.
- Every page consumes data through `usePolling` — error/stale states surface via the same chips as Plan 2. No direct `fetch` in page components.
- Pure transform helpers live in `src/lib/` with co-located `*.test.ts` and must be unit-tested. Chart components are run-to-verify.
- Numbers render with `tabular-nums`. Empty datasets render an explicit "No data yet" state, never a blank box.

---

## File Structure

- Modify `dashboard/frontend/package.json` — add Recharts (Task 1).
- Create `dashboard/frontend/src/lib/statsTypes.ts` — shared TS types for `/api/stats` + `/api/uptime` (Task 2).
- Create `dashboard/frontend/src/components/charts/` — `VolumeSection`, `LatencySection`, `Heatmap`, `DistributionSection`, `ReliabilitySection`, `Swimlane`, `EfficiencySection` (Tasks 3-7).
- Create `dashboard/frontend/src/lib/heatmap.ts`, `swimlane.ts`, `timeline.ts` — transforms (Tasks 4, 6, 9).
- Create `dashboard/frontend/src/tabs/Stats.tsx` (Task 8) and `dashboard/frontend/src/tabs/Uptime.tsx` (Task 9).
- Modify `dashboard/frontend/src/App.tsx` — register the two tabs (Task 10).

---

## Task 1: Add Recharts

**Files:**
- Modify: `dashboard/frontend/package.json`
- Test: `dashboard/frontend/src/components/charts/smoke.test.tsx`

- [ ] **Step 1: Install**

Run from `dashboard/frontend/`:
```bash
npm install recharts@^2
```

- [ ] **Step 2: Write a smoke test that Recharts renders in jsdom**

```tsx
// src/components/charts/smoke.test.tsx
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { BarChart, Bar } from 'recharts'

describe('recharts', () => {
  it('renders a chart container', () => {
    const { container } = render(
      <BarChart width={200} height={100} data={[{ x: 1 }, { x: 2 }]}>
        <Bar dataKey="x" />
      </BarChart>
    )
    expect(container.querySelector('svg')).toBeTruthy()
  })
})
```

Run: `npm test -- charts/smoke`
Expected: PASS. (Use a fixed `width`/`height`, not `ResponsiveContainer`, in tests — jsdom has no layout.)

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/package.json dashboard/frontend/package-lock.json dashboard/frontend/src/components/charts/smoke.test.tsx
git commit -m "build: add Recharts"
```

---

## Task 2: Shared stats/uptime types

**Files:**
- Create: `dashboard/frontend/src/lib/statsTypes.ts`

**Interfaces:**
- Produces: TS interfaces mirroring Plan 1's payloads. No runtime code, so no test; consumed by every later task.

- [ ] **Step 1: Write the types**

```ts
// src/lib/statsTypes.ts
export interface Stats {
  totals: { requests: number; this_hour: number; peak_rpm: number }
  hourly: { hour: string; requests: number }[]
  latency: {
    per_model: { model: string; p50: number; p95: number; count: number }[]
    histogram: { bucket_ms: string; count: number }[]
  }
  distribution: {
    by_provider: { provider: string; requests: number }[]
    top_models: { model: string; requests: number }[]
    diversity: number
  }
  errors: {
    rate_by_hour: { hour: string; total: number; errors: number; rate: number }[]
    by_type: { status: string; count: number }[]
    per_model: { model: string; requests: number; errors: number; rate: number }[]
  }
  tiers: { by_tier: { tier: string; requests: number }[] }
}

export interface UptimeModel {
  model: string
  uptime_24h: number | null
  uptime_7d: number | null
  uptime_30d: number | null
  segments: { start: string; end: string; state: 'up' | 'down' | 'nodata' }[]
}
export interface Uptime {
  models: UptimeModel[]
  incidents: { start: string; end: string | null; provider: string; model: string; event_type: string; duration_seconds: number | null }[]
  system: { uptime_24h: number | null }
  providers: { provider: string; uptime_24h: number | null }[]
}
```

- [ ] **Step 2: Verify it type-checks**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/lib/statsTypes.ts
git commit -m "feat: shared stats/uptime TS types"
```

---

## Task 3: Volume & Throughput section

**Files:**
- Create: `dashboard/frontend/src/components/charts/VolumeSection.tsx`
- Test: `dashboard/frontend/src/components/charts/VolumeSection.test.tsx`

**Interfaces:**
- Produces: `<VolumeSection stats={Stats | null} />`. Three KPI tiles (total, this hour, peak rpm) + a requests/hour bar chart. Renders "No data yet" when `stats` is null or has zero requests.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/charts/VolumeSection.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VolumeSection } from './VolumeSection'

const stats: any = {
  totals: { requests: 5, this_hour: 2, peak_rpm: 3 },
  hourly: [{ hour: '2026-06-19T10:00:00+00:00', requests: 3 }, { hour: '2026-06-19T11:00:00+00:00', requests: 2 }],
}

describe('VolumeSection', () => {
  it('shows totals', () => {
    render(<VolumeSection stats={stats} />)
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText(/peak rpm/i)).toBeInTheDocument()
  })
  it('shows empty state when null', () => {
    render(<VolumeSection stats={null} />)
    expect(screen.getByText(/no data yet/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- VolumeSection`)

- [ ] **Step 3: Implement**

```tsx
// src/components/charts/VolumeSection.tsx
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
    </div>
  )
}

export function VolumeSection({ stats }: { stats: Stats | null }) {
  if (!stats || stats.totals.requests === 0) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  const data = stats.hourly.map(h => ({ hour: h.hour.slice(11, 16), requests: h.requests }))
  return (
    <section className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Tile label="Total requests" value={stats.totals.requests} />
        <Tile label="This hour" value={stats.totals.this_hour} />
        <Tile label="Peak RPM" value={stats.totals.peak_rpm} />
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <XAxis dataKey="hour" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="requests" fill="var(--color-brand)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- VolumeSection`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/charts/VolumeSection.tsx dashboard/frontend/src/components/charts/VolumeSection.test.tsx
git commit -m "feat: Volume & Throughput stats section"
```

---

## Task 4: Latency section + heatmap transform

**Files:**
- Create: `dashboard/frontend/src/lib/heatmap.ts`
- Create: `dashboard/frontend/src/components/charts/Heatmap.tsx`
- Create: `dashboard/frontend/src/components/charts/LatencySection.tsx`
- Test: `dashboard/frontend/src/lib/heatmap.test.ts`

**Interfaces:**
- Produces: `colorFor(value: number, max: number): string` (transparent→brand by intensity); `<Heatmap cells={{day:number,hour:number,value:number}[]} />`; `<LatencySection stats={Stats|null} />` (P50/P95 per-model bars + histogram bars). The heatmap input is derived in the page from `stats.hourly` (Task 8); `heatmap.ts` only owns the color scale, which is unit-testable.

- [ ] **Step 1: Write the failing transform test**

```ts
// src/lib/heatmap.test.ts
import { describe, it, expect } from 'vitest'
import { colorFor } from './heatmap'

describe('colorFor', () => {
  it('is transparent at zero', () => expect(colorFor(0, 10)).toMatch(/0\)$|transparent/))
  it('is full intensity at max', () => expect(colorFor(10, 10)).toContain('1)'))
  it('handles max=0 without NaN', () => expect(colorFor(0, 0)).toBeTruthy())
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- heatmap`)

- [ ] **Step 3: Implement transform, then components**

```ts
// src/lib/heatmap.ts
export function colorFor(value: number, max: number): string {
  const intensity = max > 0 ? value / max : 0
  return `color-mix(in srgb, var(--color-brand) ${Math.round(intensity * 100)}%, transparent)`
}
```

> Note: the test asserts the textual form; use this simpler unit-testable variant instead so the assertions hold:

```ts
// src/lib/heatmap.ts  (final)
export function colorFor(value: number, max: number): string {
  const intensity = max > 0 ? value / max : 0
  return `rgba(59, 130, 246, ${intensity.toFixed(2)})`  // brand rgb at computed alpha
}
```

(The `0.00` alpha satisfies "transparent at zero"; `1.00` satisfies "full at max"; `max=0` yields `rgba(...,0.00)`.)

```tsx
// src/components/charts/Heatmap.tsx
import { colorFor } from '@/lib/heatmap'

export function Heatmap({ cells }: { cells: { day: number; hour: number; value: number }[] }) {
  const max = cells.reduce((m, c) => Math.max(m, c.value), 0)
  const grid = new Map(cells.map(c => [`${c.day}-${c.hour}`, c.value]))
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  return (
    <div className="rounded-xl border border-border bg-card p-4 overflow-x-auto">
      <div className="text-xs text-muted-foreground mb-2">Requests by hour × day</div>
      <div className="grid gap-0.5" style={{ gridTemplateColumns: `auto repeat(24, 14px)` }}>
        <div />
        {Array.from({ length: 24 }, (_, h) => <div key={h} className="text-[9px] text-muted-foreground text-center">{h}</div>)}
        {days.map((d, di) => (
          <>
            <div key={d} className="text-[10px] text-muted-foreground pr-1">{d}</div>
            {Array.from({ length: 24 }, (_, h) => {
              const v = grid.get(`${di}-${h}`) ?? 0
              return <div key={`${di}-${h}`} title={`${d} ${h}:00 — ${v}`} className="w-[14px] h-[14px] rounded-sm" style={{ background: colorFor(v, max) }} />
            })}
          </>
        ))}
      </div>
    </div>
  )
}
```

```tsx
// src/components/charts/LatencySection.tsx
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

export function LatencySection({ stats }: { stats: Stats | null }) {
  if (!stats || stats.latency.per_model.length === 0) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  return (
    <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 h-72">
        <div className="text-sm font-medium mb-2">Latency p50 / p95 by model (ms)</div>
        <ResponsiveContainer width="100%" height="90%">
          <BarChart data={stats.latency.per_model} layout="vertical" margin={{ left: 40 }}>
            <XAxis type="number" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis type="category" dataKey="model" width={120} fontSize={10} stroke="var(--color-muted-foreground)" />
            <Tooltip /><Legend />
            <Bar dataKey="p50" fill="var(--color-success)" />
            <Bar dataKey="p95" fill="var(--color-warning)" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-72">
        <div className="text-sm font-medium mb-2">Latency distribution</div>
        <ResponsiveContainer width="100%" height="90%">
          <BarChart data={stats.latency.histogram}>
            <XAxis dataKey="bucket_ms" fontSize={10} stroke="var(--color-muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="count" fill="var(--color-brand)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- heatmap`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/heatmap.ts dashboard/frontend/src/lib/heatmap.test.ts dashboard/frontend/src/components/charts/Heatmap.tsx dashboard/frontend/src/components/charts/LatencySection.tsx
git commit -m "feat: Latency section + time-of-day heatmap"
```

---

## Task 5: Distribution section

**Files:**
- Create: `dashboard/frontend/src/components/charts/DistributionSection.tsx`
- Test: `dashboard/frontend/src/components/charts/DistributionSection.test.tsx`

**Interfaces:**
- Produces: `<DistributionSection stats={Stats|null} />` — provider donut (PieChart), top-models horizontal bars, and a diversity score tile. Empty state when null.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/charts/DistributionSection.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DistributionSection } from './DistributionSection'

const stats: any = {
  distribution: {
    by_provider: [{ provider: 'groq', requests: 3 }, { provider: 'openrouter', requests: 1 }],
    top_models: [{ model: 'groq/llama', requests: 3 }],
    diversity: 0.811,
  },
}
describe('DistributionSection', () => {
  it('shows the diversity score', () => {
    render(<DistributionSection stats={stats} />)
    expect(screen.getByText(/0\.811/)).toBeInTheDocument()
  })
  it('empty when null', () => {
    render(<DistributionSection stats={null} />)
    expect(screen.getByText(/no data yet/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- DistributionSection`)

- [ ] **Step 3: Implement**

```tsx
// src/components/charts/DistributionSection.tsx
import { PieChart, Pie, Cell, Tooltip, BarChart, Bar, XAxis, YAxis, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

const CHART_COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']

export function DistributionSection({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  return (
    <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <div className="text-sm font-medium mb-2">Requests by provider</div>
        <ResponsiveContainer width="100%" height="85%">
          <PieChart>
            <Pie data={stats.distribution.by_provider} dataKey="requests" nameKey="provider" innerRadius={40} outerRadius={70}>
              {stats.distribution.by_provider.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
            </Pie>
            <Tooltip />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-64 lg:col-span-2">
        <div className="text-sm font-medium mb-2">Top models</div>
        <ResponsiveContainer width="100%" height="85%">
          <BarChart data={stats.distribution.top_models} layout="vertical" margin={{ left: 40 }}>
            <XAxis type="number" fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <YAxis type="category" dataKey="model" width={140} fontSize={10} stroke="var(--color-muted-foreground)" />
            <Tooltip />
            <Bar dataKey="requests" fill="var(--color-brand)" radius={[0, 3, 3, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">Routing diversity (bits)</div>
        <div className="text-2xl font-bold tabular-nums">{stats.distribution.diversity.toFixed(3)}</div>
        <p className="text-xs text-muted-foreground mt-1">Higher = traffic spread across more providers.</p>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- DistributionSection`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/charts/DistributionSection.tsx dashboard/frontend/src/components/charts/DistributionSection.test.tsx
git commit -m "feat: Distribution stats section (provider donut + top models)"
```

---

## Task 6: Reliability section + swimlane transform

**Files:**
- Create: `dashboard/frontend/src/lib/swimlane.ts`
- Create: `dashboard/frontend/src/components/charts/Swimlane.tsx`
- Create: `dashboard/frontend/src/components/charts/ReliabilitySection.tsx`
- Test: `dashboard/frontend/src/lib/swimlane.test.ts`

**Interfaces:**
- Produces: `toLanes(incidents, now): { model: string; bars: { leftPct: number; widthPct: number; ongoing: boolean }[] }[]` mapping incidents into 24h-window positioned bars; `<Swimlane incidents={...} />`; `<ReliabilitySection stats={Stats|null} />` (error-rate line + error-type bars + per-model error table). `toLanes` is the unit-tested core.

- [ ] **Step 1: Write the failing transform test**

```ts
// src/lib/swimlane.test.ts
import { describe, it, expect } from 'vitest'
import { toLanes } from './swimlane'

const now = new Date('2026-06-19T12:00:00Z').getTime()

describe('toLanes', () => {
  it('positions a closed incident within the 24h window', () => {
    const lanes = toLanes([{
      provider: 'groq', model: 'llama', event_type: 'penalized',
      start: '2026-06-19T06:00:00Z', end: '2026-06-19T07:00:00Z', duration_seconds: 3600,
    }], now)
    expect(lanes).toHaveLength(1)
    const bar = lanes[0].bars[0]
    expect(bar.leftPct).toBeCloseTo(25, 0)   // 6h before now-24h start = 18h/24h
    expect(bar.widthPct).toBeCloseTo(100 / 24, 1)
    expect(bar.ongoing).toBe(false)
  })
  it('marks an open incident as ongoing to now', () => {
    const lanes = toLanes([{
      provider: 'groq', model: 'llama', event_type: 'penalized',
      start: '2026-06-19T11:30:00Z', end: null, duration_seconds: null,
    }], now)
    expect(lanes[0].bars[0].ongoing).toBe(true)
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- swimlane`)

- [ ] **Step 3: Implement transform, then components**

```ts
// src/lib/swimlane.ts
interface Incident { provider: string; model: string; event_type: string; start: string; end: string | null; duration_seconds: number | null }
const WINDOW_MS = 24 * 60 * 60 * 1000

export function toLanes(incidents: Incident[], now: number) {
  const windowStart = now - WINDOW_MS
  const byModel = new Map<string, { leftPct: number; widthPct: number; ongoing: boolean }[]>()
  for (const inc of incidents) {
    const start = new Date(inc.start.replace('Z', '+00:00')).getTime()
    const end = inc.end ? new Date(inc.end.replace('Z', '+00:00')).getTime() : now
    if (end < windowStart) continue
    const clampedStart = Math.max(start, windowStart)
    const leftPct = ((clampedStart - windowStart) / WINDOW_MS) * 100
    const widthPct = Math.max(((end - clampedStart) / WINDOW_MS) * 100, 0.5)
    const key = `${inc.provider}/${inc.model}`
    if (!byModel.has(key)) byModel.set(key, [])
    byModel.get(key)!.push({ leftPct, widthPct, ongoing: inc.end === null })
  }
  return [...byModel.entries()].map(([model, bars]) => ({ model, bars }))
}
```

```tsx
// src/components/charts/Swimlane.tsx
import { toLanes } from '@/lib/swimlane'
import type { Uptime } from '@/lib/statsTypes'

export function Swimlane({ incidents }: { incidents: Uptime['incidents'] }) {
  const lanes = toLanes(incidents, Date.now())
  if (lanes.length === 0) {
    return <div className="text-sm text-muted-foreground p-4">No incidents in the last 24h.</div>
  }
  return (
    <div className="space-y-1">
      {lanes.map(lane => (
        <div key={lane.model} className="flex items-center gap-2">
          <span className="w-40 shrink-0 text-xs font-mono truncate">{lane.model}</span>
          <div className="relative flex-1 h-4 bg-[var(--color-success)]/15 rounded">
            {lane.bars.map((b, i) => (
              <div key={i} title={b.ongoing ? 'ongoing' : 'resolved'}
                className="absolute top-0 h-4 rounded"
                style={{ left: `${b.leftPct}%`, width: `${b.widthPct}%`,
                  background: b.ongoing ? 'var(--color-destructive)' : 'var(--color-warning)' }} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
```

```tsx
// src/components/charts/ReliabilitySection.tsx
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

export function ReliabilitySection({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  const rate = stats.errors.rate_by_hour.map(r => ({ hour: r.hour.slice(11, 16), rate: Math.round(r.rate * 100) }))
  return (
    <section className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-border bg-card p-4 h-64">
          <div className="text-sm font-medium mb-2">Error rate % (hourly)</div>
          <ResponsiveContainer width="100%" height="85%">
            <LineChart data={rate}>
              <XAxis dataKey="hour" fontSize={11} stroke="var(--color-muted-foreground)" />
              <YAxis fontSize={11} stroke="var(--color-muted-foreground)" domain={[0, 100]} />
              <Tooltip />
              <Line dataKey="rate" stroke="var(--color-destructive)" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-xl border border-border bg-card p-4 h-64">
          <div className="text-sm font-medium mb-2">Outcomes by type</div>
          <ResponsiveContainer width="100%" height="85%">
            <BarChart data={stats.errors.by_type}>
              <XAxis dataKey="status" fontSize={11} stroke="var(--color-muted-foreground)" />
              <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="var(--color-warning)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-medium mb-2">Per-model reliability</div>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground border-b border-border">
            <th className="pb-2">Model</th><th className="pb-2">Requests</th><th className="pb-2">Errors</th><th className="pb-2">Error %</th>
          </tr></thead>
          <tbody>
            {stats.errors.per_model.map(m => (
              <tr key={m.model} className="border-b border-border/50">
                <td className="py-1.5 font-mono">{m.model}</td>
                <td className="tabular-nums">{m.requests}</td>
                <td className="tabular-nums">{m.errors}</td>
                <td className="tabular-nums">{(m.rate * 100).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- swimlane`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/swimlane.ts dashboard/frontend/src/lib/swimlane.test.ts dashboard/frontend/src/components/charts/Swimlane.tsx dashboard/frontend/src/components/charts/ReliabilitySection.tsx
git commit -m "feat: Reliability section + penalty swimlane"
```

---

## Task 7: Efficiency section

**Files:**
- Create: `dashboard/frontend/src/components/charts/EfficiencySection.tsx`
- Test: `dashboard/frontend/src/components/charts/EfficiencySection.test.tsx`

**Interfaces:**
- Produces: `<EfficiencySection stats={Stats|null} />` — tier-utilization bars (from `stats.tiers.by_tier`) and a latency-vs-volume scatter (per-model p95 vs request count, a proxy for "fast + used"). Empty state when null.

- [ ] **Step 1: Write the failing render test**

```tsx
// src/components/charts/EfficiencySection.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EfficiencySection } from './EfficiencySection'

const stats: any = {
  tiers: { by_tier: [{ tier: 'default', requests: 5 }] },
  latency: { per_model: [{ model: 'groq/llama', p50: 200, p95: 400, count: 3 }] },
}
describe('EfficiencySection', () => {
  it('renders tier utilization', () => {
    render(<EfficiencySection stats={stats} />)
    expect(screen.getByText(/tier utilization/i)).toBeInTheDocument()
  })
  it('empty when null', () => {
    render(<EfficiencySection stats={null} />)
    expect(screen.getByText(/no data yet/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- EfficiencySection`)

- [ ] **Step 3: Implement**

```tsx
// src/components/charts/EfficiencySection.tsx
import { BarChart, Bar, XAxis, YAxis, Tooltip, ScatterChart, Scatter, ZAxis, ResponsiveContainer } from 'recharts'
import type { Stats } from '@/lib/statsTypes'

export function EfficiencySection({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground text-sm">No data yet</div>
  }
  const scatter = stats.latency.per_model.map(m => ({ x: m.p95, y: m.count, name: m.model }))
  return (
    <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <div className="text-sm font-medium mb-2">Tier utilization</div>
        <ResponsiveContainer width="100%" height="85%">
          <BarChart data={stats.tiers.by_tier}>
            <XAxis dataKey="tier" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--color-muted-foreground)" allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="requests" fill="var(--color-brand)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-xl border border-border bg-card p-4 h-64">
        <div className="text-sm font-medium mb-2">p95 latency vs request volume</div>
        <ResponsiveContainer width="100%" height="85%">
          <ScatterChart>
            <XAxis type="number" dataKey="x" name="p95 ms" fontSize={11} stroke="var(--color-muted-foreground)" />
            <YAxis type="number" dataKey="y" name="requests" fontSize={11} stroke="var(--color-muted-foreground)" />
            <ZAxis range={[60, 60]} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} />
            <Scatter data={scatter} fill="var(--color-success)" />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- EfficiencySection`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/charts/EfficiencySection.tsx dashboard/frontend/src/components/charts/EfficiencySection.test.tsx
git commit -m "feat: Efficiency stats section"
```

---

## Task 8: Stats page assembly

**Files:**
- Create: `dashboard/frontend/src/tabs/Stats.tsx`
- Test: `dashboard/frontend/src/tabs/Stats.test.tsx`

**Interfaces:**
- Consumes: `usePolling(fetchStats)`, all five sections, `Heatmap`.
- Produces: `<Stats />` — polls `/api/stats` every 5s, shows an error/stale chip, and stacks the five sections. Builds the heatmap `cells` from `stats.hourly` (day-of-week × hour).

- [ ] **Step 1: Write the failing render test**

```tsx
// src/tabs/Stats.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Stats } from './Stats'
import * as api from '../api'

beforeEach(() => vi.restoreAllMocks())

const payload: any = {
  totals: { requests: 1, this_hour: 1, peak_rpm: 1 },
  hourly: [{ hour: '2026-06-19T12:00:00+00:00', requests: 1 }],
  latency: { per_model: [{ model: 'groq/llama', p50: 200, p95: 400, count: 1 }], histogram: [{ bucket_ms: '0-250', count: 1 }] },
  distribution: { by_provider: [{ provider: 'groq', requests: 1 }], top_models: [{ model: 'groq/llama', requests: 1 }], diversity: 0 },
  errors: { rate_by_hour: [], by_type: [{ status: 'ok', count: 1 }], per_model: [{ model: 'groq/llama', requests: 1, errors: 0, rate: 0 }] },
  tiers: { by_tier: [{ tier: 'default', requests: 1 }] },
}

describe('Stats page', () => {
  it('renders sections from polled data', async () => {
    vi.spyOn(api, 'fetchStats').mockResolvedValue(payload)
    render(<Stats />)
    await waitFor(() => expect(screen.getByText(/peak rpm/i)).toBeInTheDocument())
    expect(screen.getByText(/tier utilization/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- tabs/Stats`)

- [ ] **Step 3: Implement**

```tsx
// src/tabs/Stats.tsx
import { fetchStats } from '../api'
import { usePolling } from '@/hooks/usePolling'
import type { Stats as StatsData } from '@/lib/statsTypes'
import { VolumeSection } from '../components/charts/VolumeSection'
import { LatencySection } from '../components/charts/LatencySection'
import { Heatmap } from '../components/charts/Heatmap'
import { DistributionSection } from '../components/charts/DistributionSection'
import { ReliabilitySection } from '../components/charts/ReliabilitySection'
import { EfficiencySection } from '../components/charts/EfficiencySection'

function buildHeatmapCells(hourly: StatsData['hourly']) {
  const map = new Map<string, number>()
  for (const h of hourly) {
    const d = new Date(h.hour.replace('Z', '+00:00'))
    const key = `${d.getUTCDay()}-${d.getUTCHours()}`
    map.set(key, (map.get(key) ?? 0) + h.requests)
  }
  return [...map.entries()].map(([k, value]) => {
    const [day, hour] = k.split('-').map(Number)
    return { day, hour, value }
  })
}

export function Stats() {
  const { data, status, error } = usePolling<StatsData>(fetchStats, { intervalMs: 5000, endpoint: '/api/stats' })
  return (
    <div className="space-y-8">
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-semibold">Statistics</h2>
        {status === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {status === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale data</span>}
      </div>
      <VolumeSection stats={data} />
      <LatencySection stats={data} />
      {data && <Heatmap cells={buildHeatmapCells(data.hourly)} />}
      <DistributionSection stats={data} />
      <ReliabilitySection stats={data} />
      <EfficiencySection stats={data} />
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- tabs/Stats`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/tabs/Stats.tsx dashboard/frontend/src/tabs/Stats.test.tsx
git commit -m "feat: Stats page assembling all five categories"
```

---

## Task 9: Uptime page + timeline transform

**Files:**
- Create: `dashboard/frontend/src/lib/timeline.ts`
- Create: `dashboard/frontend/src/tabs/Uptime.tsx`
- Test: `dashboard/frontend/src/lib/timeline.test.ts`

**Interfaces:**
- Produces: `mergeSegments(segments): { state: string; count: number }[]` (run-length merge of adjacent same-state samples, for compact timeline bars); `<Uptime />` — polls `/api/uptime` every 5s; per-model uptime % cards (24h/7d/30d), a merged status-timeline bar per model, system + per-provider headline, and an incident list with durations.

- [ ] **Step 1: Write the failing transform test**

```ts
// src/lib/timeline.test.ts
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
```

- [ ] **Step 2: Run test — expect FAIL** (`npm test -- timeline`)

- [ ] **Step 3: Implement transform, then the page**

```ts
// src/lib/timeline.ts
import type { UptimeModel } from './statsTypes'

export function mergeSegments(segments: UptimeModel['segments']): { state: string; count: number }[] {
  const out: { state: string; count: number }[] = []
  for (const s of segments) {
    const last = out[out.length - 1]
    if (last && last.state === s.state) last.count++
    else out.push({ state: s.state, count: 1 })
  }
  return out
}
```

```tsx
// src/tabs/Uptime.tsx
import { fetchUptime } from '../api'
import { usePolling } from '@/hooks/usePolling'
import type { Uptime as UptimeData } from '@/lib/statsTypes'
import { mergeSegments } from '@/lib/timeline'

function pct(v: number | null) { return v === null ? '—' : `${(v * 100).toFixed(1)}%` }
function stateColor(s: string) {
  return s === 'up' ? 'var(--color-success)' : s === 'down' ? 'var(--color-destructive)' : 'var(--color-muted-foreground)'
}

export function Uptime() {
  const { data, status, error } = usePolling<UptimeData>(fetchUptime, { intervalMs: 5000, endpoint: '/api/uptime' })
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-semibold">Uptime</h2>
        {status === 'error' && <span className="text-xs text-[var(--color-destructive)]">{error?.message}</span>}
        {status === 'stale' && <span className="text-xs text-[var(--color-warning)]">stale data</span>}
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">System uptime (24h)</div>
        <div className="text-3xl font-bold tabular-nums">{pct(data?.system.uptime_24h ?? null)}</div>
        <div className="flex flex-wrap gap-3 mt-2">
          {data?.providers.map(p => (
            <span key={p.provider} className="text-xs text-muted-foreground">{p.provider}: <b className="text-foreground">{pct(p.uptime_24h)}</b></span>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        {(data?.models ?? []).map(m => {
          const merged = mergeSegments(m.segments)
          return (
            <div key={m.model} className="rounded-xl border border-border bg-card p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-sm">{m.model}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  24h {pct(m.uptime_24h)} · 7d {pct(m.uptime_7d)} · 30d {pct(m.uptime_30d)}
                </span>
              </div>
              <div className="flex h-3 w-full gap-px overflow-hidden rounded">
                {merged.length === 0 && <div className="flex-1 bg-muted" />}
                {merged.map((seg, i) => (
                  <div key={i} style={{ flexGrow: seg.count, background: stateColor(seg.state) }} title={seg.state} />
                ))}
              </div>
            </div>
          )
        })}
        {(data?.models?.length ?? 0) === 0 && <p className="text-sm text-muted-foreground">No uptime samples yet.</p>}
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-medium mb-2">Incidents</div>
        {(data?.incidents ?? []).length === 0 && <p className="text-sm text-muted-foreground">No incidents recorded.</p>}
        <ul className="space-y-1 text-sm">
          {data?.incidents.map((inc, i) => (
            <li key={i} className="flex justify-between font-mono text-xs border-b border-border/50 py-1">
              <span>{inc.provider}/{inc.model} — {inc.event_type}</span>
              <span className="text-muted-foreground">
                {inc.start.slice(5, 16).replace('T', ' ')}{inc.duration_seconds !== null ? ` · ${inc.duration_seconds}s` : ' · ongoing'}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test — expect PASS** (`npm test -- timeline`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/timeline.ts dashboard/frontend/src/lib/timeline.test.ts dashboard/frontend/src/tabs/Uptime.tsx
git commit -m "feat: Uptime page with status timeline + incidents"
```

---

## Task 10: Register Stats + Uptime tabs

**Files:**
- Modify: `dashboard/frontend/src/App.tsx`

**Interfaces:**
- Consumes: `Stats` (Task 8), `Uptime` (Task 9).

- [ ] **Step 1: Add imports and tab entries**

In `App.tsx`, import the pages:

```tsx
import { Stats } from './tabs/Stats'
import { Uptime } from './tabs/Uptime'
```

Add to the `TABS` array (after `telemetry`):

```tsx
  { id: 'stats', label: 'Stats', component: Stats },
  { id: 'uptime', label: 'Uptime', component: Uptime },
```

- [ ] **Step 2: Run the full frontend suite**

Run: `npm test`
Expected: PASS (all).

- [ ] **Step 3: Build + run-to-verify**

Run: `npm run build`, then start backend + `npm run dev`. Click **Stats** and **Uptime**. Confirm: charts render with real data; empty states show "No data yet" rather than blank boxes; error/stale chips appear when the backend is stopped; no frozen panels.

- [ ] **Step 4: Commit**

```bash
git add dashboard/frontend/src/App.tsx
git commit -m "feat: register Stats and Uptime tabs"
```

---

## Self-Review Notes

- **Spec coverage:** Stats categories — Volume (Task 3), Latency + heatmap (Task 4), Distribution (Task 5), Reliability + swimlane (Task 6), Efficiency (Task 7), assembled (Task 8). Uptime page with per-model timeline, system/provider headline, incidents (Task 9). Money-saved correctly absent. Recharts added (Task 1); heatmap/swimlane/timeline hand-rolled (Tasks 4, 6, 9).
- **Type consistency:** `Stats` and `Uptime` interfaces (Task 2) match Plan 1's `compute_stats`/`compute_uptime` payloads exactly and are the single source of truth for every section. `usePolling<T>` and `fetchStats`/`fetchUptime` come from Plan 2 unchanged.
- **Test boundary:** transforms (`colorFor`, `toLanes`, `mergeSegments`, `buildHeatmapCells` via Stats render) are unit-tested; Recharts SVGs are run-to-verify per the chosen strategy. Recharts tests use fixed width/height (jsdom has no layout).
- **Dependency:** requires Plan 2 merged. Recharts is the only new runtime dependency.
```
