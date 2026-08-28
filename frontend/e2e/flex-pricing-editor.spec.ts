import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "/api/v1";

async function apiJson<T>(
  response: Awaited<ReturnType<APIRequestContext["get"]>>,
): Promise<T> {
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as T;
}

async function installCurrentSession(page: Page, request: APIRequestContext) {
  const me = await apiJson<{
    accessible_clients: Array<{ id: string }>;
  }>(await request.get(`${API}/me`));
  const clientId = me.accessible_clients[0]?.id;
  expect(clientId).toBeTruthy();
  const years = await apiJson<
    Array<{ id: string; start_date: string; end_date: string }>
  >(
    await request.get(`${API}/policy-years`, {
      headers: { "X-Inspro-Client": clientId! },
    }),
  );
  const today = new Date().toISOString().slice(0, 10);
  const policyYearId =
    years.find((year) => year.start_date <= today && year.end_date >= today)?.id ??
    years[0]?.id;
  expect(policyYearId).toBeTruthy();
  await page.addInitScript(
    ({ activeClientId, currentPolicyYearId }) => {
      localStorage.setItem(
        "inspro-session",
        JSON.stringify({
          state: {
            activeClientId,
            currentPolicyYearId,
            policyYearClientId: activeClientId,
          },
          version: 0,
        }),
      );
    },
    { activeClientId: clientId!, currentPolicyYearId: policyYearId! },
  );
  return policyYearId!;
}

const draftWindow = {
  id: "draft-window",
  policy_year_id: "policy-year",
  name: "Pricing review",
  window_type: "open",
  opens_at: "2026-10-01T00:00:00Z",
  closes_at: "2026-10-31T23:59:59Z",
  status: "draft",
  default_behavior: "deemed_keep_current",
  allow_plan_change: true,
  allow_leave: false,
  allow_dependant_changes: true,
  member_self_service: true,
  product_scope: null,
  flex_price_source: { legacy: "manual" },
  flex_drawdown_rule: "full",
  allow_overdraft: false,
  created_by: null,
};

const ageBands = [
  { label: "0-25", min: null, max: 25 },
  { label: "26-35", min: 26, max: 35 },
  { label: "36+", min: 36, max: null },
];

const recommendedRates = [
  { ...ageBands[0], rate: 1 },
  { ...ageBands[1], rate: 2 },
  { ...ageBands[2], rate: 3 },
];

const fixedTier = (
  key: string,
  label: string,
  cohortId: string,
  cohortLabel: string,
  premium: number,
) => ({
  key,
  option_id: `SHARED::${label.toLowerCase()}`,
  label,
  plan_code: "SHARED",
  pricing_mode: "plan_type",
  direction: "same",
  is_baseline: label === "Option 1",
  participation: label === "Option 1" ? "compulsory" : "voluntary",
  slip_premium: premium,
  sum_insured: null,
  cohort_id: cohortId,
  cohort_label: cohortLabel,
});

const pricingResponse = {
  policy_year_id: "policy-year",
  pricing: {
    products: {
      "p-gci": {
        age_bands: ageBands,
        price_tags: {
          "gci-exec-option::10": {
            "0-25": 999,
            "26-35": 999,
            "36+": 999,
          },
        },
      },
    },
  },
  products: [
    {
      product_id: "p-gpa",
      product_code: "GPA",
      line: "life",
      pricing_mode: "plan_type",
      voluntary_rates: null,
      dependant_age_limits: {},
      dependant_suggested_mode: "none",
      slip_family: {},
      slip_per_pax: {},
      tiers: [
        fixedTier("gpa-exec-o1::SHARED", "Option 1", "exec", "Executives", 10),
        fixedTier("gpa-exec-o2::SHARED", "Option 2", "exec", "Executives", 20),
        fixedTier("gpa-exec-o3::SHARED", "Option 3", "exec", "Executives", 30),
        fixedTier("gpa-staff-o1::SHARED", "Option 1", "staff", "Staff", 11),
        fixedTier("gpa-staff-o2::SHARED", "Option 2", "staff", "Staff", 21),
        fixedTier("gpa-staff-o3::SHARED", "Option 3", "staff", "Staff", 31),
      ],
    },
    {
      product_id: "p-gci",
      product_code: "GCI",
      line: "life",
      pricing_mode: "age_banded",
      voluntary_rates: recommendedRates,
      dependant_age_limits: {},
      dependant_suggested_mode: "none",
      slip_family: {},
      slip_per_pax: {},
      tiers: [
        {
          key: "gci-exec-base::1",
          option_id: "1::plan 1",
          label: "Plan 1",
          plan_code: "1",
          pricing_mode: "plan_type",
          direction: "same",
          is_baseline: true,
          participation: "compulsory",
          slip_premium: 144,
          sum_insured: 100_000,
          cohort_id: "gci-exec",
          cohort_label: "Executives",
        },
        {
          key: "gci-exec-option::10",
          option_id: "10::option 1",
          label: "Option 1",
          plan_code: "10",
          pricing_mode: "age_banded",
          direction: "upgrade",
          is_baseline: false,
          participation: "voluntary",
          slip_premium: null,
          sum_insured: 100_000,
          cohort_id: "gci-exec",
          cohort_label: "Executives",
        },
      ],
    },
  ],
};

