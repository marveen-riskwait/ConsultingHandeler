import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies the API and the socket to Flask on :3001, so the
// browser talks to everything through one origin (:3000). That is what lets
// httpOnly auth cookies flow in two-server dev exactly as they do in
// production (where Flask serves the built front on its own origin) — no
// cross-origin cookies, no CORS. Set BACKEND to point elsewhere if needed.
const BACKEND = process.env.VITE_PROXY_TARGET || 'http://localhost:3001'

export default defineConfig({
    plugins: [react()],
    server: {
        port: 3000,
        proxy: {
            '/api': { target: BACKEND, changeOrigin: true },
            '/socket.io': { target: BACKEND, changeOrigin: true, ws: true },
        },
    },
    build: { outDir: 'dist' },
})
