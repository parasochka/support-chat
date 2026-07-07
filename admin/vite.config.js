import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Static SPA build (no SSR). `npm run build` emits ./dist for Railway.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
