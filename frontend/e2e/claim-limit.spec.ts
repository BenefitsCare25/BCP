import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from "@playwright/test";

const MEMBER = {
  id: "member-limit-test",
  email: "member@example.test",
  staff_id: "EMP-001",
  display_name: "Test Member",
};

async function apiJson<T>(response: Awaited<ReturnType<APIRequestContext["get"]>>) {
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as T;
}

async function brokerContext(request: APIRequestContext) {
  const me = await apiJson<{ accessible_clients: Array<{ id: string }> }>(
    await request.get("/api/v1/me"),
  );
  const clientId = me.accessible_clients[0]?.id;
  expect(clientId).toBeTruthy();
  const headers = { "X-Inspro-Client": clientId };
  const years = await apiJson<Array<{ id: string; start_date: string; end_date: string }>>(
    await request.get("/api/v1/policy-years", { headers }),
  );
  const today = "2026-08-30";
  const year = years.find(
    (candidate) => candidate.start_date <= today && candidate.end_date >= today,
  );
  expect(year).toBeDefined();
  return { clientId, policyYearId: year!.id, headers };
}

async function installBrokerSession(page: Page, clientId: string, policyYearId: string) {
  await page.addInitScript(
    ({ client, year }) => {
      localStorage.setItem(
        "inspro-session",
        JSON.stringify({
          state: {
            activeClientId: client,
            currentPolicyYearId: year,
            policyYearClientId: client,
          },
          version: 0,
        }),
      );
    },
    { client: clientId, year: policyYearId },
  );
}

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

