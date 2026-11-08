/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// In development the Vite server proxies /api to the FastAPI app and adds the
// API key itself, so the key never reaches the browser bundle. In Docker the
// same job is done by nginx (see web/nginx.conf.template).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VOC_API_URL ?? 'http://localhost:8000'
  const apiKey = env.API_KEY ?? 'change-me'
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { '@': path.resolve(import.meta.dirname, "./src") },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
    server: {
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
          headers: { 'X-API-Key': apiKey },
        },
      },
    },
  }
})
