import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'
import { resolve } from 'path'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, '../../flexrouter/dashboard/static'),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:7352',
    },
  },
})
