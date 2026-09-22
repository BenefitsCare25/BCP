import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { singaporeTodayISO } from "../src/lib/business-date";

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

test("the HR ledger paginates and searches on the server", async ({ page }) => {
  const queries: string[] = [];
  const ledger = Array.from({ length: 21 }, (_, index) => ({
    ...claimFixture(),
    id: `ledger-${index + 1}`,
    employee_name: `Employee ${String(index + 1).padStart(2, "0")}`,
    invoice_number: `INV-${index + 1}`,
  }));
  const target = {
    ...claimFixture(),
    id: "ledger-target",
    employee_name: "Needle Employee",
  };

  await page.route("**/api/v1/hr/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/hr/auth/me") {
      await fulfilJson(route, HR_SESSION.state.me);
      return;
    }
    if (url.pathname === "/api/v1/hr/claims") {
      queries.push(url.search);
      const q = url.searchParams.get("q") ?? "";
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      await fulfilJson(
        route,
        q ? { items: [target], total: 1 } : {
          items: ledger.slice(offset, offset + limit),
          total: ledger.length,
        },
      );
      return;
    }
    await fulfilJson(route, { detail: "Unhandled HR E2E route" }, 404);
  });

  await page.goto("/hr/claims");
  await expect(page.getByText("Employee 01")).toBeVisible();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Employee 21")).toBeVisible();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await page.getByLabel("Search claims").fill("Needle");
  await expect(page.getByText("Needle Employee")).toBeVisible();

  expect(queries.some((query) => query.includes("offset=20") && query.includes("limit=20"))).toBe(true);
  expect(queries.some((query) => query.includes("q=Needle"))).toBe(true);
});

test("Singapore business dates do not roll back before 08:00", () => {
  expect(singaporeTodayISO(new Date("2026-09-21T16:30:00.000Z"))).toBe("2026-09-22");
});

test("HR can upload valid claim evidence between 10 MB and 15 MB", async ({ page }) => {
  const claim = claimFixture();
  let uploads = 0;
  await page.route("**/api/v1/hr/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/hr/auth/me") {
      await fulfilJson(route, HR_SESSION.state.me);
    } else if (
      url.pathname === `/api/v1/hr/claims/${claim.id}` &&
      request.method() === "GET"
    ) {
      await fulfilJson(route, claim);
    } else if (url.pathname === `/api/v1/hr/claims/${claim.id}/documents`) {
      uploads += 1;
      claim.documents = [{
        id: "large-doc",
        file_name: "large-receipt.pdf",
        doc_type: "receipt",
      }];
      await fulfilJson(route, claim, 201);
    } else {
      await fulfilJson(route, { detail: "Unhandled HR E2E route" }, 404);
    }
  });

  await page.goto(`/hr/claims/${claim.id}`);
  await page.getByLabel("Upload Receipt or invoice").setInputFiles({
    name: "large-receipt.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.alloc(10 * 1024 * 1024 + 1, 1),
  });
  await expect(page.getByText("large-receipt.pdf")).toBeVisible();
  expect(uploads).toBe(1);
});

test("HR preserves a foreign receipt currency and acknowledges the displayed conversion", async ({ page }, testInfo) => {
  let draftBody: Record<string, unknown> | null = null;
  const claim = {
    ...claimFixture(),
    id: "hr-claim-fx",
    claim_kind: "insured",
    claim_type: "Outpatient",
    currency: "USD",
    amount_claimed: 100,
  };

  await page.route("**/api/v1/hr/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/hr/auth/me") {
      await fulfilJson(route, HR_SESSION.state.me);
    } else if (url.pathname === "/api/v1/hr/claims/employees") {
      await fulfilJson(route, { items: [employee], total: 1 });
    } else if (url.pathname.endsWith(`/employees/${employee.id}/options`)) {
      await fulfilJson(route, {
        policy_year_start: "2026-01-01",
        policy_year_end: "2026-12-31",
        claimable_from: "2026-01-01",
        claimable_to: "2026-12-31",
        insured: [{
          product_code: "GPA",
          product_name: "Accident cover",
          claimable_from: "2026-01-01",
          claimable_to: "2026-12-31",
          requires_referral: false,
          diagnosis_required: false,
          claim_types: [{
            label: "Outpatient",
            sub_type: null,
            scope_key: "outpatient",
            requires_doctor_name: false,
            supports_stay_dates: false,
            anchor_mode: null,
          }],
        }],
        flex: null,
        claim_block: null,
        dependants: [],
        currencies: ["SGD", "USD", "MYR"],
        policy_currency: "SGD",
        hospitals: [],
      });
    } else if (url.pathname === "/api/v1/hr/claims/fx-quote") {
      await fulfilJson(route, {
        currency: "USD",
        policy_currency: "SGD",
        amount: 100,
        converted: 128.5,
        rate: 1.285,
        as_of_date: "2026-09-18",
        rate_date: "2026-09-18",
        stale: false,
        source: "test",
        available: true,
        note: "Test conversion",
      });
    } else if (url.pathname === "/api/v1/hr/claims" && request.method() === "POST") {
      draftBody = JSON.parse(request.postData() ?? "{}");
      await fulfilJson(route, claim, 201);
    } else if (url.pathname === `/api/v1/hr/claims/${claim.id}`) {
      await fulfilJson(route, claim);
    } else {
      await fulfilJson(route, { detail: "Unhandled HR E2E route" }, 404);
    }
  });

  await page.goto("/hr/claims/new");
  await page.getByRole("button", { name: /Avery Tan/ }).click();
  await page.getByLabel("Claim type").selectOption({ label: "Outpatient · Accident cover" });
  await page.getByLabel("Date incurred").fill("2026-09-18");
  await page.getByLabel("Currency").selectOption("USD");
  await page.getByLabel("Claim amount").fill("100");
  await page.getByLabel("Clinic or provider").fill("Foreign Clinic");
  await page.getByLabel("Invoice or receipt number").fill("USD-100");
  await expect(page.getByText("SGD 128.50")).toBeVisible();
  if (process.env.INSPRO_CAPTURE_REVIEW === "1") {
    const reviewDirectory = resolve(process.cwd(), "..", ".impeccable", "review");
    mkdirSync(reviewDirectory, { recursive: true });
    await page.screenshot({
      path: resolve(reviewDirectory, `${testInfo.project.name}-foreign-claim.png`),
      fullPage: true,
    });
  }
  await page.getByRole("button", { name: "Save and add evidence" }).click();
  await expect(page.getByRole("heading", { name: "Draft claim" })).toBeVisible();

  expect(draftBody).toMatchObject({
    currency: "USD",
    amount_claimed: 100,
    fx_acknowledged: true,
    fx_quoted_amount: 128.5,
  });
});

