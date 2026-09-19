import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

test.use({ actionTimeout: 15_000 });

test("broker WICA uses one workspace from intake to settlement", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const me = await (await request.get("/api/v1/me")).json();
  const clientId = me.active_client_id;
  await page.addInitScript(client => {
    localStorage.setItem("inspro-session", JSON.stringify({ state: { activeClientId: client, currentPolicyYearId: null, policyYearClientId: null }, version: 0 }));
  }, clientId);
  const headers = { "X-Inspro-Client": clientId };
  const settings = await (await request.get("/api/v1/wica/settings", { headers })).json();
  if (!settings.enabled) {
    await page.goto("/settings/company?tab=wica");
    await page.getByRole("button", { name: "Add period", exact: true }).click();
    await page.getByLabel("Period label", { exact: true }).fill("2026 WICA");
    await page.getByLabel("Start date", { exact: true }).fill("2026-01-01");
    await page.getByLabel("End date", { exact: true }).fill("2026-12-31");
    await page.getByLabel("Enable WICA").check();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("In use · period locked")).toHaveCount(0);
    await expect.poll(async () => (await (await request.get("/api/v1/wica/settings", { headers })).json()).enabled).toBe(true);
  }
  await page.goto("/claims/wica");
  await page.getByRole("button", { name: "New incident", exact: true }).click();
  const create = page.getByRole("form", { name: "New incident" });
  await create.getByLabel("Incident date", { exact: true }).fill("2026-08-10");
  await create.getByLabel("Employee not in member listing").check();
  await create.getByLabel("Employee name", { exact: true }).fill(`Review Employee ${info.project.name}`);
  await create.getByLabel("Staff ID", { exact: true }).fill("WICA-REVIEW");
  await create.getByLabel("MOM I-Report number (optional)").fill("IR-2026-0810");
  await create.getByRole("button", { name: "Create incident" }).click();
  await expect(page).toHaveURL(/\/claims\/wica\?incident=/);
  const workspaceURL = page.url();
  // Return before any workspace mutation invalidates the cached register.
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByRole("button", { name: `Review Employee ${info.project.name}`, exact: true })).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: `Review Employee ${info.project.name}`, exact: true }).click();
  await expect(page).toHaveURL(workspaceURL);
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles([
    { name: "Medical bill.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 medical bill fixture") },
    { name: "Medical certificate.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 medical certificate fixture") },
  ]);
  await expect(page.getByRole("button", { name: "Medical certificate.pdf", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Medical certificate.pdf", exact: true }).click();
  let form = page.getByRole("form", { name: "Tag Medical certificate.pdf" });
  await form.getByLabel("Document type", { exact: true }).selectOption("Medical Certificate");
  await form.getByLabel("Document date", { exact: true }).fill("2026-08-10");
  await form.getByLabel("Benefit type", { exact: true }).selectOption("Others");
  await form.getByRole("button", { name: "Save tags" }).click();
  await expect(page.getByText("Supporting document", { exact: true }).filter({ visible: true })).toBeVisible();
  await page.getByRole("button", { name: "Medical bill.pdf", exact: true }).click();
  form = page.getByRole("form", { name: "Tag Medical bill.pdf" });
  await form.getByLabel("Document type", { exact: true }).selectOption("Medical Bill");
  await form.getByLabel("Document date", { exact: true }).fill("2026-08-10");
  await form.getByLabel("Benefit type", { exact: true }).selectOption("Medical");
  await form.getByLabel("Medical certificate.pdf", { exact: true }).check();
  await form.getByRole("button", { name: "Save tags" }).click();
  await page.getByRole("button", { name: "Set pending insurer approval", exact: true }).click();
  await expect(page.getByText("Pending insurer approval", { exact: true }).filter({ visible: true })).toBeVisible();
  await page.getByRole("button", { name: "Prepare all pending", exact: true }).click();
  await expect(page.getByRole("button", { name: "Download pack", exact: true })).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download pack", exact: true }).click()]);
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  await page.getByRole("button", { name: "Record sent", exact: true }).click();
  const sent = page.getByRole("form", { name: "Record external submission" });
  await sent.getByLabel("Sent date", { exact: true }).fill("2026-08-11");
  await sent.getByLabel("Recipient / submission reference").fill("Insurer external submission TEST-001");
  await sent.getByRole("button", { name: "Save sent record" }).click();
  await expect(page.getByText(/Sent 11 Aug 2026/)).toBeVisible();
  await page.getByRole("button", { name: "Record settlement", exact: true }).click();
  const settle = page.getByRole("form", { name: "Record settlement" });
  await settle.getByLabel("Settlement amount (SGD)", { exact: true }).fill("250");
  await settle.getByLabel("Settlement date", { exact: true }).fill("2026-08-20");
  await settle.getByRole("button", { name: "Save settlement" }).click();
  await expect(page.getByText("Settled", { exact: true }).filter({ visible: true })).toBeVisible();
  await expect(page).toHaveURL(workspaceURL);
  await expect(page.locator("main h1, main h2")).toHaveCount(0);
  await expect(page.getByText(/Request documents|Pending documents|Employee portal/i)).toHaveCount(0);
  const accessibility = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  const out = resolve("../.impeccable/review"); mkdirSync(out, { recursive: true });
  await page.locator("main").evaluate(el => { el.scrollTop = 0; });
  await page.evaluate(() => window.scrollTo(0, 0));
  const geometry = await page.locator(".wica-documents").evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth, nameWidth: el.querySelector("tbody tr td:nth-child(2)")?.getBoundingClientRect().width ?? 0 }));
  expect(geometry.scroll).toBeLessThanOrEqual(geometry.width + 1);
  if (info.project.name.startsWith("mobile")) expect(geometry.nameWidth).toBeGreaterThan(180);
  await page.screenshot({ path: resolve(out, info.project.name.startsWith("mobile") ? "mobile.png" : "desktop.png"), fullPage: true });
  if (!info.project.name.startsWith("mobile")) {
    await page.setViewportSize({ width: 1918, height: 922 });
    await page.screenshot({ path: resolve(out, "user-1918.png"), fullPage: true });
  }
  await page.reload();
  await expect(page.getByText("Settled", { exact: true }).filter({ visible: true })).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await page.getByLabel("Search incidents").fill(`Review Employee ${info.project.name}`);
  await expect(page.getByRole("button", { name: `Review Employee ${info.project.name}`, exact: true })).toBeVisible();
  if (!info.project.name.startsWith("mobile")) {
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.locator("main").evaluate(el => { el.scrollTop = 0; });
    await page.screenshot({ path: resolve(out, "wica-list.png"), fullPage: true });
    await page.goto("/settings/company?tab=wica");
    await expect(page.getByText("In use · period locked")).toBeVisible();
    await page.screenshot({ path: resolve(out, "wica-settings.png"), fullPage: true });
  }
});
