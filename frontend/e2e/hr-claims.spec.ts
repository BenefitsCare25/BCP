import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const HR_SESSION = {
  state: {
    token: "hr-e2e-token",
    expiresAt: "2099-01-01T00:00:00Z",
    me: {
      user_id: "hr-user-1",
      email: "hr@stm.demo",
      display_name: "Demo HR Admin",
      role: "client_hr",
      client_id: "client-demo",
      company_name: "STM (demo)",
      mfa_status: "confirmed",
      mfa_available: true,
    },
  },
  version: 0,
};

const employee = {
  id: "employee-demo-1",
  name: "Avery Tan",
  staff_id: "STM-0042",
  period: "2026-01-01 - 2026-12-31",
};

function claimFixture() {
  return {
    id: "hr-claim-1",
    employee_id: employee.id,
    employee_name: employee.name,
    policy_year_id: "year-2026",
    claim_ref: null as string | null,
    claim_kind: "flex",
    claim_type: "Wellness",
    provider_name: "Northstar Wellness",
    invoice_number: "NW-2026-0042",
    status: "draft",
    incurred_date: "2026-09-18",
    amount_claimed: 88.5,
    currency: "SGD",
    created_by_user_id: "hr-user-1",
    submitted_by_name: "Demo HR Admin",
    submitted_by_email: "hr@stm.demo",
    submission_channel: "hr",
    can_add_evidence: true,
    can_submit: true,
    created_at: "2026-09-22T08:00:00Z",
    submitted_at: null as string | null,
    doc_slots: [
      {
        key: "receipt",
        label: "Receipt or invoice",
        instructions: "Show the provider, date and amount paid.",
      },
    ],
    documents: [] as { id: string; file_name: string; doc_type: string }[],
  };
}

async function fulfilJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function assertAccessible(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(result.violations).toEqual([]);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((session) => {
    localStorage.setItem("inspro-hr-session", JSON.stringify(session));
  }, HR_SESSION);
});

