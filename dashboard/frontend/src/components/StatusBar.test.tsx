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
