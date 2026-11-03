/// <reference types="vitest/config" />
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const root = path.dirname(fileURLToPath(import.meta.url));
// The FastAPI backend (app/server) serves /api; override with API_PROXY_TARGET.
const target = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000';
const proxy = { '/api': { target, changeOrigin: true } };
// VITE_DEMO=static builds the public static demo: no server, the API runs in the browser (src/demo).
const staticDemo = process.env.VITE_DEMO === 'static';

export default defineConfig({
  base: '/',
  define: { __STATIC_DEMO__: JSON.stringify(staticDemo) },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(root, 'src') } },
  server: { port: 5180, proxy },
  preview: { port: 5180, proxy: staticDemo ? {} : proxy },
  build: {
    // The FastAPI app serves the built client from app/server/static; the static demo goes to dist/demo.
    outDir: staticDemo ? 'dist/demo' : '../server/static',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (/node_modules\/(lightweight-charts|fancy-canvas)\//.test(id)) return 'lightweight-charts';
          if (/node_modules\/(recharts|d3-[a-z-]+|victory-vendor|@reduxjs|redux|react-redux|immer|reselect|es-toolkit|decimal\.js-light|eventemitter3|internmap|tiny-invariant|use-sync-external-store)\//.test(id)) {
            return 'recharts';
          }
          return undefined;
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
});
