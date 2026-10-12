import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

// No-login QR pages, fully intercepted: synthetic company, people and figures.
const TOKEN = "SyntheticLinkToken_abcdefghijklmnopqrstuvwxyz0123";

function tier(category: string, plan: string, baseline = true) {
  return {
    key: `${category}::${plan}`, tier_category_id: category, plan_code: plan, label: `Plan ${plan}`,
    participation: "compulsory", dependant_participation: "voluntary", direction: "same",
    is_baseline: baseline, is_current: false, financials: null, price_tag: null,
    differences: [], differences_total: 0,
  };
}

function cohort(category: string, label: string, plan: string) {
  return {
    category_id: category, label, cohort: label.toLowerCase(),
    tier_set: {
      product_id: "p-ghs", product_code: "GHS", product_name: "Group Hospital & Surgical",
      employee_participation: "compulsory", dependant_participation: "voluntary",
      baseline_tier_category_id: category, baseline_plan_code: plan, allow_plan_change: false,
      can_decline: false, eligible_only: false, tiers: [tier(category, plan)], dependant: null,
    },
  };
}

const FORM = {
  status: "open",
  company_name: "Synthetic Test Company Pte Ltd",
  window_name: "Annual enrolment (synthetic)",
  closes_at: "2035-12-31T15:59:00Z",
  opens_at: "2020-01-01T00:00:00Z",
  title: "Voluntary enrolment form",
  window_type: "open",
  policy_start: "2026-04-01",
  policy_end: "2027-03-31",
  intro_lines: [],
  helpline: null,
  submission_note: null,
  eligibility_notes: [],
  clauses: [
    { id: "accurate", text: "The information in this form is true and complete. I will tell my HR team or broker if any of it changes.", applies_to: "all" },
    { id: "dependant_consent", text: "I have the consent of each family member named in this form to share their personal data with the broker and insurer for this enrolment.", applies_to: "dependants" },
  ],
  documents: [],
  rules: [],
  products: [{
    product_code: "GHS", product_name: "Group Hospital & Surgical", insurer: "Synthetic Insurer",
    cohorts: [cohort("c-senior", "Senior managers", "4"), cohort("c-staff", "All other staff", "1")],
    age_limits: { spouse: { min: 18, max: 70 }, child: { min: 0, max: 25 } },
    gst_included: true,
  }],
  automatic: [],
  contributions: [{
    product_code: "GHS", employee_pct: null, dependant_pct: 50, upgrade_pct: null, gst_included: true,
    tiers: [
      { tier_key: "c-senior::4", mode: "tiered", premium: { EO: 300, ES: 1457.58, EC: 1457.58, EF: 2615.16 },
        premium_per_dependant: null, employee: null, family: { spouse: 578.79, child: 578.79, both: 1157.58 }, per_dependant: null },
      { tier_key: "c-staff::1", mode: "tiered", premium: { EO: 300, ES: 681.5, EC: 681.5, EF: 1064.26 },
        premium_per_dependant: null, employee: null, family: { spouse: 190.75, child: 190.75, both: 382.13 }, per_dependant: null },
    ],
  }],
  plans: [],
  allow_dependant_changes: true,
  max_children: 6,
};

async function mockEnrol(page: Page, form: unknown, onSubmit?: (body: Record<string, unknown>, route: Route) => Promise<void>) {
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/public/site")) return route.fulfill({ json: {} });
    const known = route.request().headers()["x-inspro-link"] === TOKEN;
    if (url.pathname.endsWith("/public/enrol") && route.request().method() === "GET" && known) {
      return route.fulfill({ json: form });
    }
    if (url.pathname.endsWith("/public/enrol/submissions") && onSubmit) {
      return onSubmit(route.request().postDataJSON() as Record<string, unknown>, route);
    }
    return route.fulfill({ status: 404, json: { detail: { code: "link_not_found", message: "This link is not valid. Ask for a new one." } } });
  });
}

