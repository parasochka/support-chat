import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Static SPA build (no SSR). `npm run build` emits ./dist; in production the
// FastAPI service serves it at /admin (see the two-stage Dockerfile), hence
// the /admin/ base. The app uses a hash router, so one HTML entry suffices.
export default defineConfig({
  base: '/admin/',
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
