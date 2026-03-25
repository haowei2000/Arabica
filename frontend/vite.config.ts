import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, path.resolve(__dirname, '..'), '')

  // Get backend host and port from env, with fallbacks
  const backendHost = env.VITE_BACKEND_HOST || env.AIWEN_APP_HOST || '127.0.0.1'
  const backendPort = env.VITE_BACKEND_PORT || env.AIWEN_APP_PORT || '8000'
  const defaultBackendUrl = `http://${backendHost}:${backendPort}`

  // API base URL for frontend code (usually '/api' for relative URLs)
  const apiUrl = env.VITE_API_BASE_URL || '/api'

  // Proxy target: if API base URL is relative, use default backend URL
  const proxyTarget = apiUrl.startsWith('http')
    ? apiUrl.replace(/\/api\/?$/, '')
    : defaultBackendUrl

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 3000,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          // Ensure SSE (Server-Sent Events) streams are not buffered.
          // Without this, http-proxy may hold chunks until the connection
          // closes, causing the frontend to receive nothing until the
          // run finishes.
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes, req, res) => {
              if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
                // Disable any proxy-level buffering / compression
                proxyRes.headers['cache-control'] = 'no-cache'
                proxyRes.headers['x-accel-buffering'] = 'no'
                delete proxyRes.headers['content-encoding']
                delete proxyRes.headers['content-length']
              }
            })
          },
        },
      },
    },
  }
})
