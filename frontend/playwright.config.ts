import { defineConfig, devices } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:5176";
const e2eDirectory = mkdtempSync(join(tmpdir(), "inspro-e2e-"));
const e2eDatabase = join(e2eDirectory, "inspro.db");
const databaseUrl = `sqlite:///${e2eDatabase.replaceAll("\\", "/")}`;
const seedDatabase = resolve("../backend/inspro.db");
if (existsSync(seedDatabase)) {
  copyFileSync(seedDatabase, e2eDatabase);
} else {
  const env = { ...process.env, INSPRO_DATABASE_URL: databaseUrl };
  for (const args of [
    ["run", "alembic", "upgrade", "head"],
    ["run", "python", "-m", "scripts.seed_demo"],
  ]) {
    const result = spawnSync("uv", args, {
      cwd: resolve("../backend"),
      env,
      encoding: "utf8",
      shell: process.platform === "win32",
    });
    if (result.status !== 0) {
      throw new Error(result.stderr || result.stdout || "E2E database setup failed");
    }
  }
}
process.once("exit", () => rmSync(e2eDirectory, { recursive: true, force: true }));

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command:
        "uv run uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: "../backend",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        INSPRO_ENV: "dev",
        INSPRO_AUTH_MODE: "mock",
        INSPRO_MOCK_ROLE: "broker_admin",
        INSPRO_DATABASE_URL: databaseUrl,
        INSPRO_E2E: "1",
      },
    },
    {
      command: "pnpm dev --host 127.0.0.1 --port 5176",
      cwd: ".",
      url: baseURL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 960 } },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
