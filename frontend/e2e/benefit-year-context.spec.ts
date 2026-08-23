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
  await expect(page.getByText("2026 benefit year · selected")).toBeVisible();

  await selectYear(page, 2025);
  await expect(page.getByText("2025 benefit year · selected")).toBeVisible();
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
  }

  await expect(
    page.getByText("No products are configured for this benefit year"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Download" }).first()).toBeVisible();
  await screenshot(page, testInfo, "historical-reports");
  await assertAccessible(page);

  await page.goto("/client-relations/company-benefits");
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
  const grace = page.getByLabel("Claim submission grace period (days)");
  await expect(grace).toHaveValue("15");
  await selectYear(page, 2026);
  await expect(grace).toHaveValue("30");
  await selectYear(page, years.future.year);
  await expect(grace).toHaveValue("45");
  await grace.fill("46");
  await grace.blur();
  await expect(page.getByText("Claim grace period updated")).toBeVisible();
  await selectYear(page, 2026);
  await selectYear(page, years.future.year);
  await expect(grace).toHaveValue("46");

  const draft =
    testInfo.project.name === "mobile-chromium"
      ? { code: "GTL", line: "Life Insurance" }
      : { code: "GHS", line: "Medical Insurance" };
  const draftResponse = await request.put(
    `${API}/policy-years/${years.current.id}/product-setups/${draft.code}`,
    {
      headers: years.headers,
      data: { answers: {}, template_version: 1 },
    },
  );
  expect(draftResponse.ok(), await draftResponse.text()).toBeTruthy();
  const listingResponse = await request.post(`${API}/panel-listings`, {
    headers: years.headers,
    data: {
      insurer: `E2E-${testInfo.project.name}`,
      panel_provider: "Release gate",
      country: "SG",
      clinic_type: "gp",
    },
  });
  expect(listingResponse.ok(), await listingResponse.text()).toBeTruthy();
  const listing = (await listingResponse.json()) as PanelListing;
  const panelsResponse = await request.put(
    `${API}/policy-years/${years.current.id}/panels`,
    {
      headers: years.headers,
      data: { panel_listing_ids: [listing.id] },
    },
  );
  expect(panelsResponse.ok(), await panelsResponse.text()).toBeTruthy();

  await selectYear(page, 2025);
  await page.goto("/client-relations/company-benefits");
  for (const line of ["Medical Insurance", "Life Insurance", "General Insurance"]) {
    const tab = page.getByRole("tab", { name: new RegExp(`^${line}`) });
    await expect(tab).not.toContainText(/\d/);
  }

  await selectYear(page, 2026);
  const currentTab = page.getByRole("tab", {
    name: new RegExp(`^${draft.line}`),
  });
  await expect(currentTab).toContainText(/\d/);

  await page.goto("/policy-admin/panel-clinics");
  const switches = page.getByRole("switch");
  await expect.poll(() => switches.count()).toBeGreaterThan(0);
  const currentChecked = await switches.evaluateAll((items) =>
    items.filter((item) => (item as HTMLButtonElement).dataset.state === "checked")
      .length,
  );
  expect(currentChecked).toBeGreaterThan(0);

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
