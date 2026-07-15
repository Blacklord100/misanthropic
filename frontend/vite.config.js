import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'
import tailwindcss from '@tailwindcss/vite'

// Build straight into the Python package: the stdlib server serves
// src/misanthropic/static at "/". The dist is checked in so pip/pipx/py2app
// installs need no Node at runtime.
export default defineConfig({
  plugins: [preact(), tailwindcss()],
  build: {
    outDir: '../src/misanthropic/static',
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` proxies API calls to a local dev server instance —
    // NEVER the default 8787 (that may be a live instance someone is using).
    proxy: Object.fromEntries(
      ['/admin', '/health', '/v1'].map((p) => [
        p,
        { target: 'http://127.0.0.1:8788', changeOrigin: true },
      ]),
    ),
  },
})
