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

const emptyMembership = {
  employees_total: 0,
  family_status_counts: {},
  source_counts: {},
  tiers: [],
  assignments: [],
  scheme_status: "draft",
  ineligible_count: 0,
  ineligible_designations: {},
  ambiguous_count: 0,
  ambiguous_examples: [],
};

const emptyCoverage = {
  employees_total: 0,
  employees_ok: 0,
  dependants_total: 0,
  dependants_ok: 0,
  has_tiers: false,
  scheme_status: "draft",
  buckets: [],
  preview_cap: 20,
};

function draftScheme(policyYearId: string) {
  return {
    id: "flex-age-review",
    policy_year_id: policyYearId,
    status: "draft",
    origin: "manual",
    scheme: {
      meta: { scheme_name: "Boundary review", currency: "SGD" },
      tiers: [],
    },
    source_ref: null,
    confidence: null,
    confirmed_at: null,
  };
}

async function mockFlexData(page: Page, policyYearId: string) {
  let current = draftScheme(policyYearId);
  let savedBody: Record<string, unknown> | null = null;

  await page.route(
    new RegExp(`/api/v1/policy-years/${policyYearId}/flex-scheme(?:/.*)?$`),
    async (route) => {
      const url = new URL(route.request().url());
      const suffix = url.pathname.split("/flex-scheme")[1] ?? "";
      if (suffix === "/membership") {
        await route.fulfill({ json: emptyMembership });
        return;
      }
      if (suffix === "/coverage") {
        await route.fulfill({ json: emptyCoverage });
        return;
      }
      if (suffix === "/roster-vocab") {
        await route.fulfill({
          json: { employees_total: 0, designations: [], grades: [] },
        });
        return;
      }
      if (suffix === "" && route.request().method() === "PUT") {
        const payload = route.request().postDataJSON() as {
          scheme: Record<string, unknown>;
        };
        savedBody = payload.scheme;
        current = { ...current, scheme: payload.scheme };
        await route.fulfill({ json: current });
        return;
      }
      if (suffix === "" && route.request().method() === "GET") {
        await route.fulfill({ json: current });
        return;
      }
      await route.fulfill({ status: 404, json: { detail: "Not found" } });
    },
  );

  return () => savedBody;
}

test("broker saves inclusive ANB employee and dependant boundaries", async ({
  page,
  request,
}) => {
  const policyYearId = await installCurrentSession(page, request);
  const savedBody = await mockFlexData(page, policyYearId);

  await page.goto("/client-relations/company-benefits?tab=flex");
  await expect(page.getByText("Age next birthday · inclusive")).toBeVisible();

  await page.getByLabel("Employee min").fill("18");
  await page.getByLabel("Employee max").fill("65");
  await page.getByLabel("Spouse max").fill("70");
  await page.getByLabel("Child max").fill("25");
  await page.getByRole("button", { name: "Save draft" }).click();

  await expect.poll(savedBody).toMatchObject({
    meta: {
      employee_age_limits: { min: 18, max: 65 },
      dependant_age_limits: {
        spouse: { max: 70 },
        child: { max: 25 },
      },
    },
  });
  await expect(page.getByText("Flex scheme saved")).toBeVisible();
});

test("broker viewer sees the age convention without mutation controls", async ({
  page,
  request,
}) => {
  const policyYearId = await installCurrentSession(page, request);
  await mockFlexData(page, policyYearId);
  await page.route(/\/api\/v1\/me$/, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...body, role: "broker_viewer" } });
  });

  await page.goto("/client-relations/company-benefits?tab=flex");
  await expect(page.getByText(/broker viewer role is read-only/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /upload flexible benefits/i })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("Employee min")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save draft" })).toHaveCount(0);
});
