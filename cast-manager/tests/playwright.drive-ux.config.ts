import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  testMatch: 'drive-ux.spec.ts',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.CAST_MANAGER_URL || 'http://127.0.0.1:4174',
    colorScheme: 'light',
    trace: 'on-first-retry',
  },
})
