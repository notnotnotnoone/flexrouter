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
