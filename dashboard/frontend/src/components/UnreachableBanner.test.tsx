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
