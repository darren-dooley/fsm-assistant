import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Point at the backend; FSM_PORT must match the backend's own FSM_PORT.
const apiPort = process.env.FSM_PORT ?? '8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': `http://127.0.0.1:${apiPort}`,
    },
  },
})