test("HR reuses a referral upload when a claim-create response is lost", async ({ page }) => {
  let referralUploads = 0;
  let createAttempts = 0;
  const createKeys: string[] = [];
  const createBodies: Record<string, unknown>[] = [];
  const claim = {
    ...claimFixture(),
    id: "hr-claim-specialist",
    claim_kind: "insured",
    claim_type: "Specialist",
  };

  await page.route("**/api/v1/hr/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/hr/auth/me") {
      await fulfilJson(route, HR_SESSION.state.me);
    } else if (url.pathname === "/api/v1/hr/claims/employees") {
      await fulfilJson(route, { items: [employee], total: 1 });
    } else if (url.pathname.endsWith(`/employees/${employee.id}/options`)) {
      await fulfilJson(route, {
        policy_year_start: "2026-01-01",
        policy_year_end: "2026-12-31",
        claimable_from: "2026-01-01",
        claimable_to: "2026-12-31",
        insured: [{
          product_code: "SP",
          product_name: "Specialist care",
          claimable_from: "2026-01-01",
          claimable_to: "2026-12-31",
          requires_referral: true,
          diagnosis_required: false,
          claim_types: [{
            label: "Specialist",
            sub_type: null,
            scope_key: "specialist",
            requires_doctor_name: false,
            supports_stay_dates: false,
            anchor_mode: "sp_course",
          }],
        }],
        flex: null,
        claim_block: null,
        dependants: [],
        currencies: ["SGD"],
        policy_currency: "SGD",
        hospitals: [],
      });
    } else if (url.pathname.endsWith(`/employees/${employee.id}/referrals`)) {
      referralUploads += 1;
      await fulfilJson(route, { id: "referral-1", file_name: "referral.pdf" }, 201);
    } else if (url.pathname === "/api/v1/hr/claims" && request.method() === "POST") {
      createAttempts += 1;
      createKeys.push(request.headers()["idempotency-key"] ?? "");
      createBodies.push(JSON.parse(request.postData() ?? "{}"));
      if (createAttempts === 1) {
        await route.abort("connectionreset");
      } else {
        await fulfilJson(route, claim, 201);
      }
    } else if (url.pathname === `/api/v1/hr/claims/${claim.id}`) {
      await fulfilJson(route, claim);
    } else {
      await fulfilJson(route, { detail: "Unhandled HR E2E route" }, 404);
    }
  });

  await page.goto("/hr/claims/new");
  await page.getByRole("button", { name: /Avery Tan/ }).click();
  await page.getByLabel("Claim type").selectOption({ label: "Specialist · Specialist care" });
  await page.getByLabel("Date incurred").fill("2026-09-18");
  await page.getByLabel("Claim amount").fill("80");
  await page.getByLabel("Clinic or provider").fill("Specialist Centre");
  await page.getByLabel("Invoice or receipt number").fill("SP-80");
  await page.getByLabel("Visit type").selectOption("follow_up");
  await page.getByLabel("Referral letter").setInputFiles({
    name: "referral.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n% synthetic referral\n"),
  });
  await page.getByRole("button", { name: "Save and add evidence" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "Save and add evidence" }).click();
  await expect(page.getByRole("heading", { name: "Draft claim" })).toBeVisible();

  expect(referralUploads).toBe(1);
  expect(createAttempts).toBe(2);
  expect(createKeys[0]).toBeTruthy();
  expect(createKeys[1]).toBe(createKeys[0]);
  expect(createBodies.map((body) => body.referral_document_id)).toEqual([
    "referral-1",
    "referral-1",
  ]);
});
