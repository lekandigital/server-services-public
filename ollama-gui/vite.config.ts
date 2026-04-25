import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          'markdown': ['markdown-it', 'highlight.js'],
        },
      },
    },
  },
  server: {
    port: 8081,
    host: '0.0.0.0',
    proxy: process.env.VITE_NO_PROXY
      ? {}
      : {
          '/api': {
            target: 'http://localhost:11434',
            changeOrigin: true,
          },
        },
  },
  preview: {
    port: 8081,
    host: '0.0.0.0',
  },
})
