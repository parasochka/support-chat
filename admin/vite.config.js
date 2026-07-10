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
    rollupOptions: {
      output: {
        // Split the heavyweight vendors out of the entry chunk (the app code
        // itself is already split per page via React.lazy in App.jsx). The
        // browser caches these across releases that only touch app code.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('recharts') || id.includes('d3-')) return 'recharts';
          if (id.includes('@mui')) return 'mui';
          if (id.includes('react-admin') || id.includes('/ra-')) return 'ra';
          return 'vendor';
        },
      },
    },
  },
});