async function mockClaimForm(page: Page) {
  await page.addInitScript((member) => {
    localStorage.setItem(
      "inspro-portal-session",
      JSON.stringify({
        state: {
          token: "e2e-member-token",
          expiresAt: "2100-01-01T00:00:00Z",
          member,
        },
        version: 0,
      }),
    );
  }, MEMBER);

  await page.route(/\/api\/v1\/portal\/me$/, (route) =>
    route.fulfill({
      json: {
        member: MEMBER,
        access: {
          state: "active",
          capabilities: ["record", "claim"],
          last_day: null,
          access_ends_on: null,
        },
        company: { slug: "demo", name: "Demo", legal_name: "Demo Company" },
        employee: { id: "employee-limit-test", staff_id: "EMP-001", employee_name: "Test Member" },
        policy_year: {
          id: "policy-limit-test",
          year: 2026,
          start_date: "2026-01-01",
          end_date: "2026-12-31",
        },
        flex_eligible: false,
        enrollment_open: false,
      },
    }),
  );
  await page.route(/\/api\/v1\/portal\/auth\/security-status$/, (route) =>
    route.fulfill({ json: { mfa_status: "none", mfa_available: false } }),
  );
  await page.route(/\/api\/v1\/portal\/conversations/, (route) =>
    route.fulfill({
      json: { total: 0, offset: 0, limit: 20, unread_total: 0, items: [] },
    }),
  );
  await page.route(/\/api\/v1\/portal\/benefit-statement$/, (route) =>
    route.fulfill({
      json: {
        employee: {
          id: "employee-limit-test",
          staff_id: "EMP-001",
          employee_name: "Test Member",
        },
        policy_year_id: "policy-limit-test",
        is_matched: true,
        attributes: [],
        coverage: [],
        dependants: [],
        flex: null,
      },
    }),
  );
  await page.route(/\/api\/v1\/portal\/claims(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { total: 0, offset: 0, limit: 20, items: [] },
    }),
  );
  await page.route(/\/api\/v1\/portal\/claims\/form\/draft$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: null });
      return;
    }
    const body = route.request().postDataJSON() as {
      form_data: Record<string, string>;
      expected_version: number | null;
    };
    await route.fulfill({
      json: {
        id: "form-draft-limit-test",
        form_data: body.form_data,
        version: (body.expected_version ?? 0) + 1,
        updated_at: "2026-08-30T00:00:00Z",
      },
    });
  });
  await page.route(/\/api\/v1\/portal\/coverage-options$/, (route) =>
    route.fulfill({
      json: {
        policy_year_start: "2026-01-01",
        policy_year_end: "2026-12-31",
        claimable_from: "2026-01-01",
        claimable_to: "2026-12-31",
        insured: [
          {
            product_code: "GCGP",
            product_name: "Group Clinical GP",
            plan_code: "P1",
            annual_policy_limit: null,
            covers_dependants: false,
            covered_dependant_ids: [],
            insurer: "Example Insurer",
            insurer_member_id: "INS-001",
            sub_types: ["TCM (Traditional Chinese Medicine)", "Physiotherapy"],
            requires_referral: false,
            diagnosis_group: "gp",
            diagnosis_required: true,
            category: "outpatient",
            claim_types: [
              {
                label: "GP (General Practitioner)",
                sub_type: null,
                scope_code: "standard",
                scope_key: "insured:gcgp:standard",
                benefit_key: null,
                requires_doctor_name: false,
                supports_stay_dates: false,
                anchor_mode: null,
                doc_slots: [],
                doc_slots_by_sector: null,
              },
              {
                label: "Physiotherapy",
                sub_type: "Physiotherapy",
                scope_code: "gp_physiotherapy",
                scope_key: "insured:gcgp:gp_physiotherapy",
                benefit_key: "Physiotherapy",
                requires_doctor_name: false,
                supports_stay_dates: false,
                anchor_mode: null,
                doc_slots: [],
                doc_slots_by_sector: null,
              },
              {
                label: "TCM (Traditional Chinese Medicine)",
                sub_type: "TCM (Traditional Chinese Medicine)",
                scope_code: "gp_tcm",
                scope_key: "insured:gcgp:gp_tcm",
                benefit_key: "TCM & Chiropractor",
                requires_doctor_name: false,
                supports_stay_dates: false,
                anchor_mode: null,
                doc_slots: [],
                doc_slots_by_sector: null,
              },
            ],
          },
        ],
        flex: null,
        claim_block: null,
        dependants: [],
        currencies: ["SGD"],
        policy_currency: "SGD",
        hospitals: [],
      },
    }),
  );
  await page.route(/\/api\/v1\/portal\/utilization$/, (route) =>
    route.fulfill({
      json: {
        policy_year_id: "policy-limit-test",
        insured: [
          {
            product_code: "GCGP",
            product_name: "Group Clinical GP",
            benefit_key: null,
            limit: null,
            limit_display: "S$9,999",
            limit_basis: "policy_year",
            limit_status: "needs_review",
            limit_is_enforceable: false,
            approved: 100,
            pending: 50,
            pending_unconverted: 0,
            remaining: null,
            claim_count: 2,
            pending_claim_ids: ["pending-gp"],
            orphaned: false,
            limit_unparsed: false,
          },
          {
            product_code: "GCGP",
            product_name: "Group Clinical GP",
            benefit_key: "Physiotherapy",
            limit: null,
            limit_display: "S$80 per visit",
            approved: 0,
            pending: 0,
            pending_unconverted: 0,
            remaining: null,
            claim_count: 0,
            pending_claim_ids: [],
            orphaned: false,
            limit_unparsed: false,
            limit_basis: "per_visit",
            limit_status: "verified",
            limit_is_enforceable: false,
            claim_scope_codes: ["gp_physiotherapy"],
          },
          {
            product_code: "GCGP",
            product_name: "Group Clinical GP",
            benefit_key: "TCM & Chiropractor",
            limit: 300,
            limit_display: "S$300 per policy year",
            approved: 100,
            pending: 50,
            pending_unconverted: 0,
            remaining: 200,
            claim_count: 2,
            pending_claim_ids: ["pending-tcm"],
            orphaned: false,
            limit_unparsed: false,
            limit_basis: "policy_year",
            limit_status: "verified",
            limit_is_enforceable: true,
            claim_scope_codes: ["gp_tcm"],
          },
        ],
        flex: null,
      },
    }),
  );
  await page.route(/\/api\/v1\/portal\/claim-diagnoses/, (route) =>
    route.fulfill({ json: { group: "gp", items: [] } }),
  );
}