test("price book separates tier mechanics and keeps overrides option-safe", async ({
  page,
  request,
}) => {
  const policyYearId = await installCurrentSession(page, request);
  let savedPricing: Record<string, unknown> | null = null;

  await page.route(
    new RegExp(`/api/v1/policy-years/${policyYearId}/enrollment-windows`),
    (route) => route.fulfill({ json: [{ ...draftWindow, policy_year_id: policyYearId }] }),
  );
  await page.route(
    new RegExp(`/api/v1/policy-years/${policyYearId}/flex-pricing`),
    (route) =>
      route.fulfill({
        json: { ...pricingResponse, policy_year_id: policyYearId },
      }),
  );
  await page.route(
    new RegExp(`/api/v1/policy-years/${policyYearId}/enrollment-pricing-config`),
    async (route) => {
      savedPricing = (await route.request().postDataJSON()).pricing;
      await route.fulfill({
        json: { ...pricingResponse, policy_year_id: policyYearId, pricing: savedPricing },
      });
    },
  );

  await page.goto("/client-relations/enrollment?tab=flex");
  await expect(page.getByRole("heading", { name: "Recommended price book" })).toBeVisible();
  await expect(page.getByText(/Fixed pricing.*6 plan assignments/)).toBeVisible();
  await expect(page.getByText(/Mixed pricing.*1 fixed assignments.*3 rate bands/)).toBeVisible();

  await page.getByRole("button", { name: /GPA/ }).click();
  const option1Apply = page.getByRole("button", {
    name: "Apply Option 1 price to every employee category",
  }).first();
  await expect(option1Apply).toContainText("Apply to 2");
  await page.getByLabel("GPA Executives Option 1 price tag").fill("77");
  await expect(page.getByLabel("GPA Staff Option 1 price tag")).toHaveValue("11");
  await option1Apply.click();
  await expect(page.getByLabel("GPA Staff Option 1 price tag")).toHaveValue("77");
  await expect(page.getByLabel("GPA Executives Option 2 price tag")).toHaveValue("20");
  await expect(page.getByLabel("GPA Staff Option 2 price tag")).toHaveValue("21");

  await page.getByRole("button", { name: /GCI/ }).click();
  await expect(page.getByRole("heading", { name: "Fixed-price tiers" })).toBeVisible();
  await expect(
    page.getByText("Saved tier overrides take priority over the rate table."),
  ).toBeVisible();
  const calculatedRow = page
    .getByRole("region", { name: "GCI calculated plan prices" })
    .getByRole("row")
    .filter({ hasText: "Option 1" });
  await expect(calculatedRow).toContainText("$999");
  await expect(calculatedRow).toContainText("Tier override");

  await page.getByLabel("Remove 0-25 age band").click();
  const shiftedLabel = page.getByLabel("Age band 1 label");
  await expect(shiftedLabel).toHaveValue("26-35");
  const shiftedBand = shiftedLabel.locator("xpath=ancestor::tr");
  await expect(shiftedBand).toContainText("2");
  await expect(shiftedBand).toContainText("Recommended");

  await page.getByLabel("GCI Executives Plan 1 price tag").fill("155");
  await page.getByRole("button", { name: "Save price tags" }).click();
  await expect.poll(() => savedPricing).not.toBeNull();
  const saved = savedPricing as {
    products: Record<
      string,
      {
        age_bands: typeof ageBands;
        voluntary_rates: typeof recommendedRates;
        price_tags: Record<string, Record<string, number>>;
      }
    >;
  };
  expect(saved.products["p-gci"].voluntary_rates).toHaveLength(2);
  expect(saved.products["p-gci"].age_bands).toHaveLength(3);
  expect(saved.products["p-gci"].price_tags["gci-exec-base::1"]).toEqual({
    "0-25": 155,
    "26-35": 155,
    "36+": 155,
  });
  expect(saved.products["p-gci"].price_tags["gci-exec-option::10"]).toEqual({
    "0-25": 999,
    "26-35": 999,
    "36+": 999,
  });
});
