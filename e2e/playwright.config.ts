import { defineConfig, devices } from "@playwright/test";

// Run against the Compose stack: `make up && make seed`, then `npm test` (or `npm run demo`).
export default defineConfig({
  timeout: 180_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.OPS_INBOX_URL ?? "http://localhost:5410",
    ...devices["Desktop Chrome"],
    viewport: { width: 1280, height: 720 },
  },
  projects: [
    { name: "e2e", testDir: "./tests" },
    {
      name: "demo",
      testDir: "./demo",
      use: {
        video: { mode: "on", size: { width: 1280, height: 720 } },
        launchOptions: { slowMo: 60 },
        deviceScaleFactor: 1,
      },
      outputDir: "./demo-output",
    },
  ],
});