test("HR submits and tracks one employee claim with evidence", async ({ page }, testInfo) => {
  const runtimeErrors: string[] = [];
  const requests: { method: string; path: string; headers: Record<string, string>; body: string | null }[] = [];
  let created = false;
  const claim = claimFixture();

  page.on("pageerror", (error) => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) runtimeErrors.push(`http ${response.status()}: ${response.url()}`);
  });

  await page.route("**/api/v1/hr/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({
      method: request.method(),
      path: url.pathname + url.search,
      headers: request.headers(),
      body: request.postData(),
    });

    if (url.pathname === "/api/v1/hr/auth/me") {
      await fulfilJson(route, HR_SESSION.state.me);
      return;
    }
    if (url.pathname === "/api/v1/hr/claims/employees") {
      await fulfilJson(route, { items: [employee], total: 1 });
      return;
    }
    if (url.pathname === `/api/v1/hr/claims/employees/${employee.id}/options`) {
      await fulfilJson(route, {
        policy_year_start: "2026-01-01",
        policy_year_end: "2026-12-31",
        claimable_from: "2026-01-01",
        claimable_to: "2026-12-31",
        insured: [],
        flex: {
          currency: "SGD",
          wallet_amount: 800,
          flex_balance: 620,
          categories: [
            {
              name: "Wellness",
              sub_limit: 250,
              note: null,
              doc_slots: claim.doc_slots,
            },
          ],
          doc_slots: claim.doc_slots,
          claimable_from: "2026-01-01",
          claimable_to: "2026-12-31",
        },
        claim_block: null,
        dependants: [],
        currencies: ["SGD"],
        policy_currency: "SGD",
        hospitals: [],
      });
      return;
    }
    if (url.pathname === "/api/v1/hr/claims" && request.method() === "POST") {
      created = true;
      await fulfilJson(route, claim, 201);
      return;
    }
    if (url.pathname === "/api/v1/hr/claims" && request.method() === "GET") {
      await fulfilJson(route, {
        items: created ? [claim] : [],
        total: created ? 1 : 0,
      });
      return;
    }
    if (url.pathname === `/api/v1/hr/claims/${claim.id}` && request.method() === "GET") {
      await fulfilJson(route, claim);
      return;
    }
    if (url.pathname === `/api/v1/hr/claims/${claim.id}/documents`) {
      claim.documents = [
        { id: "doc-1", file_name: "wellness-receipt.pdf", doc_type: "receipt" },
      ];
      await fulfilJson(route, claim, 201);
      return;
    }
    if (url.pathname === `/api/v1/hr/claims/${claim.id}/submit`) {
      claim.status = "submitted";
      claim.claim_ref = "CLM-2026-0042";
      claim.submitted_at = "2026-09-22T08:10:00Z";
      claim.can_add_evidence = true;
      claim.can_submit = false;
      await fulfilJson(route, claim);
      return;
    }
    await fulfilJson(route, { detail: "Unhandled HR E2E route" }, 404);
  });

  await page.goto("/hr/claims");
  await expect(page.getByRole("heading", { name: "Employee claims" })).toBeVisible();
  await expect(page.getByText("No delegated claims yet")).toBeVisible();
  await page.getByRole("link", { name: "Start a claim" }).click();

  await page.getByRole("button", { name: /Avery Tan/ }).click();
  await page.getByLabel("Claim type").selectOption({ label: "Wellness · Flexible benefits" });
  await page.getByLabel("Date incurred").fill("2026-09-18");
  await page.getByLabel("Claim amount").fill("88.50");
  await page.getByLabel("Clinic or provider").fill("Northstar Wellness");
  await page.getByLabel("Invoice or receipt number").fill("NW-2026-0042");
  await assertAccessible(page);
  await page.getByRole("button", { name: "Save and add evidence" }).click();

  await expect(page.getByRole("heading", { name: "Draft claim" })).toBeVisible();
  await expect(page.getByText("Filed for Avery Tan")).toBeVisible();
  await expect(page.getByText(/By Demo HR Admin/)).toBeVisible();
  await page.getByLabel("Upload Receipt or invoice").setInputFiles({
    name: "wellness-receipt.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n% synthetic HR claim evidence\n"),
  });
  await expect(page.getByText("wellness-receipt.pdf")).toBeVisible();
  await expect(page.getByText("All required evidence is attached.")).toBeVisible();
  await assertAccessible(page);
  await page.getByRole("button", { name: "Submit claim" }).click();

  await expect(page.getByRole("heading", { name: "CLM-2026-0042" })).toBeVisible();
  await expect(page.locator("span").filter({ hasText: /^Submitted$/ })).toBeVisible();
  if (process.env.INSPRO_CAPTURE_REVIEW === "1") {
    const reviewDirectory = resolve(process.cwd(), "..", ".impeccable", "review");
    mkdirSync(reviewDirectory, { recursive: true });
    await page.screenshot({
      path: resolve(reviewDirectory, `${testInfo.project.name}.png`),
      fullPage: true,
    });
  }
  await page.getByRole("link", { name: "All claims" }).click();
  await expect(page.getByText("Avery Tan")).toBeVisible();
  await expect(page.getByText("CLM-2026-0042")).toBeVisible();

  const protectedRequests = requests.filter((item) => !item.path.includes("/auth/"));
  expect(protectedRequests.length).toBeGreaterThan(0);
  for (const request of protectedRequests) {
    expect(request.headers.authorization).toBe("Bearer hr-e2e-token");
    expect(request.headers["x-inspro-tenant-slug"]).toBe("demo");
  }
  const draft = requests.find(
    (item) => item.method === "POST" && item.path === "/api/v1/hr/claims",
  );
  expect(draft?.headers["idempotency-key"]).toBeTruthy();
  expect(JSON.parse(draft?.body ?? "{}")).toMatchObject({
    employee_id: employee.id,
    claim_kind: "flex",
    flex_category_name: "Wellness",
    currency: "SGD",
  });
  const evidence = requests.find((item) => item.path.endsWith("/documents"));
  expect(evidence?.body).toContain('name="doc_type"');
  expect(evidence?.body).toContain("receipt");
  expect(
    requests.find((item) => item.path.endsWith("/submit"))?.headers[
      "idempotency-key"
    ],
  ).toBeTruthy();
  expect(runtimeErrors).toEqual([]);
});

test("an unauthenticated HR visitor is returned to sign in", async ({ page }) => {
  await page.addInitScript(() => localStorage.removeItem("inspro-hr-session"));
  await page.route("**/api/v1/hr/auth/refresh", (route) =>
    fulfilJson(route, { detail: "Not authenticated" }, 401),
  );
  await page.goto("/hr/claims");
  await expect(page).toHaveURL(/\/hr\/sign-in$/);
  await expect(page.getByRole("heading", { name: /Sign in/i })).toBeVisible();
});
