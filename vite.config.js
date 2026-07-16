import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        admin: resolve(import.meta.dirname, 'admin.html'),
        request: resolve(import.meta.dirname, 'request.html'),
        portal: resolve(import.meta.dirname, 'portal.html'),
      },
    },
  },
});
