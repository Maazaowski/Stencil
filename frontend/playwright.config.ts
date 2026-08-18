import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for Stencil's UI regression suite.
 *
 * The suite runs against an ALREADY-RUNNING stack (docker compose, or
 * `npm run dev` + a local backend) rather than starting one itself: the app
 * needs MySQL, Redis, a Celery worker and the watcher, which Playwright's
 * `webServer` cannot orchestrate meaningfully.
 *
 *   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
 *   npm run test:e2e
 *
 * Credentials come from the environment so the suite never hard-codes a
 * password. `npm run test:e2e` seeds a throwaway admin first (see
 * scripts/seed-e2e-user.mjs) unless E2E_EMAIL/E2E_PASSWORD are already set.
 */
export default defineConfig({
  testDir: "./e2e",
  // Storage state produced by the auth setup project.
  outputDir: "./e2e/.artifacts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  timeout: 45_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    testIdAttribute: "data-testid",
  },

  projects: [
    // Logs in once and writes the session cookie to e2e/.auth/user.json.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: "e2e/.auth/user.json" },
      dependencies: ["setup"],
      testIgnore: /(auth\.setup|unauthenticated\.spec|responsive\.spec)\.ts/,
    },
    // Auth-boundary tests must run WITHOUT a session cookie.
    {
      name: "chromium-anonymous",
      use: { ...devices["Desktop Chrome"] },
      testMatch: /unauthenticated\.spec\.ts/,
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 7"], storageState: "e2e/.auth/user.json" },
      dependencies: ["setup"],
      testMatch: /responsive\.spec\.ts/,
    },
  ],
});
