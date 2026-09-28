import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("AI Settings remains accessible before choosing a company", async ({
  page,
}) => {
  await page.goto("/settings/ai");
  await expect(
    page.getByRole("heading", { name: "AI Settings", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Select a company", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Policies", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "No published policies yet" }),
  ).toBeVisible();
});

test("AI Settings brings provider, policies and real-role guidance together", async ({
  page,
  request,
}) => {
  const me = await (await request.get("/api/v1/me")).json();
  await page.addInitScript(
    (id) =>
      localStorage.setItem(
        "inspro-session",
        JSON.stringify({ state: { activeClientId: id }, version: 0 }),
      ),
    me.accessible_clients[0].id,
  );
  await page.goto("/settings/ai?tab=provider");
  await expect(
    page.getByRole("heading", { name: "AI Settings", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("This company's AI key", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "AI oversight", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Policies", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "No published policies yet" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Upload policy", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator("[data-context-bar=platform]")).toContainText(
    "All companies",
  );
  await page.getByRole("tab", { name: "AI use & access", exact: true }).click();
  await expect(page.getByText("broker_admin", { exact: true })).toBeVisible();
  await expect(page.getByText("broker_viewer", { exact: true })).toBeVisible();
  await expect(page.getByText("system_admin", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: /owner/i })).toHaveCount(0);
  const axe = await new AxeBuilder({ page })
    .include("main")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(axe.violations).toEqual([]);
  await page.goto("/firm/ai-oversight");
  await expect(page).toHaveURL(/settings\/ai\?tab=policies/);
});

test("broker viewer can read policies without selecting a company and cannot modify them", async ({
  page,
  request,
}) => {
  const me = await (await request.get("/api/v1/me")).json();
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ json: { ...me, role: "broker_viewer" } }),
  );
  const record = {
    id: "policy-one",
    policy_id: "policy",
    title: "Platform AI usage policy",
    category: "AI usage policy",
    version: 1,
    status: "published",
    review_due: "2027-09-28",
    file_name: "policy.pdf",
    size_bytes: 1200,
    uploaded_by: "System administrator",
    created_at: "2026-09-28T05:00:00Z",
    published_by: "System administrator",
    published_at: "2026-09-28T06:00:00Z",
    archived_at: null,
    archived_by: null,
  };
  await page.route("**/api/v1/ai-policies?*", (route) =>
    route.fulfill({ json: { items: [record], has_more: false } }),
  );
  await page.route("**/api/v1/ai-policies/policy-one/download", (route) =>
    route.fulfill({
      body: "%PDF-1.4\nPolicy fixture\n%%EOF",
      contentType: "application/pdf",
    }),
  );
  await page.goto("/settings/ai?tab=policies");
  await expect(
    page.getByRole("heading", { name: record.title, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Select a company", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: /Upload policy|Publish|Archive|Add version/,
    }),
  ).toHaveCount(0);
  const downloaded = page.waitForEvent("download");
  await page
    .getByRole("button", {
      name: "Download Platform AI usage policy version 1",
    })
    .click();
  expect((await downloaded).suggestedFilename()).toBe("policy.pdf");
  const axe = await new AxeBuilder({ page })
    .include("main")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(axe.violations).toEqual([]);
});

test("system administrator uploads and explicitly publishes a policy draft", async ({
  page,
  request,
}) => {
  const me = await (await request.get("/api/v1/me")).json();
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ json: { ...me, role: "system_admin" } }),
  );
  let items: Record<string, unknown>[] = [];
  await page.route("**/api/v1/ai-policies**", async (route) => {
    const request = route.request();
    if (request.method() === "POST" && request.url().endsWith("/publish")) {
      items[0] = {
        ...items[0],
        status: "published",
        published_by: "Test administrator",
        published_at: "2026-09-28T06:00:00Z",
      };
      await route.fulfill({ json: items[0] });
    } else if (request.method() === "POST") {
      expect(request.postDataBuffer()?.toString()).toContain("%PDF-1.4");
      items = [
        {
          id: "draft-one",
          policy_id: "policy",
          version: 1,
          title: "Uploaded AI policy",
          category: "AI usage policy",
          status: "draft",
          review_due: null,
          file_name: "policy.pdf",
          size_bytes: 30,
          uploaded_by: "Test administrator",
          created_at: "2026-09-28T05:00:00Z",
          published_at: null,
          published_by: null,
          archived_at: null,
          archived_by: null,
        },
      ];
      await route.fulfill({ status: 201, json: items[0] });
    } else await route.fulfill({ json: { items, has_more: false } });
  });
  await page.goto("/settings/ai?tab=policies");
  await page
    .getByRole("button", { name: "Upload policy", exact: true })
    .click();
  await page
    .getByLabel("Policy title", { exact: true })
    .fill("Uploaded AI policy");
  await page.getByLabel("Policy PDF", { exact: true }).setInputFiles({
    name: "policy.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\nPolicy fixture\n%%EOF"),
  });
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Publish this policy version?" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Publish policy", exact: true })
    .click();
  await expect(page.getByText("Published", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Published", { exact: true })).toBeVisible();
});
