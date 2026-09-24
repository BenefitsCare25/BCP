import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from "@playwright/test";

interface PolicyYear {
  id: string;
  year: number;
  start_date: string;
  end_date: string;
  coverage_start: string;
  coverage_end: string;
  claim_grace_period_days: number | null;
}

interface Me {
  accessible_clients: Array<{ id: string; name: string }>;
}

interface PanelListing {
  id: string;
  insurer: string;
  panel_provider: string;
  country: string;
  clinic_type: string;
}

const API = "/api/v1";

function runtimeMonitor(page: Page) {
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
    path: testInfo.outputPath(`${name}-${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });
}

async function apiJson<T>(response: Awaited<ReturnType<APIRequestContext["get"]>>) {
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as T;
}

async function ensureYear(
  request: APIRequestContext,
  headers: Record<string, string>,
  startDate: string,
  endDate: string,
  graceDays: number,
) {
  let years = await apiJson<PolicyYear[]>(
    await request.get(`${API}/policy-years`, { headers }),
  );
  let year = years.find(
    (candidate) =>
      candidate.start_date === startDate && candidate.end_date === endDate,
  );
  if (!year) {
    const created = await request.post(`${API}/policy-years`, {
      headers,
      data: { start_date: startDate, end_date: endDate },
    });
    if (created.ok()) {
      year = (await created.json()) as PolicyYear;
    } else {
      // Desktop and mobile projects may start together. If the other project
      // won the create race, re-read the already-created period.
      expect(created.status()).toBe(409);
      years = await apiJson<PolicyYear[]>(
        await request.get(`${API}/policy-years`, { headers }),
      );
      year = years.find(
        (candidate) =>
          candidate.start_date === startDate && candidate.end_date === endDate,
      );
    }
  }
  expect(year).toBeDefined();
  const updated = await request.patch(`${API}/policy-years/${year!.id}`, {
    headers,
    data: { claim_grace_period_days: graceDays },
  });
  return apiJson<PolicyYear>(updated);
}

async function context(request: APIRequestContext, futureYear: number) {
  const me = await apiJson<Me>(await request.get(`${API}/me`));
  const now = new Date();
  const today = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
  let client: Me["accessible_clients"][number] | undefined;
  for (const candidate of me.accessible_clients) {
    const candidateHeaders = { "X-Inspro-Client": candidate.id };
    const candidateYears = await apiJson<PolicyYear[]>(
      await request.get(`${API}/policy-years`, { headers: candidateHeaders }),
    );
    if (candidateYears.some((year) => year.start_date <= today && year.end_date >= today)) {
      client = candidate;
      break;
    }
  }
  expect(client).toBeDefined();
  const headers = { "X-Inspro-Client": client!.id };
  const current = (await apiJson<PolicyYear[]>(
    await request.get(`${API}/policy-years`, { headers }),
  )).find(
    (year) => year.start_date <= today && year.end_date >= today,
  );
  expect(current).toBeDefined();
  const patchedCurrent = await apiJson<PolicyYear>(
    await request.patch(`${API}/policy-years/${current!.id}`, {
      headers,
      data: { claim_grace_period_days: 30 },
    }),
  );
  const past = await ensureYear(
    request,
    headers,
    "2025-01-01",
    "2025-12-31",
    15,
  );
  const future = await ensureYear(
    request,
    headers,
    `${futureYear}-01-01`,
    `${futureYear}-12-31`,
    45,
  );
  return { client: client!, headers, current: patchedCurrent, past, future };
}

async function installSession(page: Page, clientId: string) {
  await page.addInitScript((id) => {
    if (localStorage.getItem("inspro-session")) return;
    localStorage.setItem(
      "inspro-session",
      JSON.stringify({
        state: {
          activeClientId: id,
          currentPolicyYearId: null,
          policyYearClientId: null,
        },
        version: 0,
      }),
    );
  }, clientId);
}

async function selectYear(page: Page, startYear: number) {
  const select = page.getByRole("combobox", { name: "Select benefit year" });
  await select.click();
  await page
    .getByRole("option", { name: new RegExp(`\\b${startYear}\\b`) })
    .click();
  await expect(select).toContainText(String(startYear));
}

async function expectYearRequest(
  page: Page,
  requests: string[],
  path: string,
  policyYearId: string,
) {
  requests.length = 0;
  await page.goto(path);
  await expect(
    page.getByRole("combobox", { name: "Select benefit year" }),
  ).toBeVisible();
  await expect
    .poll(() => requests.some((url) => url.includes(policyYearId)), {
      message: `${path} did not request data for ${policyYearId}`,
      timeout: 15_000,
    })
    .toBeTruthy();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test("benefit-year selection defaults to today and follows every module", async ({
  page,
  request,
}, testInfo) => {
  // This scenario intentionally traverses every benefit-year-aware module and
  // runs two accessibility scans. Keep a bounded allowance for parallel CI
  // workers without weakening any of its assertions.
  test.setTimeout(45_000);

  const years = await context(
    request,
    testInfo.project.name === "mobile-chromium" ? 2028 : 2027,
  );
  const runtimeErrors = runtimeMonitor(page);
  const apiRequests: string[] = [];
  page.on("request", (pending) => {
    if (pending.url().includes("/api/v1/")) apiRequests.push(pending.url());
  });
  await installSession(page, years.client.id);

  await page.goto("/dashboard");
  const yearSelect = page.getByRole("combobox", { name: "Select benefit year" });
  await expect(yearSelect).toContainText("2026");
  await expect(yearSelect).not.toContainText("Today");
  if (testInfo.project.name === "desktop-chromium") {
    const contextBar = page.locator('[data-context-bar="company"]');
    const companyName = contextBar.getByText(years.client.name, { exact: true });
    const [companyBox, yearBox] = await Promise.all([
      companyName.boundingBox(),
      yearSelect.boundingBox(),
    ]);
    expect(companyBox).not.toBeNull();
    expect(yearBox).not.toBeNull();
    expect(yearBox!.y).toBeLessThan(companyBox!.y + companyBox!.height);
    expect(companyBox!.y).toBeLessThan(yearBox!.y + yearBox!.height);
  }
  await yearSelect.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(yearSelect).toBeFocused();

  await selectYear(page, 2025);
  const persisted = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("inspro-session") ?? "{}"),
  );
  expect(persisted.state.currentPolicyYearId).toBe(years.past.id);
  expect(persisted.state.policyYearClientId).toBe(years.client.id);

  await page.reload();
  await expect(yearSelect).toContainText("2025");

  const paths = [
    "/client-relations/company-benefits",
    "/client-relations/enrollment",
    "/policy-admin/member-listing",
    "/policy-admin/member-coverage",
    "/policy-admin/panel-clinics",
    "/policy-admin/underwriting",
    "/claims/review?tab=queue",
    "/claims/reports?tab=pa",
  ];
  for (const path of paths) {
    await expectYearRequest(page, apiRequests, path, years.past.id);
    await expect(yearSelect).toContainText("2025");
    if (path === "/claims/review?tab=queue") {
      await expect(page.getByLabel("Benefit year scope")).toHaveCount(0);
      await expect(page.getByLabel("Incurred from")).toHaveCount(0);
      await expect(page.getByLabel("Incurred to")).toHaveCount(0);
      const claimsRequest = apiRequests
        .map((url) => new URL(url))
        .find((url) => url.pathname === "/api/v1/claims");
      expect(claimsRequest?.searchParams.get("policy_year_id")).toBe(years.past.id);
      expect(claimsRequest?.searchParams.get("all_years")).toBeNull();
    }
  }

  await expect(
    page.getByText("No products are configured for this benefit year"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Download" }).first()).toBeVisible();
  await screenshot(page, testInfo, "historical-reports");
  await assertAccessible(page);

  await page.goto("/client-relations/company-benefits");
  await expect(page.locator("main")).toBeVisible();
  const verticalScroll = await page.evaluate(() => {
    const main = document.querySelector("main");
    if (!main) throw new Error("App shell main element is missing");
    return {
      htmlRange:
        document.documentElement.scrollHeight -
        document.documentElement.clientHeight,
      bodyRange: document.body.scrollHeight - document.body.clientHeight,
      htmlOverflow: getComputedStyle(document.documentElement).overflowY,
      bodyOverflow: getComputedStyle(document.body).overflowY,
    };
  });
  expect(verticalScroll.htmlRange).toBeLessThanOrEqual(1);
  expect(verticalScroll.bodyRange).toBeLessThanOrEqual(1);
  expect(verticalScroll.htmlOverflow).toBe("hidden");
  expect(verticalScroll.bodyOverflow).toBe("hidden");
  const benefitHeading = page.getByRole("heading", { name: "Benefit years" });
  await expect(benefitHeading).toBeVisible();
  await expect(page.getByRole("button", { name: "Add benefit year" })).toBeVisible();
  const invalidEndDate = page.getByTestId(
    `benefit-year-${years.past.id}-end_date`,
  );
  await invalidEndDate.fill(years.current.start_date);
  await invalidEndDate.blur();
  await expect(
    page.getByText(new RegExp(`Overlaps .*${years.current.start_date}`)),
  ).toBeVisible();
  await expect(invalidEndDate).toHaveAttribute("aria-invalid", "true");
  const tabs = page.getByRole("tablist").first();
  const [tabsBox, benefitBox] = await Promise.all([
    tabs.boundingBox(),
    benefitHeading.boundingBox(),
  ]);
  expect(tabsBox).not.toBeNull();
  expect(benefitBox).not.toBeNull();
  expect(benefitBox!.y).toBeGreaterThan(tabsBox!.y + tabsBox!.height);
  const recentHeading = page.getByRole("heading", { name: "Recent changes" });
  if (await recentHeading.isVisible()) {
    const recentBox = await recentHeading.boundingBox();
    expect(recentBox!.y).toBeGreaterThan(benefitBox!.y);
  }
  await screenshot(page, testInfo, "historical-company-benefits");
  await assertAccessible(page);

  expect(runtimeErrors).toEqual([]);
});

test("year-specific deadlines, products, and panel networks stay isolated", async ({
  page,
  request,
}, testInfo) => {
  const years = await context(
    request,
    testInfo.project.name === "mobile-chromium" ? 2028 : 2027,
  );
  const runtimeErrors = runtimeMonitor(page);
  await installSession(page, years.client.id);
  await page.goto("/claims/review?tab=settings");

  await selectYear(page, 2025);
  const graceSetting = page.getByRole("group", {
    name: "Claim submission grace period (days)",
  });
  await expect(graceSetting.getByText("15", { exact: true })).toBeVisible();
  await selectYear(page, 2026);
  await expect(graceSetting.getByText("30", { exact: true })).toBeVisible();
  await selectYear(page, years.future.year);
  await expect(graceSetting.getByText("45", { exact: true })).toBeVisible();
  await graceSetting.getByRole("button", { name: "Edit" }).click();
  const grace = graceSetting.getByRole("spinbutton", {
    name: "Claim submission grace period (days)",
  });
  await expect(grace).toHaveValue("45");
  await grace.fill("46");
  await graceSetting.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Claim grace period updated")).toBeVisible();
  await selectYear(page, 2026);
  await selectYear(page, years.future.year);
  await expect(graceSetting.getByText("46", { exact: true })).toBeVisible();

  const draft =
    testInfo.project.name === "mobile-chromium"
      ? { code: "GTL", line: "Life Insurance" }
      : { code: "GHS", line: "Medical Insurance" };
  const setupPath = `${API}/policy-years/${years.current.id}/product-setups/${draft.code}`;
  const existingSetup = await request.get(setupPath, { headers: years.headers });
  expect([200, 404]).toContain(existingSetup.status());
  const savedSetup = existingSetup.ok()
    ? ((await existingSetup.json()) as {
        template_version: number;
        updated_at: string;
      })
    : null;
  const draftResponse = await request.put(
    setupPath,
    {
      headers: years.headers,
      data: {
        answers: {},
        template_version: savedSetup?.template_version ?? 1,
        expected_updated_at: savedSetup?.updated_at,
      },
    },
  );
  expect(draftResponse.ok(), await draftResponse.text()).toBeTruthy();
  const listingInput = {
    insurer: `E2E-${testInfo.project.name}`,
    panel_provider: "Release gate",
    country: "SG",
    clinic_type: "gp",
  };
  const findListing = (listings: PanelListing[]) =>
    listings.find(
      (candidate) =>
        candidate.insurer === listingInput.insurer &&
        candidate.panel_provider === listingInput.panel_provider &&
        candidate.country === listingInput.country &&
        candidate.clinic_type === listingInput.clinic_type,
    );
  let listings = await apiJson<PanelListing[]>(
    await request.get(`${API}/panel-listings`, { headers: years.headers }),
  );
  let listing = findListing(listings);
  if (!listing) {
    const listingResponse = await request.post(`${API}/panel-listings`, {
      headers: years.headers,
      data: listingInput,
    });
    if (listingResponse.ok()) {
      listing = (await listingResponse.json()) as PanelListing;
    } else {
      // A concurrent project/retry may have created the shared entry after
      // our initial read. Re-read it instead of failing on the expected 409.
      expect(listingResponse.status()).toBe(409);
      listings = await apiJson<PanelListing[]>(
        await request.get(`${API}/panel-listings`, { headers: years.headers }),
      );
      listing = findListing(listings);
    }
  }
  expect(listing).toBeDefined();
  const panelsResponse = await request.put(
    `${API}/policy-years/${years.current.id}/panels`,
    {
      headers: years.headers,
      data: { panel_listing_ids: [listing!.id] },
    },
  );
  expect(panelsResponse.ok(), await panelsResponse.text()).toBeTruthy();

  // Assert on the draft product itself, not on the line's badge total: the
  // badge also counts company-level catalog products, which span every year by
  // design, and a spec running in parallel (rule-builder) adds one to this
  // same company while it runs.
  const lineTab = page.getByRole("tab", { name: new RegExp(`^${draft.line}`) });
  const draftProductTab = page.getByRole("tab", { name: draft.code, exact: true });
  await selectYear(page, 2025);
  await page.goto("/client-relations/company-benefits");
  await expect(page.getByRole("combobox", { name: "Select benefit year" })).toContainText("2025");
  await lineTab.click();
  await expect(draftProductTab).toHaveCount(0);

  await selectYear(page, 2026);
  await lineTab.click();
  await expect(draftProductTab).toBeVisible();
  await expect(lineTab).toContainText(/\d/);

  await page.goto("/policy-admin/panel-clinics");
  const switches = page.getByRole("switch");
  await expect.poll(() => switches.count()).toBeGreaterThan(0);
  // The switches render from the listing catalog before the year's selection
  // arrives, so the checked count has to be polled like the 2025 one below.
  await expect
    .poll(async () =>
      switches.evaluateAll(
        (items) =>
          items.filter((item) => (item as HTMLButtonElement).dataset.state === "checked")
            .length,
      ),
    )
    .toBeGreaterThan(0);

  await selectYear(page, 2025);
  await expect
    .poll(async () =>
      switches.evaluateAll(
        (items) =>
          items.filter(
            (item) => (item as HTMLButtonElement).dataset.state === "checked",
          ).length,
      ),
    )
    .toBe(0);
  await switches.first().scrollIntoViewIfNeeded();
  await expect(switches.first()).toBeVisible();
  await screenshot(page, testInfo, "historical-panel-networks");
  await assertAccessible(page);

  expect(runtimeErrors).toEqual([]);
});

test("bulk confirm counts only the categories the endpoint will take", async ({
  page,
  request,
}, testInfo) => {
  const years = await context(
    request,
    testInfo.project.name === "mobile-chromium" ? 2030 : 2029,
  );
  const draft = years.future;
  const mappingItem = (id: string, name: string, overrides: Record<string, unknown>) => ({
    category_id: id,
    product_code: "GHS",
    display_name: name,
    plan_code: "A",
    category_status: "needs_review",
    rule_status: "validated",
    source: "slip",
    matching_rule: { op: "all" },
    rule_human_readable: `${name} rule`,
    confidence: 0.9,
    matched_count: 12,
    expected_count: 12,
    unresolved_clauses: [],
    errors: [],
    warnings: [],
    reused: false,
    bulk_confirmable: false,
    ...overrides,
  });
  await page.route(`**${API}/policy-years/${draft.id}/eligibility-mappings`, (route) =>
    route.fulfill({ json: {
      policy_year_id: draft.id, employee_count: 24, total: 2, validated: 2, proposed: 0,
      needs_review: 1, unmapped: 0, not_applicable: 0, reused: 0, missing_categories: 0,
      missing_category_plans: [],
      categories: [
        mappingItem("ready", "Managers", { bulk_confirmable: true }),
        // Validated and confident, but a draft: the endpoint never takes it.
        mappingItem("draft", "Executives", { category_status: "draft" }),
      ],
    } }),
  );
  let bulkConfirms = 0;
  await page.route(`**${API}/categories/bulk-confirm**`, (route) => {
    bulkConfirms += 1;
    // Simulate the list going stale: nothing was confirmed after all.
    return route.fulfill({ json: { confirmed: 0, skipped_invalid_rules: 0, threshold: 0.85 } });
  });
  const runtimeErrors = runtimeMonitor(page);
  await installSession(page, years.client.id);
  await page.goto("/client-relations/company-benefits");
  await selectYear(page, Number(draft.start_date.slice(0, 4)));

  await expect(
    page.getByText("1 employee category has validated rules ready to confirm together. 1 needs individual review."),
  ).toBeVisible();
  await page.getByText("Review categories needing attention").click();
  await expect(page.getByText("GHS: Executives — draft; open it to finish the rule")).toBeVisible();

  await page.getByRole("button", { name: "Review 1 validated rule" }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm validated employee categories?" });
  await expect(dialog.getByText("1 category still needs individual review", { exact: false })).toBeVisible();
  await dialog.getByRole("button", { name: "Confirm 1 rule" }).click();
  await expect(
    page.getByText("1 category changed since this list loaded and was not confirmed. Review the list again."),
  ).toBeVisible();
  await expect(page.getByText("0 employee categories confirmed")).toHaveCount(0);
  expect(bulkConfirms).toBe(1);
  expect(runtimeErrors).toEqual([]);
  await screenshot(page, testInfo, "launch-readiness-bulk-confirm");
});
