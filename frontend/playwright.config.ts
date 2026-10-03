import { defineConfig, devices } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

function testPort(name: string, fallback: number): number {
  const port = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error(`${name} must be a port between 1024 and 65535`);
  }
  return port;
}

const apiPort = testPort("INSPRO_E2E_API_PORT", 8006);
const webPort = testPort("INSPRO_E2E_WEB_PORT", 5176);
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${webPort}`;
const e2eDirectory = mkdtempSync(join(tmpdir(), "inspro-e2e-"));
const e2eDatabase = join(e2eDirectory, "inspro.db");
const databaseUrl = `sqlite:///${e2eDatabase.replaceAll("\\", "/")}`;
const e2eEncryptionKey = `${randomBytes(32).toString("base64url")}=`;
const env = {
  ...process.env,
  INSPRO_AI_KEY_ENCRYPTION_KEY: e2eEncryptionKey,
  INSPRO_DATABASE_URL: databaseUrl,
  INSPRO_TENANT_MODE: "header",
};
for (const args of [
  ["run", "alembic", "upgrade", "head"],
  ["run", "python", "-m", "scripts.seed_demo"],
]) {
  const result = spawnSync("uv", args, {
    cwd: resolve("../backend"),
    env,
    encoding: "utf8",
    shell: process.platform === "win32",
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "E2E database setup failed");
  }
}
process.once("exit", () => rmSync(e2eDirectory, { recursive: true, force: true }));

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  globalTimeout: process.env.CI ? 8 * 60_000 : undefined,
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
        `uv run uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: "../backend",
      url: `${apiOrigin}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        INSPRO_ENV: "dev",
        INSPRO_AUTH_MODE: "mock",
        INSPRO_TENANT_MODE: "header",
        INSPRO_MOCK_ROLE: "broker_admin",
        INSPRO_AI_KEY_ENCRYPTION_KEY: e2eEncryptionKey,
        INSPRO_DATABASE_URL: databaseUrl,
        INSPRO_E2E: "1",
        INSPRO_CORS_ORIGINS: new URL(baseURL).origin,
      },
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${webPort}`,
      cwd: ".",
      url: baseURL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { INSPRO_DEV_API_TARGET: apiOrigin, VITE_TENANT_MODE: "header" },
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
