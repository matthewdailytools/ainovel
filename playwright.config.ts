import { defineConfig } from '@playwright/test'
import { homedir } from 'node:os'
import { join } from 'node:path'

const live = process.env.AINOVEL_LIVE_URL
const liveEvidence = live
  ? join(homedir(), '.ainovel', 'acceptance', 'browser-live', new Date().toISOString().replace(/[:.]/g, '-'))
  : undefined
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: live ? '**/*.live.spec.ts' : '**/*.offline.spec.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  outputDir: liveEvidence ? join(liveEvidence, 'results') : 'test-results',
  reporter: [
    ['list'],
    [
      'html',
      { open: 'never', outputFolder: liveEvidence ? join(liveEvidence, 'report') : 'playwright-report' },
    ],
  ],
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
