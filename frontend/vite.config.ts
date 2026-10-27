import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'

const API_TARGET = process.env.API_TARGET ?? 'http://127.0.0.1:8000'

// The backend accepts only same-origin browser requests (Origin must equal its
// own Host). Through the proxy the browser's Origin is the dev server, so we
// rewrite it to the API origin — but only when the request genuinely came from
// this dev server's own page. Foreign origins are forwarded unchanged and the
// backend keeps rejecting them.
const apiProxy: ProxyOptions = {
  target: API_TARGET,
  changeOrigin: true,
  configure(proxy) {
    proxy.on('proxyReq', (proxyReq, req) => {
      const origin = req.headers.origin
      if (origin && origin === `http://${req.headers.host}`) {
        proxyReq.setHeader('origin', new URL(API_TARGET).origin)
      }
    })
  },
}

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy: { '/api': apiProxy } },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy: { '/api': apiProxy } },
})
