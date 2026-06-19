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
