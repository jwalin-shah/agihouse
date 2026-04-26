import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    host: true,
    port: 5173,
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/events': { target: 'http://localhost:9876', changeOrigin: true, ws: false },
      '/push': { target: 'http://localhost:9876', changeOrigin: true },
      '/health': { target: 'http://localhost:9876', changeOrigin: true },
      '/recall': { target: 'http://localhost:9876', changeOrigin: true },
      '/tick': { target: 'http://localhost:9876', changeOrigin: true },
      '/audio': { target: 'http://localhost:9876', changeOrigin: true },
    },
  },
  build: { target: 'esnext' },
})
