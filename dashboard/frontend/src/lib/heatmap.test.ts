import { describe, it, expect } from 'vitest'
import { colorFor } from './heatmap'

describe('colorFor', () => {
  it('is transparent at zero', () => expect(colorFor(0, 10)).toMatch(/0\.00\)/))
  it('is full intensity at max', () => expect(colorFor(10, 10)).toContain('1.00)'))
  it('handles max=0 without NaN', () => expect(colorFor(0, 0)).toBeTruthy())
})
