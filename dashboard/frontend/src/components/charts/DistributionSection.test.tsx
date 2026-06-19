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
