import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    host: true,
    port: 8956,
  },
  build: {
    target: 'es2020',
    outDir: 'dist',
  },
})
