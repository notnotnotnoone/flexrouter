import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ConfigHealthBanner } from './ConfigHealthBanner'
import * as api from '../api'

beforeEach(() => vi.restoreAllMocks())

describe('ConfigHealthBanner', () => {
  it('renders errors and warnings', async () => {
    vi.spyOn(api, 'fetchConfigValidation').mockResolvedValue({
      errors: ["tiers.default[0].provider: 'nope' not defined in providers"],
      warnings: ["tiers.default[8]: 'gpt-oss-safeguard-20b' matches a non-chat name pattern"],
    })
    render(<ConfigHealthBanner />)
    await waitFor(() => expect(screen.getByText(/not defined in providers/)).toBeInTheDocument())
    expect(screen.getByText(/non-chat name pattern/)).toBeInTheDocument()
  })

  it('renders nothing when clean', async () => {
    vi.spyOn(api, 'fetchConfigValidation').mockResolvedValue({ errors: [], warnings: [] })
    const { container } = render(<ConfigHealthBanner />)
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })
})
