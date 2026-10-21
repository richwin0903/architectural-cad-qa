import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import dxfFontAliases from './build/dxfFontAliases';

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/cad/' : '/',
  plugins: [react(), dxfFontAliases()],
  worker: { plugins: () => [dxfFontAliases()] },
  optimizeDeps: { exclude: ['dxf-viewer'] },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8322',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
}));
