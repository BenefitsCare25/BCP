import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

function monitorRuntime(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      errors.push(`http ${response.status()}: ${response.url()}`);
    }
  });
  return errors;
}

async function assertAccessible(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(result.violations).toEqual([]);
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({
    path: testInfo.outputPath(`${name}.png`),
    fullPage: true,
    animations: "disabled",
  });
}

async function openClaimsReview(page: Page, tab: string) {
  await page.goto(`/claims/review?tab=${tab}`);
  await page.getByRole("combobox", { name: "Select company" }).waitFor();
  const companyGate = page.getByRole("heading", { name: "Select a company" });
  if (await companyGate.isVisible()) {
    await page.getByRole("button", { name: /CDL 2026/ }).click();
  }
}

test("signed-in administrator can use every Claims Review tab", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "queue");

  const queue = page.getByRole("tab", { name: "Queue" });
  const messages = page.getByRole("tab", { name: /Messages/ });
  const reviewRules = page.getByRole("tab", { name: "Review rules" });
  const claimSettings = page.getByRole("tab", { name: "Claim settings" });
  await expect(queue).toHaveAttribute("aria-selected", "true");
  await expect(messages).toBeVisible();
  await expect(reviewRules).toBeVisible();
  await expect(claimSettings).toBeVisible();
  await expect(page.getByText("Claims review queue")).toBeVisible();

  await messages.click();
  await expect(messages).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Messages" })).toBeVisible();

  await reviewRules.click();
  await expect(page.getByText("AI review rules by claim type")).toBeVisible();
  await expect(page.getByText("GP (General Practitioner)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Customize" }).first().click();
  await expect(page.getByText("Use this setup")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save rules" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await screenshot(page, testInfo, "review-rules");
  await assertAccessible(page);

  await claimSettings.click();
  await expect(page.getByText("Claim document types")).toBeVisible();
  await expect(page.getByText("Discharge Summary", { exact: true })).toBeVisible();
  await screenshot(page, testInfo, "claim-settings");
  await assertAccessible(page);

  expect(runtimeErrors).toEqual([]);
});

test("Claims Review stays within the viewport", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "ai-extraction");
  await expect(page.getByText("AI review rules by claim type")).toBeVisible();
  await expect(page.getByText("GP (General Practitioner)", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await screenshot(page, testInfo, "review-rules-mobile");
  await assertAccessible(page);
  expect(runtimeErrors).toEqual([]);
});
