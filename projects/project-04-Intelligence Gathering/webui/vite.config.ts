import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode, command }) => {
  const environment = command === 'serve' ? loadEnv(mode, __dirname, 'DEV_') : {}
  const target = new URL(environment.DEV_API_ORIGIN || 'http://localhost:8080')
  if (target.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(target.hostname) ||
      target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('DEV_API_ORIGIN must be a local HTTP origin')
  }
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      outDir: '../api/webui',
      emptyOutDir: true,
    },
    server: {
      port: 5173,
      proxy: {
        '/workbench': target.origin,
        '/api': {
          target: target.origin,
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
