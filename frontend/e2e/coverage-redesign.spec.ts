import { expect, test, type Page } from "@playwright/test";

const MEMBER = {
  id: "coverage-member",
  email: "member@example.test",
  staff_id: "EMP-001",
  display_name: "Alex Tan",
};

function item(name: string, value: string, note?: string, properties: Record<string, string> = {}) {
  return { number: "1", name, value, note: note ?? null, kind: "currency", sub_items: [], properties };
}

const CARE_ROUTE: Record<string, string> = { GCGP: "gp", GCSP: "specialist", GHS: "hospital", GMM: "hospital" };

function line(code: string, name: string, items: ReturnType<typeof item>[], family = false, cap: string | null = null) {
  return {
    product_code: code,
    product_name: name,
    care_route: CARE_ROUTE[code] ?? null,
    category_id: null,
    category_display: null,
    match_method: null,
    match_confidence: null,
    rule_human_readable: null,
    plan_code: "A",
    cover_description: null,
    annual_policy_limit: cap,
    benefit_schedule: { items },
    financials: null,
    covers_dependants: family,
    covered_dependants: family
      ? [{ id: "child-1", name: "Jamie Tan", relationship: "Child", dob: null, role: "child" }]
      : [],
  };
}

async function mockMember(page: Page, descriptionsOnly = false) {
  await page.route("**/api/v1/portal/auth/refresh", route => route.fulfill({ json: {
    token: "e2e-member-token", expires_at: "2100-01-01T00:00:00Z", member: MEMBER,
  } }));

  await page.route(/\/api\/v1\/portal\/me$/, (route) => route.fulfill({ json: {
    member: MEMBER,
    access: { state: "active", capabilities: ["record", "entitlement", "claim"], last_day: null, access_ends_on: null },
    company: { slug: "demo", name: "Demo", legal_name: "Demo Company" },
    employee: { id: "employee-1", staff_id: "EMP-001", employee_name: "Alex Tan" },
    policy_year: { id: "year-1", year: 2026, start_date: "2026-01-01", end_date: "2026-12-31" },
    flex_eligible: false,
    enrollment_open: false,
  } }));
  await page.route(/\/api\/v1\/portal\/auth\/security-status$/, (route) => route.fulfill({ json: { mfa_status: "none", mfa_available: false } }));
  await page.route(/\/api\/v1\/portal\/conversations/, (route) => route.fulfill({ json: { total: 0, offset: 0, limit: 20, unread_total: 0, items: [] } }));
  await page.route(/\/api\/v1\/portal\/benefit-statement$/, (route) => route.fulfill({ json: {
    employee: { id: "employee-1", staff_id: "EMP-001", employee_name: "Alex Tan" },
    policy_year_id: "year-1", is_matched: true, attributes: [], dependants: [], flex: null,
    coverage: descriptionsOnly ? [
      { ...line("GCGP", "Group GP", []), care_route: "gp" },
      { ...line("GCSP", "Group Specialist", []), care_route: "specialist" },
      { ...line("GHS", "Group Hospital & Surgical", []), care_route: "hospital" },
      { ...line("GDT", "Group Dental", []), care_route: "dental" },
    ] : [
      line("GCGP", "Group GP", [
        item("Panel consultation", "As charged"),
        item("Non-panel visit", "80", "per visit", { maximum_visits: "6" }),
        item("Annual health screening", "150"),
      ]),
      line("GCSP", "Group Specialist", [item("Referral from GP", "Required"), item("Specialist consultation", "120", "per visit")]),
      line("GHS", "Group Hospital & Surgical", [item("Daily Room & Board", "250", "per day"), item("Intensive Care Unit", "500", "per day"), item("In-patient Expenses", "As charged")], true, "50000"),
      line("GMM", "Group Major Medical", [item("Inpatient Benefits", "10000")]),
      line("GTL", "Group Term Life", [item("Death benefit", "100000")]),
    ],
  } }));
}

