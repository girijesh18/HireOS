import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Works in Docker (backend service) and locally (localhost)
const API_TARGET = process.env.VITE_API_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: 'all',
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path,
      }
    }
  }
})
