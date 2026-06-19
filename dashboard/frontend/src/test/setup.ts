import '@testing-library/jest-dom/vitest'

// Recharts ResponsiveContainer requires ResizeObserver which jsdom doesn't have.
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
