import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./evals/browser",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:41871",
    browserName: "chromium",
    viewport: { width: 390, height: 844 },
    reducedMotion: "reduce",
  },
  webServer: {
    command: "npm run dev -- --port 41871 --strictPort",
    url: "http://127.0.0.1:41871",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
