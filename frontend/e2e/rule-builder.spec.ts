import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { addRequiredCondition } from "../src/components/primitives/RuleBuilder";
import type { AttributeSchema, RuleNode } from "../src/types";

const SCHEMA: AttributeSchema[] = [
  {
    id: "job-category-schema",
    client_id: null,
    attribute_id: "job_category",
    display_name: "Job Category Code",
    data_type: "string",
    enum_values: null,
    is_required: false,
    is_pii: false,
    allow_matching: true,
    allow_ai_values: true,
    description: null,
    derived_from: null,
    derivation_rule: null,
  },
  {
    id: "nationality-schema",
    client_id: null,
    attribute_id: "nationality",
    display_name: "Nationality",
    data_type: "enum",
    enum_values: ["Thai", "Singaporean"],
    is_required: false,
    is_pii: true,
    allow_matching: true,
    allow_ai_values: false,
    description: null,
    derived_from: null,
    derivation_rule: null,
  },
];

test("adding a manual condition preserves a single rule and requires both", () => {
  const current: RuleNode = {
    in: ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]],
  };

  expect(addRequiredCondition(current, SCHEMA)).toEqual({
    and: [
      current,
      { "=": ["", ""] },
    ],
  });
});

test("adding a condition extends an existing AND group without nesting it", () => {
  const current: RuleNode = {
    and: [
      { in: ["job_category", ["J1", "J2"]] },
      { in: ["nationality", ["Thai"]] },
    ],
  };

  expect(addRequiredCondition(current, SCHEMA)).toEqual({
    and: [
      { in: ["job_category", ["J1", "J2"]] },
      { in: ["nationality", ["Thai"]] },
      { "=": ["", ""] },
    ],
  });
});

async function apiJson<T>(
  response: Awaited<ReturnType<APIRequestContext["get"]>>,
): Promise<T> {
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as T;
}

async function installBrokerSession(
  page: Page,
  clientId: string,
  policyYearId: string,
) {
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

test("a broker can add a second condition to a single-condition category", async ({
  page,
  request,
}, testInfo) => {
  const me = await apiJson<{ accessible_clients: Array<{ id: string }> }>(
    await request.get("/api/v1/me"),
  );
  const clientId = me.accessible_clients[0]?.id;
  expect(clientId).toBeTruthy();
  const headers = { "X-Inspro-Client": clientId };
  const years = await apiJson<Array<{ id: string; start_date: string; end_date: string }>>(
    await request.get("/api/v1/policy-years", { headers }),
  );
  const policyYear = years.find(
    (candidate) =>
      candidate.start_date <= "2026-08-31" &&
      candidate.end_date >= "2026-08-31",
  );
  expect(policyYear).toBeDefined();

  const productCode = testInfo.project.name.startsWith("mobile") ? "RBM" : "RBD";
  const product = await apiJson<{ id: string; code: string }>(
    await request.post("/api/v1/schemas/products?scope=company", {
      headers,
      data: {
        code: productCode,
        display_name: "Rule Builder Test Product",
        participation_model: "standard",
        has_dependants: false,
        is_outpatient: false,
        line: "medical",
        form_profile: "tiered_medical",
      },
    }),
  );
  const category = await apiJson<{ id: string; display_name: string }>(
    await request.post("/api/v1/categories", {
      headers,
      data: {
        policy_year_id: policyYear!.id,
        product_id: product.id,
        display_name: "Officer category for manual rule editing",
      },
    }),
  );
  const patchResponse = await request.patch(`/api/v1/categories/${category.id}`, {
    headers,
    data: {
      matching_rule: {
        in: ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]],
      },
    },
  });
  expect(patchResponse.ok(), await patchResponse.text()).toBeTruthy();

  try {
    await installBrokerSession(page, clientId!, policyYear!.id);
    await page.goto("/client-relations/company-benefits");

    await page.getByRole("tab", { name: "Medical" }).click();
    await page.getByRole("tab", { name: productCode, exact: true }).click();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page
      .getByRole("button", { name: /Employee Category & Plan Type/ })
      .click();

    const categorySection = page
      .locator("section")
      .filter({ hasText: category.display_name })
      .first();
    await categorySection
      .getByRole("button", { name: "Employee category rule" })
      .click();

    const editor = page.getByRole("dialog");
    const addCondition = editor.getByRole("button", { name: "Add condition" });
    await expect(addCondition).toBeVisible();
    await addCondition.click();

    await expect(editor.getByText("AND", { exact: true })).toBeVisible();
    const attributes = editor.getByRole("combobox", {
      name: "Employee attribute",
    });
    await expect(attributes).toHaveCount(2);
    await attributes.nth(1).click();
    await page.getByRole("option", { name: "Nationality", exact: true }).click();

    const operators = editor.getByRole("combobox", {
      name: "Comparison operator",
    });
    await operators.nth(1).click();
    await page.getByRole("option", { name: "in", exact: true }).click();
    await editor
      .getByRole("textbox", { name: "Nationality values" })
      .fill("Thai");

    await editor.getByText("Technical rule JSON").click();
    await expect(editor.locator("pre")).toContainText('"nationality"');
    await expect(editor.locator("pre")).toContainText('"Thai"');

    const accessibility = await new AxeBuilder({ page })
      .include('[role="dialog"]')
      .analyze();
    expect(accessibility.violations).toEqual([]);
  } finally {
    const cleanup = await request.delete(
      `/api/v1/policy-years/${policyYear!.id}/products/${productCode}`,
      { headers },
    );
    expect(cleanup.ok(), await cleanup.text()).toBeTruthy();
  }
});
