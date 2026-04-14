import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': {
        target: 'https://localhost:9444',
        changeOrigin: true,
        secure: false,   // self-signed cert — OK for local dev
      },
    },
  },
})
