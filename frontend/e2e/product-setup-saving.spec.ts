import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";

const BASE = "/client-relations/company-benefits";
const API = "/api/v1";

async function json(response: Awaited<ReturnType<APIRequestContext["get"]>>) {
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function setup(page: Page, request: APIRequestContext, info: TestInfo, scenario: number) {
  test.setTimeout(90_000);
  const me = await json(await request.get(`${API}/me`));
  const clientId = me.accessible_clients[0].id as string;
  const headers = { "X-Inspro-Client": clientId };
  const yearNumber = 2041 + scenario * 2 + (info.project.name === "mobile-chromium" ? 1 : 0);
  const years = await json(await request.get(`${API}/policy-years`, { headers }));
  const year = years.find((candidate: { year: number }) => candidate.year === yearNumber)
    ?? await json(await request.post(`${API}/policy-years`, { headers, data: {
    start_date: `${yearNumber}-01-01`, end_date: `${yearNumber}-12-31`,
  } }));
  const label = `Saving ${yearNumber} ${Date.now().toString(36)}`;
  const product = await json(await request.post(`${API}/schemas/products?scope=company`, {
    headers, data: { code: "", display_name: "Draft saving review", variant_of: "GHS",
      variant_label: label, line: "medical", form_profile: "tiered_medical" },
  }));
  const path = `${API}/policy-years/${year.id}/product-setups/${product.code}`;
  await json(await request.put(path, { headers, data: { template_version: 1, answers: {
    header: {}, eligibility: { member_cover_eligibility: "Employee" },
    categories: [], plans: [{ code: "1", label: "Plan 1", selected: true }],
    sob: { columns: [{ id: "one", label: "Plan 1", plan_codes: ["1"] }], items: [] },
    rate_table: {}, endorsements: [], arrangements: {}, cover_description: "Original cover",
  } } }));
  await page.addInitScript(({ clientId, yearId }) => {
    localStorage.setItem("inspro-session", JSON.stringify({ state: {
      activeClientId: clientId, currentPolicyYearId: yearId, policyYearClientId: clientId,
    }, version: 0 }));
  }, { clientId, yearId: year.id });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto(`${BASE}?product=${product.code}&section=header`);
  // Date inputs have no implicit textbox role in Chromium.
  await expect(page.getByLabel(`${product.code} coverage start`)).toBeVisible({ timeout: 30_000 });
  return { clientId, year, product, path, headers, errors, label, clients: me.accessible_clients };
}

async function section(page: Page, label: string) {
  await page.getByRole("button", { name: label, exact: true }).click();
}

test("policy numbers map to product entities, persist as a draft, and apply on confirm", async ({ page, request }, info) => {
  const c = await setup(page, request, info, 3);
  await page.getByRole("textbox", { name: "Source policy number(s)", exact: true }).fill("POL-A, POL-B");
  const confirm = page.getByRole("button", { name: "Confirm setup", exact: true });
  await expect(confirm).toBeDisabled();
  const editor = page.getByRole("region", { name: `${c.product.code} policy-number assignments` });
  await editor.getByRole("button", { name: "Add policy-number assignment" }).click();
  await page.getByLabel("Entity scope 1", { exact: true }).click();
  await page.getByRole("option", { name: "Specific entity", exact: true }).click();
  await page.getByLabel("Legal entity 1", { exact: true }).fill("Review Entity A");
  await page.getByLabel("Policy number 1", { exact: true }).fill("POL-A");
  await editor.getByRole("button", { name: "Add policy-number assignment" }).click();
  await page.getByLabel("Entity scope 2", { exact: true }).click();
  await page.getByRole("option", { name: "Specific entity", exact: true }).click();
  await page.getByLabel("Legal entity 2", { exact: true }).fill(" review entity a ");
  await page.getByLabel("Policy number 2", { exact: true }).fill("POL-B");
  await expect(confirm).toBeDisabled();
  await expect(editor.getByText(/Each entity can have only one policy number/)).toBeVisible();
  await page.getByLabel("Legal entity 2", { exact: true }).fill("Review Entity B");
  await expect(confirm).toBeEnabled();
  await section(page, "SOB");
  await section(page, "Header & Policy");
  await expect(page.getByLabel("Policy number 1", { exact: true })).toHaveValue("POL-A");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft saved · pending confirmation", { exact: true })).toBeVisible();
  const saved = await json(await request.get(c.path, { headers: c.headers }));
  const expected = [{ entity: "Review Entity A", policy_number: "POL-A" }, { entity: "Review Entity B", policy_number: "POL-B" }];
  expect(saved.answers.policy_number_mappings).toEqual(expected);
  const termsPath = `${API}/policy-years/${c.year.id}/product-terms`;
  const before = await json(await request.get(termsPath, { headers: c.headers }));
  expect(before.find((t: { code: string }) => t.code === c.product.code).policy_number_mappings).toBeNull();
  await page.reload();
  await expect(page.getByLabel("Policy number 2", { exact: true })).toHaveValue("POL-B");
  await expect(editor.getByRole("button", { name: /Remove policy-number assignment/ })).toHaveCount(0);
  const accessibility = await new AxeBuilder({ page }).include('section[aria-label$="policy-number assignments"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.getByLabel("Policy number 2", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("policy-number-assignments.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await confirm.click();
  await page.getByRole("dialog").getByRole("button", { name: "Confirm setup", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
  const applied = await json(await request.get(termsPath, { headers: c.headers }));
  const term = applied.find((t: { code: string }) => t.code === c.product.code);
  expect(term.policy_number_mappings).toEqual(expected);
  expect(term.policy_number).toBeNull();
  expect(c.errors).toEqual([]);
});

test("an incomplete policy assignment saves, reloads, and remains editable", async ({ page, request }, info) => {
  const c = await setup(page, request, info, 4);
  await page.getByRole("button", { name: "Add policy-number assignment", exact: true }).click();
  const confirm = page.getByRole("button", { name: "Confirm setup", exact: true });
  await expect(confirm).toBeDisabled();
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft saved · pending confirmation", { exact: true })).toBeVisible();
  await page.reload();
  const number = page.getByLabel("Policy number 1", { exact: true });
  await expect(number).toHaveValue("");
  await expect(confirm).toBeDisabled();
  await number.fill("POL-RECOVERY");
  await expect(confirm).toBeEnabled();
  expect((await json(await request.get(c.path, { headers: c.headers }))).answers.policy_number_mappings).toEqual([{ entity: null, policy_number: "" }]);
  expect(c.errors).toEqual([]);
});

test("a legacy draft without header answers opens its summary and editor", async ({ page, request }, info) => {
  const c = await setup(page, request, info, 5);
  const current = await json(await request.get(c.path, { headers: c.headers }));
  await json(await request.put(c.path, { headers: c.headers, data: {
    template_version: current.template_version, expected_updated_at: current.updated_at, answers: {},
  } }));
  await page.goto(`${BASE}?product=${c.product.code}`);
  await page.reload();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
  await expect(page.getByText("Something went wrong", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await section(page, "Header & Policy");
  await expect(page.getByRole("region", { name: `${c.product.code} policy-number assignments` })).toBeVisible();
  expect(c.errors).toEqual([]);
});

test("draft persists every section, reloads, and applies terms only on confirmation", async ({ page, request }, info) => {
  const c = await setup(page, request, info, 0);
  const end = page.getByLabel(`${c.product.code} coverage end`);
  await end.fill(`${c.year.year}-11-30`);
  await section(page, "SOB");
  const cover = page.getByPlaceholder("What this product covers…");
  await cover.fill("Saved across product sections");
  await section(page, "Header & Policy");
  await expect(end).toHaveValue(`${c.year.year}-11-30`);
  await expect(page.getByRole("status").filter({ hasText: "Unsaved changes" })).toBeVisible();
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft saved · pending confirmation", { exact: true })).toBeVisible();
  const termsPath = `${API}/policy-years/${c.year.id}/product-terms`;
  const terms = await json(await request.get(termsPath, { headers: c.headers }));
  expect(terms.find((t: { code: string }) => t.code === c.product.code).coverage_end).toBe(`${c.year.year}-12-31`);
  const saved = await json(await request.get(c.path, { headers: c.headers }));
  expect(saved.answers.cover_description).toBe("Saved across product sections");
  await page.reload();
  await expect(end).toHaveValue(`${c.year.year}-11-30`);
  await section(page, "SOB");
  await expect(cover).toHaveValue("Saved across product sections");
  await page.getByRole("button", { name: "Confirm setup", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm setup", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
  const applied = await json(await request.get(termsPath, { headers: c.headers }));
  expect(applied.find((t: { code: string }) => t.code === c.product.code).coverage_end).toBe(`${c.year.year}-11-30`);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await section(page, "SOB");
  await cover.fill("Pending update");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft changes · pending confirmation", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Update setup", exact: true })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("saved-product-draft.png"), fullPage: true });
  expect(c.errors).toEqual([]);
});

test("leaving product, insurance line, route and year offers save or discard", async ({ page, request }, info) => {
  const c = await setup(page, request, info, 1);
  const otherYearNumber = c.year.year + 20;
  const years = await json(await request.get(`${API}/policy-years`, { headers: c.headers }));
  if (!years.some((year: { year: number }) => year.year === otherYearNumber)) {
    await json(await request.post(`${API}/policy-years`, { headers: c.headers, data: {
      start_date: `${otherYearNumber}-01-01`, end_date: `${otherYearNumber}-12-31`,
    } }));
  }
  const other = await json(await request.post(`${API}/schemas/products?scope=company`, {
    headers: c.headers, data: { code: "", display_name: "Second saving review", variant_of: "GHS",
      variant_label: `Other ${c.year.year}`, line: "medical", form_profile: "tiered_medical" },
  }));
  await page.reload();
  await section(page, "SOB");
  const cover = page.getByPlaceholder("What this product covers…");
  await cover.fill("Save before switching product");
  const otherTab = page.getByRole("tab", { name: new RegExp(`Other ${c.year.year}`) });
  await otherTab.click();
  const dialog = page.getByRole("dialog", { name: "Save setup changes before leaving?" });
  await dialog.getByRole("button", { name: "Continue editing" }).click();
  await expect(cover).toHaveValue("Save before switching product");
  await otherTab.click();
  await dialog.getByRole("button", { name: "Save draft & leave" }).click();
  await expect(page.getByText("Second saving review", { exact: true }).first()).toBeVisible();
  expect((await json(await request.get(c.path, { headers: c.headers }))).answers.cover_description).toBe("Save before switching product");
  await page.getByRole("tab", { name: new RegExp(c.label) }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await section(page, "SOB");
  await cover.fill("Save before switching line");
  await page.getByRole("tab", { name: /Life Insurance/ }).click();
  await dialog.getByRole("button", { name: "Save draft & leave" }).click();
  await expect(page.getByRole("tab", { name: /Life Insurance/ })).toHaveAttribute("data-state", "active");
  await page.getByRole("tab", { name: /Medical Insurance/ }).click();
  await page.getByRole("tab", { name: new RegExp(c.label) }).click();
  // Returning to the deep-linked product can reopen its editor automatically.
  if (await page.getByRole("button", { name: "Edit", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "Edit", exact: true }).click();
  }
  await section(page, "SOB");
  await cover.fill("Save before leaving page");
  await page.getByRole("link", { name: "All companies", exact: true }).click();
  await dialog.getByRole("button", { name: "Save draft & leave" }).click();
  await expect(page).toHaveURL(/\/home/);
  expect((await json(await request.get(c.path, { headers: c.headers }))).answers.cover_description).toBe("Save before leaving page");
  await page.goto(`${BASE}?product=${c.product.code}&section=header`);
  await section(page, "SOB");
  await cover.fill("Save before changing company");
  const otherClient = c.clients.find((candidate: { id: string }) => candidate.id !== c.clientId);
  expect(otherClient).toBeTruthy();
  await page.getByRole("combobox", { name: "Select company" }).click();
  await page.getByRole("option", { name: otherClient.name, exact: true }).click();
  await dialog.getByRole("button", { name: "Save draft & leave" }).click();
  await expect(page.getByRole("combobox", { name: "Select company" })).toContainText(otherClient.name);
  expect((await json(await request.get(c.path, { headers: c.headers }))).answers.cover_description).toBe("Save before changing company");
  await page.goto(`${BASE}?product=${c.product.code}&section=header`);
  await section(page, "SOB");
  await cover.fill("Discard before changing year");
  await page.getByRole("combobox", { name: "Select benefit year" }).click();
  await page.getByRole("option", { name: new RegExp(String(otherYearNumber)) }).first().click();
  await dialog.getByRole("button", { name: "Discard changes", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Select benefit year" })).toContainText(String(otherYearNumber));
  expect((await json(await request.get(c.path, { headers: c.headers }))).answers.cover_description).toBe("Save before changing company");
  expect(other.id).toBeTruthy();
  expect(c.errors).toEqual([]);
});

test("incomplete terms can be saved and failed save-before-leaving retains inputs", async ({ page, request }, info) => {
  const c = await setup(page, request, info, 2);
  const end = page.getByLabel(`${c.product.code} coverage end`);
  await end.fill("");
  await section(page, "SOB");
  await expect(page.getByRole("button", { name: "Confirm setup", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft saved · pending confirmation", { exact: true })).toBeVisible();
  await page.reload();
  await expect(end).toHaveValue("");
  await end.fill(`${c.year.year}-10-31`);
  await section(page, "SOB");
  const cover = page.getByPlaceholder("What this product covers…");
  await cover.fill("Retain after failed save");
  let fail = true;
  await page.route(`**${c.path}`, route => {
    if (fail && route.request().method() === "PUT") return route.fulfill({ status: 422, json: { detail: "Draft save unavailable. Retry." } });
    return route.continue();
  });
  await page.getByRole("tab", { name: /Life Insurance/ }).click();
  const dialog = page.getByRole("dialog", { name: "Save setup changes before leaving?" });
  await dialog.getByRole("button", { name: "Save draft & leave" }).click();
  await expect(page.getByText("Draft save unavailable. Retry.", { exact: true })).toBeVisible();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Continue editing" }).click();
  await expect(cover).toHaveValue("Retain after failed save");
  await section(page, "Header & Policy");
  await expect(end).toHaveValue(`${c.year.year}-10-31`);
  fail = false;
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByText("Draft saved · pending confirmation", { exact: true })).toBeVisible();
  expect((await json(await request.get(c.path, { headers: c.headers }))).answers.cover_description).toBe("Retain after failed save");
  // The intercepted failed request deliberately appears in Chromium's console.
  expect(c.errors.filter(message => !message.includes("422"))).toEqual([]);
});
