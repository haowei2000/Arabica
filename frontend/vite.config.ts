import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiUrl = env.VITE_API_BASE_URL || 'http://localhost:8000/api'
  // Extract base URL for proxy (remove /api suffix if present)
  const proxyTarget = apiUrl.replace(/\/api\/?$/, '') || 'http://localhost:8000'

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