test("employee completes the shared QR form without signing in", async ({ page }, testInfo) => {
  let sent: Record<string, unknown> | null = null;
  await mockEnrol(page, FORM, async (body, route) => {
    sent = body;
    await route.fulfill({ status: 201, json: {
      reference_no: "QR-2026-00042", submitted_at: "2026-10-12T08:00:00Z",
      copy_key: "synthetic-copy-key-0123456789", copy_expires_at: "2035-01-01T00:00:00Z",
    } });
  });
  await page.goto(`/enrol#${TOKEN}`);
  await expect(page.getByRole("heading", { name: "Voluntary enrolment form" })).toBeVisible();
  expect(page.url()).not.toContain("#");
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations).toEqual([]);

  await page.getByLabel("Full name").fill("Synthetic Person");
  await page.getByLabel(/NRIC \/ FIN/).fill("S8000001D");
  await page.getByLabel("Staff ID").fill("SYN-1");
  await page.getByRole("radio", { name: "Male", exact: true }).check();
  await page.getByLabel("Mobile number").fill("91234567");
  await page.getByLabel("Email").fill("synthetic@example.test");
  await page.getByRole("tab", { name: /Family/ }).click();
  await page.getByRole("button", { name: /Add spouse/ }).click();
  const spouse = page.locator("ul[aria-label='Family members on this form'] > li").first();
  await spouse.getByLabel("Full name").fill("Synthetic Spouse");
  await spouse.getByLabel(/NRIC \/ birth certificate/).fill("S8500005E");
  await spouse.getByLabel("Date of birth").fill("1985-05-05");
  await spouse.getByLabel("Sex").selectOption("F");
  await page.getByRole("tab", { name: /Benefit choices/ }).click();
  await page.getByRole("radio", { name: "Senior managers" }).check();
  await page.getByRole("radio", { name: /Enrol family or change plan/ }).check();
  await page.getByRole("checkbox", { name: /Synthetic Spouse/ }).check();
  await expect(page.getByText("S$578.79")).toBeVisible();
  await page.getByRole("tab", { name: /Declarations/ }).click();
  for (const box of await page.getByRole("checkbox").all()) await box.check();
  await page.getByRole("tab", { name: /Review and sign/ }).click();
  const submit = page.getByRole("button", { name: "Sign and submit" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("Your full name").fill("Synthetic Person");
  await expect(submit).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await submit.click();
  await expect(page.getByRole("heading", { name: "Form received" })).toBeVisible();
  await expect(page.getByText("QR-2026-00042")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("qr-receipt.png"), fullPage: true });

  const body = sent as unknown as {
    products: { category_id: string; tier_key: string; covered: string[] }[];
    dependants: { key: string; relationship: string }[];
    signature_name: string;
  };
  expect(body.products[0]).toMatchObject({ category_id: "c-senior", tier_key: "c-senior::4" });
  expect(body.products[0].covered).toEqual([body.dependants[0].key]);
  expect(body.signature_name).toBe("Synthetic Person");
});

test("a closed period and an unknown link explain what to do", async ({ page }) => {
  await mockEnrol(page, { ...FORM, status: "closed", products: [] });
  await page.goto(`/enrol#${TOKEN}`);
  await expect(page.getByRole("heading", { name: "Enrolment has closed" })).toBeVisible();
  await page.goto("/enrol#UnknownToken_0123456789abcdefghijklmnopq");
  await expect(page.getByRole("heading", { name: "This link is not valid" })).toBeVisible();
});

test("the form follows the portal language", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("inspro.portal.language", "zh-SG"));
  await mockEnrol(page, FORM);
  await page.goto(`/enrol#${TOKEN}`);
  await expect(page.getByRole("tab", { name: /您的资料/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: /核对并签署/ })).toBeVisible();
});

function hrState(state: string, extra: Record<string, unknown> = {}) {
  return {
    state, company_name: "Synthetic Test Company Pte Ltd", label: "Annual enrolment · 12 Oct 2026",
    file_count: 12, size_bytes: 2_400_000, server_time: new Date().toISOString(), expires_at: null,
    access_seconds: 600, attempts_left: 5, ...extra,
  };
}

test("HR enters the PIN, starts the ten-minute window and downloads", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/public/site")) return route.fulfill({ json: {} });
    if (path.endsWith("/public/hr-download") && route.request().method() === "GET") return route.fulfill({ json: hrState("ready") });
    if (path.endsWith("/public/hr-download/pin")) {
      attempts += 1;
      return attempts === 1
        ? route.fulfill({ status: 401, json: { detail: { code: "wrong_pin", message: "That PIN is not correct.", attempts_left: 4 } } })
        : route.fulfill({ json: hrState("verified") });
    }
    if (path.endsWith("/public/hr-download/start")) {
      return route.fulfill({ json: hrState("active", { expires_at: new Date(Date.now() + 600_000).toISOString() }) });
    }
    if (path.endsWith("/public/hr-download/file")) {
      return route.fulfill({ body: Buffer.from("PK\u0005\u0006" + "\u0000".repeat(18)), headers: {
        "content-type": "application/zip", "content-disposition": "attachment; filename=\"forms.zip\"",
      } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto(`/hr-download#${TOKEN}`);
  await expect(page.getByText("12 forms")).toBeVisible();
  await page.getByLabel(/PIN/).fill("123456");
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect(page.getByText("That PIN is not correct. 4 attempts left.")).toBeVisible();
  await page.getByLabel(/PIN/).fill("654321");
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect(page.getByText("PIN accepted.")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Start download/ }).click();
  expect((await download).suggestedFilename()).toBe("forms.zip");
  await expect(page.getByRole("timer")).toContainText(/0?9:5\d|10:00/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("an expired HR link says how to get a new one", async ({ page }) => {
  await page.route("**/api/v1/**", route => new URL(route.request().url()).pathname.endsWith("/public/hr-download")
    ? route.fulfill({ json: hrState("expired") })
    : route.fulfill({ json: {} }));
  await page.goto(`/hr-download#${TOKEN}`);
  await expect(page.getByRole("heading", { name: "This link has expired" })).toBeVisible();
  await expect(page.getByText("Ask your broker for a new link and PIN.")).toBeVisible();
});
