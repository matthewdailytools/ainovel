import { defineConfig } from '@playwright/test'

const live = process.env.AINOVEL_LIVE_URL
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: live ? '**/*.live.spec.ts' : '**/*.offline.spec.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: live || 'http://127.0.0.1:8877',
    headless: true,
    channel: process.env.AINOVEL_BROWSER_CHANNEL,
    viewport: { width: 1280, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: live
    ? undefined
    : {
        command:
          process.platform === 'win32'
            ? '".venv\\Scripts\\python.exe" scripts/e2e_server.py'
            : '.venv/bin/python scripts/e2e_server.py',
        url: 'http://127.0.0.1:8877/api/session',
        reuseExistingServer: false,
        timeout: 30_000,
      },
})
