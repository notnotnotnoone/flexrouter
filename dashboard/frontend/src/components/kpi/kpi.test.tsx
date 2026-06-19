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