test("claim form shows the selected plan balance and warns without blocking a full receipt", async ({
  page,
}, testInfo: TestInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await mockClaimForm(page);
  await page.goto("/portal/demo/claims/new");

  await page
    .getByRole("combobox", { name: "Claim type" })
    .selectOption({ label: "TCM (Traditional Chinese Medicine)" });

  const limits = page.getByRole("region", { name: "Limit for this claim" });
  await expect(limits).toContainText("TCM & Chiropractor");
  await expect(limits).toContainText("S$150 available after pending");
  await expect(limits).toContainText("of S$300");
  await expect(limits).toContainText("S$200 confirmed balance");
  await expect(limits).toContainText("S$50 submitted and not settled yet");
  await expect(limits).not.toContainText("Overall plan");
  await expect(limits).not.toContainText("S$9,999");

  await page.getByRole("spinbutton", { name: "Incurred amount" }).fill("250");
  const amountWarning = page.getByText(
    /Your receipt is above the amount currently available after submitted claims/,
  );
  await expect(amountWarning).toBeVisible();
  await expect(amountWarning).toContainText("S$150");
  await expect(amountWarning).toContainText("Submit the full receipt");
  await expect(page.getByRole("button", { name: "Submit claim" })).toBeEnabled();

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  expect(runtimeErrors).toEqual([]);

  await page.screenshot({
    path: testInfo.outputPath(`claim-limit-${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });
});

test("per-visit wording is informative and never becomes an annual balance", async ({
  page,
}) => {
  const runtimeErrors = monitorRuntime(page);
  await mockClaimForm(page);
  await page.goto("/portal/demo/claims/new");

  await page
    .getByRole("combobox", { name: "Claim type" })
    .selectOption({ label: "Physiotherapy" });

  const limits = page.getByRole("region", { name: "Limit for this claim" });
  await expect(limits).toContainText("Physiotherapy");
  await expect(limits).toContainText("S$80 per visit");
  await expect(limits).toContainText("Per visit condition; this is policy wording");
  await expect(limits).not.toContainText("left of");

  await page.getByRole("spinbutton", { name: "Incurred amount" }).fill("500");
  await expect(page.getByText(/Your receipt is above the current balance/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit claim" })).toBeEnabled();

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  expect(runtimeErrors).toEqual([]);
});

test("what's left shows only verified annual balances", async ({
  page,
}, testInfo: TestInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await mockClaimForm(page);
  await page.goto("/portal/demo/coverage?tab=usage");

  const main = page.getByRole("main");
  await expect(main).toContainText("Group Clinical GP");
  await expect(main).toContainText("TCM & Chiropractor");
  await expect(main).toContainText("S$150");
  await expect(main).toContainText("available after pending");
  await expect(main).toContainText("S$200 confirmed balance");
  await expect(main).not.toContainText("S$9,999");
  await expect(main).not.toContainText("Physiotherapy");
  await expect(main).not.toContainText("As charged");

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  expect(runtimeErrors).toEqual([]);

  await page.screenshot({
    path: testInfo.outputPath(`whats-left-${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });
});

test("broker can review plan and line mappings from the SoB editor", async ({
  page,
  request,
}, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  const context = await brokerContext(request);
  const productCode =
    testInfo.project.name === "mobile-chromium" ? "GP" : "GHS2";
  const draft = await request.put(
    `/api/v1/policy-years/${context.policyYearId}/product-setups/${productCode}`,
    {
      headers: context.headers,
      data: { answers: {}, template_version: 1 },
    },
  );
  expect(draft.ok(), await draft.text()).toBeTruthy();
  await installBrokerSession(page, context.clientId, context.policyYearId);
  await page.goto("/client-relations/company-benefits");

  const productTab = page.getByRole("tab", {
    name: new RegExp(`^${productCode}$`),
  }).first();
  await expect(productTab).toBeVisible();
  await productTab.click();
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByRole("button", { name: /^SOB(?:\s|$)/ }).click();

  const editor = page.getByRole("region", { name: "Claim limit settings" });
  await expect(editor).toBeVisible();
  await expect(editor).toContainText("Claim type coverage");
  await expect(editor).toContainText(
    productCode === "GHS2"
      ? "Hospitalisation/Day Surgery/Other Inpatient Treatment"
      : "Physiotherapy",
  );

  const overallButton = editor.getByRole("button", { name: /Set limit|Edit/ }).first();
  await overallButton.click();
  const amount = editor.getByRole("spinbutton", { name: "Annual amount (SGD)" }).first();
  await amount.fill("2500");
  await editor.getByRole("button", { name: "Verify setting" }).first().click();
  await expect(editor).toContainText("SGD 2,500");
  await expect(editor).toContainText("Verified");

  const addLine = editor.getByRole("combobox", { name: "Add a benefit line limit" });
  if (await addLine.isVisible()) {
    await addLine.click();
    const options = page.getByRole("option");
    await expect.poll(() => options.count()).toBeGreaterThan(0);
    const firstLabel = (await options.first().textContent())?.trim() ?? "";
    await options.first().click();
    await editor.getByRole("button", { name: "Add setting" }).click();
    await editor.getByRole("checkbox").first().click();
    const lineBasis = editor.getByRole("combobox", { name: "Limit basis" }).first();
    await lineBasis.click();
    await page.getByRole("option", { name: "Per policy year" }).click();
    const lineAmount = editor.getByRole("spinbutton", { name: "Annual amount (SGD)" }).first();
    await lineAmount.fill("750");
    await editor.getByRole("button", { name: "Verify setting" }).first().click();
    await expect(editor).toContainText(firstLabel);
  }

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  expect(runtimeErrors).toEqual([]);

  await page.screenshot({
    path: testInfo.outputPath(`broker-claim-limits-${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });
});
