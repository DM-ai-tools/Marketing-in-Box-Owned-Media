import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The backend's own default (see application/backend/app/__main__.py). Override with
// VITE_API_TARGET when the API is served somewhere else — in production that is the deployed
// backend's public URL, read at *runtime* by the preview server rather than baked into the
// bundle, since the proxy below runs server-side.
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8001'

// Every call in src/ is a relative `/api/*` fetch, and the backend mounts its routers at bare
// `/auth`, `/pipeline`, ... (app/main.py) — so this rewrite is what makes the two halves line up.
// It is shared by `server` and `preview` because the deployed frontend needs exactly the same
// same-origin proxy the dev server provides: the session cookie is SameSite=Lax, so a browser
// would refuse to send it to a backend on a different domain.
const apiProxy: Record<string, ProxyOptions> = {
  '/api': {
    target: API_TARGET,
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, ''),
    // Without this, a backend that isn't listening on API_TARGET surfaces in the browser as a
    // bare "Failed to fetch" with no hint of *why* — which reads as "chat history is broken"
    // rather than "the API is on a different port". Answer with the actual diagnosis instead.
    configure: (proxy) => {
      proxy.on('error', (err, _req, res) => {
        const message =
          `Backend not reachable at ${API_TARGET} (${err.message}). Start it with ` +
          '`python -m app --reload` from application/backend, or point VITE_API_TARGET at ' +
          'the port it is actually running on.'
        console.error(`[vite proxy] ${message}`)
        if ('writeHead' in res && !res.headersSent) {
          res.writeHead(502, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ detail: message }))
        } else {
          res.end()
        }
      })
    },
  },
}

// Vite refuses requests whose Host header it does not recognise (DNS-rebinding protection), which
// on a platform-assigned domain means a blanket "Blocked request" until the domain is named here.
// Railway injects RAILWAY_PUBLIC_DOMAIN, so the generated domain allows itself; the wildcard keeps
// working if the service is renamed, and a custom domain can be added via ALLOWED_HOSTS.
const allowedHosts = [
  process.env.RAILWAY_PUBLIC_DOMAIN,
  '.up.railway.app',
  ...(process.env.ALLOWED_HOSTS?.split(',').map((h) => h.trim()) ?? []),
].filter((h): h is string => Boolean(h))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: apiProxy,
    // Smoke builds write into smoke/out-* under the frontend root. Watching those (especially on
    // Windows) can EBUSY-crash the dev server mid-run; they are never source for HMR.
    watch: {
      ignored: ['**/smoke/out-*/**'],
    },
  },
  preview: {
    host: true,
    port: Number(process.env.PORT) || 4173,
    allowedHosts,
    proxy: apiProxy,
  },
})
