import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AccountStatus } from './AccountStatus'
import * as api from '../api'
import { FetchError } from '../api'
import { clearDiagnostics, getDiagnostics } from '../lib/diagnostics'

beforeEach(() => { vi.restoreAllMocks(); clearDiagnostics() })

describe('AccountStatus', () => {
  it('records a diagnostic instead of swallowing fetch errors', async () => {
    vi.spyOn(api, 'fetchStatus').mockRejectedValue(new FetchError('HTTP 500', 500, '/api/status'))
    render(<AccountStatus />)
    await waitFor(() => expect(getDiagnostics().length).toBeGreaterThan(0))
    expect(getDiagnostics()[0].status).toBe(500)
  })
})
