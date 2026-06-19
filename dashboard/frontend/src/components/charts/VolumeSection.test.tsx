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
