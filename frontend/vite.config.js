import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// El frontend llama a /api/... y Vite reenvía esas peticiones al backend de
// FastAPI. Así el navegador ve un solo origen y el backend no necesita CORS.
const BACKEND = process.env.FOCUSFLOW_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        rewrite: (ruta) => ruta.replace(/^\/api/, ''),
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        rewrite: (ruta) => ruta.replace(/^\/api/, ''),
      },
    },
  },
})
