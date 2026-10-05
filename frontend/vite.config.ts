import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Бэкенд — FastAPI на :8000 (docker compose db + uvicorn). Прокси убирает CORS в разработке,
// а refresh-cookie `vz_refresh` (путь /api/auth) живёт на origin фронтенда.
const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true, // CORS на бэкенде разрешает только http://localhost:5173
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
    },
  },
})