test("care routes show the right facts and hide GTL", async ({ page }, testInfo) => {
  await mockMember(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/portal/demo/coverage?tab=benefits");
  await expect(page.getByRole("tab", { name: "What's covered" })).toBeVisible();
  await expect(page.getByRole("button", { name: /See a GP/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /See a specialist/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Hospital & surgery/ })).toBeVisible();
  await expect(page.getByText("Group Term Life")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("coverage-index.png"), fullPage: true });

  await page.getByRole("button", { name: /Hospital & surgery/ }).click();
  await expect(page.getByText("Room & board", { exact: true })).toBeVisible();
  await expect(page.getByText("Yearly limit", { exact: true })).toBeVisible();
  await expect(page.locator("dd").filter({ hasText: "S$50,000" })).toBeVisible();
  await expect(page.locator("dl").getByText("S$250", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Extra cover after your hospital plan")).toBeVisible();
  await expect(page.getByText("Daily Room & Board", { exact: true })).toBeHidden();
  await page.getByText("Full benefit schedule").first().click();
  await expect(page.getByText("Daily Room & Board", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("coverage-hospital.png"), fullPage: true });

  // Back from a care detail returns to the care list, not out of coverage.
  await page.goBack();
  await expect(page.getByRole("button", { name: /See a GP/ })).toBeVisible();

  // The chosen family member survives a refresh along with the open route.
  await page.getByRole("button", { name: "Jamie Tan" }).click();
  await expect(page.getByRole("button", { name: /Hospital & surgery/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /See a GP/ })).toHaveCount(0);
  await page.getByRole("button", { name: /Hospital & surgery/ }).click();
  await page.reload();
  await expect(page.getByText("Covered person:")).toContainText("Jamie Tan");
  await page.getByRole("button", { name: "Me", exact: true }).click();
  await page.getByRole("button", { name: /See a GP/ }).click();
  await expect(page.getByText("Panel clinic", { exact: true })).toBeVisible();
  await expect(page.getByText("Non-panel clinic", { exact: true })).toBeVisible();
  // A visit count stored as a property is not money, and a screening is not a cap.
  await expect(page.getByText("Yearly limit", { exact: true })).toHaveCount(0);
  await expect(page.getByText("S$6", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Find a clinic" })).toBeVisible();
  await page.getByRole("button", { name: "All care options" }).click();
  await page.getByRole("button", { name: /See a specialist/ }).click();
  await expect(page.getByText("Referral", { exact: true })).toBeVisible();
  await expect(page.locator("dl").getByText("Required", { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
  const overflowing = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflowing).toBe(false);
});

test("coverage uses the report label and falls back to the plan name", async ({ page }, testInfo) => {
  await mockMember(page);
  let label: string | null = "4 Bed Restr Hosp / Inpatient Expenses - S$10,000";
  await page.route(/\/api\/v1\/portal\/benefit-statement$/, route => route.fulfill({ json: {
    employee: { id: "employee-1", staff_id: "EMP-001", employee_name: "Alex Tan" },
    policy_year_id: "year-1", is_matched: true, attributes: [], dependants: [], flex: null,
    coverage: [{ ...line("GHS", "Group Hospital & Surgical", [item("Daily Room & Board", "250")]),
      plan_code: "1", plan_display_name: label }],
  } }));
  await page.goto("/portal/demo/coverage?tab=benefits&p=hospital");
  await expect(page.getByText(label, { exact: true })).toBeVisible();
  await expect(page.getByText("Plan 1", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("report-label.png"), fullPage: true });
  label = "Plan 1";
  await page.reload();
  await expect(page.getByText("Plan 1", { exact: true })).toBeVisible();
  label = null;
  await page.reload();
  await expect(page.getByText("Plan 1", { exact: true })).toBeVisible();
});

test("coverage card rows align when titles and descriptions wrap", async ({ page }, testInfo) => {
  await mockMember(page, true);
  await page.goto("/portal/demo/coverage?tab=benefits");
  const cards = page.locator(".portal-coverage .clay-sheets:not(.clay-sheets-other) > .clay-sheet");
  await expect(cards).toHaveCount(4);
  await page.evaluate(() => document.fonts.ready);

  const widths = testInfo.project.name === "desktop-chromium" ? [1440, 940, 768] : [393, 320];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 960 });
    const rows = await cards.evaluateAll((elements) => elements.map((card) => {
      const box = card.getBoundingClientRect();
      const positions = [".clay-sheet-title", ".clay-sheet-value", ".clay-sheet-go"].map((selector) => {
        const element = card.querySelector(selector)!;
        const rect = element.getBoundingClientRect();
        return { top: rect.top, right: rect.right, bottom: rect.bottom };
      });
      const art = card.querySelector("img")!.getBoundingClientRect();
      return { top: box.top, bottom: box.bottom, right: box.right, positions, artBottom: art.bottom };
    }));
    for (const card of rows) {
      // Body copy starts below the illustration, including single-line phone titles.
      expect(card.positions[1].top).toBeGreaterThanOrEqual(card.artBottom);
      for (const position of card.positions) {
        expect(position.right).toBeLessThanOrEqual(card.right);
        expect(position.bottom).toBeLessThanOrEqual(card.bottom);
      }
      for (const peer of rows.filter((row) => Math.abs(row.top - card.top) < 1)) {
        for (let index = 0; index < card.positions.length; index++) {
          expect(Math.abs(card.positions[index].top - peer.positions[index].top)).toBeLessThan(1);
        }
      }
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`coverage-aligned-${width}.png`), fullPage: true });
  }
});
