import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  use: {
    baseURL: process.env.CAST_MANAGER_URL || 'http://127.0.0.1:8004',
    trace: 'on-first-retry',
  },
})
