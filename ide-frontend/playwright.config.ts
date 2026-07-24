import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  retries: 0,
  preserveOutput: 'always',
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:9490',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium-desktop',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chromium-mobile',
      testMatch: /cockpit-panels\.spec\.ts/,
      use: { ...devices['Pixel 7'] },
    },
    {
      name: 'firefox-smoke',
      testMatch: /cockpit-panels\.spec\.ts/,
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit-smoke',
      testMatch: /cockpit-panels\.spec\.ts/,
      use: { ...devices['Desktop Safari'] },
    },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 9490',
    url: 'http://127.0.0.1:9490',
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
