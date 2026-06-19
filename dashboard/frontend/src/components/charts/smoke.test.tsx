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
