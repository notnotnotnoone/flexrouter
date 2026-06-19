import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { StatsTab } from './Stats'
import * as api from '@/api'

const payload: any = {
  totals: { requests: 100, this_hour: 10, peak_rpm: 5 },
  hourly: [{ hour: '2026-06-19T12:00:00Z', requests: 10 }],
  latency: {
    per_model: [{ model: 'groq/llama', p50: 200, p95: 400, count: 3 }],
    histogram: [{ bucket_ms: '0-100', count: 2 }],
  },
  distribution: {
    by_provider: [{ provider: 'groq', requests: 50 }],
    top_models: [{ model: 'groq/llama', requests: 50 }],
    diversity: 0.8,
  },
  errors: {
    rate_by_hour: [{ hour: '2026-06-19T12:00:00Z', total: 10, errors: 1, rate: 0.1 }],
    by_type: [{ status: '429', count: 1 }],
    per_model: [{ model: 'groq/llama', requests: 10, errors: 1, rate: 0.1 }],
  },
  tiers: { by_tier: [{ tier: 'default', requests: 100 }] },
}

describe('StatsTab', () => {
  it('renders sections from polled data', async () => {
    vi.spyOn(api, 'fetchStats').mockResolvedValue(payload)
    render(<StatsTab />)
    await waitFor(() => expect(screen.getByText(/peak rpm/i)).toBeInTheDocument())
    expect(screen.getAllByText(/tier utilization/i).length).toBeGreaterThan(0)
  })

  it('shows loading state initially', () => {
    vi.spyOn(api, 'fetchStats').mockResolvedValue(payload)
    render(<StatsTab />)
    expect(screen.getByText(/loading stats/i)).toBeInTheDocument()
  })
})
