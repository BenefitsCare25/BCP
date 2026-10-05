import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "/api/v1";

async function mappingDrawer(page: Page, request: APIRequestContext) {
  const { year } = await session(page, request);
  let employee = { id: "mapping-review", staff_id: "SAVE-1", employee_name: "Mapping Review Member",
    attribute_values: { category: "Executives", email: "review@example.test" }, derived_attribute_values: {},
    matched_category_id: "dental", match_method: "rule", match_confidence: 1,
    updated_at: "2026-10-01T00:00:00Z", matched_plans: [{ category_id: "dental", product_code: "GD", plan_code: "1" }],
    unmatched_product_codes: ["GCGP"], roster_fields: [{ attribute_id: "category", display_name: "Employee category", data_type: "string" }],
  };
  await page.route(`**${API}/policy-years/${year.id}/member-facets`, (route) => route.fulfill({ json: {
    employees_total: 1, terminated_total: 0, categories: [], products: [], attributes: [],
  } }));
  await page.route(`**${API}/policy-years/${year.id}/member-query/list`, (route) => route.fulfill({ json: {
    total: 1, items: [employee], offset: 0, limit: 50,
  } }));
  await page.route(`**${API}/employees/${employee.id}`, (route) => {
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON();
      expect(payload.attribute_values.email).toBe("review@example.test");
      expect(payload.expected_updated_at).toBe(employee.updated_at);
      employee = { ...employee, attribute_values: payload.attribute_values, updated_at: "2026-10-01T02:00:00Z" };
    }
    return route.fulfill({ json: employee });
  });
  const category = (id: string, label: string, code: string, scope = "employee") => ({
    id, display_name: label, plan_assignments: { plan_code: code, member_scope: scope },
  });
  await page.route(`**${API}/categories/grouped?*`, (route) => route.fulfill({ json: [
    { product_code: "GD", product_display_name: "Dental", categories: [category("dental", "All employees", "1")] },
    { product_code: "GCGP", product_display_name: "General practitioner", categories: [
      category("gp-1", "Executives", "1"), category("gp-2", "Executives", "2"),
      category("spouse-1", "Spouse", "1", "dependant"), category("spouse-2", "Spouse", "2", "dependant"),
    ] },
  ] }));
  let saveCalls = 0;
  let reject = false;
  await page.route(`**${API}/match-results/employees/${employee.id}/override`, (route) => {
    saveCalls++;
    if (reject) return route.fulfill({ status: 409, json: { detail: "Review conflict" } });
    const payload = route.request().postDataJSON();
    expect(payload.category_ids).toEqual(["dental", "gp-2"]);
    expect(payload.expected_updated_at).toBe(employee.updated_at);
    employee = { ...employee, unmatched_product_codes: [], updated_at: "2026-10-01T01:00:00Z",
      matched_plans: [...employee.matched_plans, { category_id: "gp-2", product_code: "GCGP", plan_code: "2" }],
    };
    return route.fulfill({ json: { employee_id: employee.id, updated_at: employee.updated_at } });
  });
  await page.goto(`/policy-admin/member-listing?tab=employees&employee=${employee.id}`);
  await expect(page.getByRole("heading", { name: employee.employee_name, exact: true })).toBeVisible({ timeout: 15000 });
  return { calls: () => saveCalls, reject: (value: boolean) => { reject = value; } };
}

test("employee mapping has a persistent save, excludes dependant options, and protects unsaved edits", async ({ page, request }, info) => {
  const review = await mappingDrawer(page, request);
  const drawer = page.getByRole("dialog", { name: "Mapping Review Member" });
  await expect(drawer.getByText("Missing product mapping: GCGP")).toBeVisible();
  await expect(drawer.getByRole("checkbox", { name: /Spouse/ })).toHaveCount(0);
  const save = drawer.getByRole("button", { name: "Save employee mapping", exact: true });
  await expect(save).toBeDisabled();
  await drawer.getByRole("checkbox", { name: "Executives · Plan 1", exact: true }).check();
  await drawer.getByRole("checkbox", { name: "Executives · Plan 2", exact: true }).check();
  await expect(drawer.getByRole("checkbox", { name: "Executives · Plan 1", exact: true })).not.toBeChecked();
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Unsaved employee mapping" })).toBeVisible();
  await page.getByRole("button", { name: "Continue editing", exact: true }).click();
  review.reject(true);
  await save.click();
  await expect(drawer.getByRole("alert")).toContainText("have not been saved");
  await expect(drawer.getByRole("checkbox", { name: "Executives · Plan 2", exact: true })).toBeChecked();
  review.reject(false);
  await save.click();
  await expect(save).toBeDisabled();
  await expect(drawer.getByText("Missing product mapping: GCGP")).toHaveCount(0);
  expect(review.calls()).toBe(2);
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Executives · Plan 2", exact: true })).toBeChecked();
  await page.screenshot({ path: info.outputPath("employee-mapping-save.png"), fullPage: true });
});

test("editing the employee category keeps Save changes visible and persists the roster record", async ({ page, request }, info) => {
  await mappingDrawer(page, request);
  const drawer = page.getByRole("dialog", { name: "Mapping Review Member" });
  await drawer.getByRole("button", { name: "Edit roster data", exact: true }).click();
  const save = drawer.getByRole("button", { name: "Save changes", exact: true });
  await expect(save).toBeVisible();
  await expect(save).toBeInViewport();
  await drawer.getByRole("textbox", { name: "Employee category", exact: true }).fill("Senior executives");
  await expect(save).toBeEnabled();
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Unsaved roster changes" })).toBeVisible();
  await page.getByRole("button", { name: "Continue editing", exact: true }).click();
  await save.click();
  await expect(drawer.getByText("All changes saved", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Senior executives", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog", { name: "Mapping Review Member" }).getByText("Senior executives", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("employee-roster-save.png"), fullPage: true });
});

async function session(page: Page, request: APIRequestContext) {
  const me = await (await request.get(`${API}/me`)).json();
  const client = me.accessible_clients[0];
  const headers = { "X-Inspro-Client": client.id };
  const years = await (await request.get(`${API}/policy-years`, { headers })).json();
  const today = new Date().toISOString().slice(0, 10);
  const year = years.find((y: { start_date: string; end_date: string }) => y.start_date <= today && y.end_date >= today) ?? years[0];
  await page.addInitScript(({ clientId, yearId }) => {
    localStorage.setItem("inspro-session", JSON.stringify({ state: {
      activeClientId: clientId, currentPolicyYearId: yearId, policyYearClientId: clientId,
    }, version: 0 }));
  }, { clientId: client.id, yearId: year.id });
  return { year, headers };
}

const period = {
  id: "review-period", name: "Employee review", window_type: "open", status: "draft",
  opens_at: "2026-10-01T00:00:00Z", closes_at: "2026-10-31T23:59:59Z",
  default_behavior: "deemed_keep_current", allow_plan_change: true, allow_leave: false,
  allow_dependant_changes: true, member_self_service: true, uses_flex: false,
  product_scope: ["GCGP", "GD"], flex_price_source: null, flex_drawdown_rule: "full",
  allow_overdraft: false, created_by: null,
};

const rollout = {
  employees_total: 5, invite_pending: 2, invited: 0, signed_in: 0, no_email: 1,
  duplicate: 2, disabled: 0, mail_deliverable: true, mail_mode: "smtp", sending: false,
  needs_attention: [{ employee_id: "shared-1", staff_id: "HR-1", employee_name: "Shared Member",
    reason: "duplicate", email: "hr@review.test" }], needs_attention_truncated: false,
};

const issue = {
  code: "unconfirmed_categories", severity: "blocker", count: 2, count_unit: "mappings", employee_count: 51,
  message: "Eligibility mappings currently assigned to employees still require broker review and confirmation.",
  products: ["GCGP"],
};

async function overview(page: Page, yearId: string) {
  await page.route(`**${API}/policy-years/${yearId}/enrollment-windows`, (route) =>
    route.fulfill({ json: [{ ...period, policy_year_id: yearId }] }));
  await page.route(`**${API}/member-accounts/rollout?*`, (route) => route.fulfill({ json: rollout }));
  await page.route(`**${API}/enrollment-windows/review-period/readiness`, (route) =>
    route.fulfill({ json: { ready: false, issues: [issue] } }));
}

test("readiness names employees, distinguishes mappings, and supports search and paging", async ({ page, request }, info) => {
  const { year } = await session(page, request);
  await overview(page, year.id);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const employees = Array.from({ length: 51 }, (_, i) => ({
    employee_id: `review-${i}`, staff_id: `R-${String(i).padStart(3, "0")}`, employee_name: `Review Employee ${i}`,
    employee_category: "Executives", grade: "E10", reason: "Assigned eligibility mappings need broker confirmation.",
    products: [], mappings: [{ category_id: "review-category", category_name: "Executives", product_code: "GCGP", status: "needs_review" }],
  }));
  await page.route(`**${API}/enrollment-windows/review-period/readiness/unconfirmed_categories/employees?*`, (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get("q") ?? "";
    const offset = Number(url.searchParams.get("offset") ?? "0");
    const rows = employees.filter((e) => `${e.staff_id} ${e.employee_name}`.toLowerCase().includes(q.toLowerCase()));
    return route.fulfill({ json: { total: rows.length, items: rows.slice(offset, offset + 50) } });
  });
  await page.goto("/client-relations/enrollment");
  await expect(page.getByText("· 2 mappings", { exact: true })).toBeVisible();
  await expect(page.getByText("· 51 employees affected", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open period", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Show affected employees", exact: true }).click();
  await expect(page.getByRole("link", { name: "Review Employee 0", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review GCGP · Executives" }).first()).toHaveAttribute("href", /category=review-category/);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("link", { name: "Review Employee 50", exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Search affected employees" }).fill("R-001");
  await expect(page.getByRole("link", { name: "Review Employee 1", exact: true })).toBeVisible();
  await expect(page.getByText("1 employee matches your search", { exact: true })).toBeVisible();
  const accessible = await new AxeBuilder({ page }).include('section').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(accessible.violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("enrolment-employee-review.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("bulk invitations target unique addresses and shared mailboxes have an individual path", async ({ page, request }, info) => {
  const { year } = await session(page, request);
  await overview(page, year.id);
  let mailReady = false;
  let sent = false;
  let calls = 0;
  await page.route(`**${API}/member-accounts/rollout?*`, (route) => route.fulfill({ json: {
    ...rollout, mail_deliverable: mailReady, invite_pending: sent ? 0 : 2, invited: sent ? 2 : 0,
  } }));
  await page.route(`**${API}/member-accounts/bulk-invite`, (route) => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ policy_year_id: year.id });
    sent = true;
    return route.fulfill({ json: { queued: 2, accounts_created: 2, duplicate: 2, no_email: 1,
      already_invited: 0, skipped_disabled: 0, already_sending: false } });
  });
  await page.goto("/client-relations/enrollment");
  await expect(page.getByRole("button", { name: "Send all 2 invitations", exact: true })).toBeDisabled();
  await expect(page.getByRole("link", { name: "Shared Member", exact: true })).toHaveAttribute("href", /employee=shared-1/);
  mailReady = true;
  await page.reload();
  await page.getByRole("button", { name: "Send all 2 invitations", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("2 employees selected.");
  await page.getByRole("dialog").getByRole("button", { name: "Send invitations", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send all invitations", exact: true })).toBeDisabled();
  expect(calls).toBe(1);
  await page.screenshot({ path: info.outputPath("bulk-invitation-follow-up.png"), fullPage: true });
});

for (const checksFail of [false, true]) {
  test(`enrolment opens without mail delivery when validation ${checksFail ? "cannot load" : "has warnings"}`,
    async ({ page, request }, info) => {
      const { year } = await session(page, request);
      await overview(page, year.id);
      let opened = false;
      let opens = 0;
      let invitations = 0;
      await page.route(`**${API}/policy-years/${year.id}/enrollment-windows`, (route) =>
        route.fulfill({ json: [{ ...period, policy_year_id: year.id, status: opened ? "open" : "draft" }] }));
      await page.route(`**${API}/member-accounts/rollout?*`, (route) =>
        route.fulfill({ json: { ...rollout, mail_deliverable: false } }));
      await page.route(`**${API}/member-accounts/bulk-invite`, (route) => {
        invitations++;
        return route.fulfill({ status: 503, json: { detail: "Email delivery is not configured." } });
      });
      await page.route(`**${API}/enrollment-windows/review-period/readiness`, (route) =>
        checksFail ? route.fulfill({ status: 503, json: { detail: "Validation unavailable" } }) :
          route.fulfill({ json: { ready: false, issues: [issue, { code: "portal_access_incomplete",
            severity: "warning", message: "Some employees do not have portal access.", count: 5 }] } }));
      await page.route(`**${API}/enrollment-windows/review-period/progress`, (route) =>
        route.fulfill({ json: { total: 5, not_started: 5, in_progress: 0, submitted: 0,
          confirmed: 0, deemed: 0, declined: 0, not_in_period: 0 } }));
      await page.route(`**${API}/enrollment-windows/review-period/open`, (route) => {
        opens++;
        opened = true;
        return route.fulfill({ json: { window: { ...period, policy_year_id: year.id, status: "open" },
          enrollments_created: 5 } });
      });
      await page.goto("/client-relations/enrollment");
      const open = page.getByRole("button", { name: "Open period", exact: true });
      await expect(open).toBeEnabled();
      if (checksFail) await expect(page.getByRole("alert").filter({ hasText: "Could not load validation checks" })).toBeVisible({ timeout: 20000 });
      else await expect(page.getByText("2 validation warnings", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Send all 2 invitations", exact: true })).toBeDisabled();
      await page.screenshot({ path: info.outputPath("open-with-advisory-validation.png"), fullPage: true });
      await open.click();
      await expect(page.getByRole("button", { name: "Close period", exact: true })).toBeVisible();
      if (!checksFail) {
        await expect(page.getByText("2 validation warnings", { exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "Show affected employees", exact: true })).toHaveCount(2);
      }
      expect(opens).toBe(1);
      expect(invitations).toBe(0);
      await page.screenshot({ path: info.outputPath("opened-with-validation-retained.png"), fullPage: true });
    });
}

test("send all offers valid disabled accounts with explicit confirmation and concise content", async ({ page, request }, info) => {
  const { year } = await session(page, request);
  await overview(page, year.id);
  let sent = false;
  let calls = 0;
  await page.route(`**${API}/member-accounts/rollout?*`, (route) => route.fulfill({ json: {
    ...rollout, invite_pending: 0, disabled: sent ? 0 : 466,
    disabled_invite_pending: sent ? 0 : 466, invited: sent ? 466 : 0,
    mail_deliverable: true, mail_mode: "log",
  } }));
  await page.route(`**${API}/member-accounts/bulk-invite`, (route) => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ policy_year_id: year.id, reenable_disabled: true });
    sent = true;
    return route.fulfill({ json: { queued: 466, accounts_created: 0, accounts_reenabled: 466,
      duplicate: 2, no_email: 1, already_invited: 0, skipped_disabled: 0, already_sending: false } });
  });
  await page.goto("/client-relations/enrollment");
  const send = page.getByRole("button", { name: "Send all 466 invitations", exact: true });
  await expect(send).toBeEnabled();
  for (const text of ["Everyone reachable by email", "This environment writes invites",
    "Use an employee's own verified email", "Send invitations in bulk", "Nobody else ever sees it"]) {
    await expect(page.getByText(text, { exact: false })).toHaveCount(0);
  }
  await page.screenshot({ path: info.outputPath("send-all-concise-panel.png"), fullPage: true });
  await send.click();
  const dialog = page.getByRole("dialog", { name: "Send all invitations?" });
  await expect(dialog.getByRole("button", { name: "Send invitations", exact: true })).toBeDisabled();
  expect(calls).toBe(0);
  await dialog.getByRole("checkbox", { name: "Re-enable 466 disabled accounts and invite them." }).check();
  await expect(dialog).toContainText("466 employees selected.");
  const violations = (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations;
  expect(violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("send-all-disabled-confirmation.png"), fullPage: true });
  await dialog.getByRole("button", { name: "Re-enable and send", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send all invitations", exact: true })).toBeDisabled();
  expect(calls).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("unmatched product links and employee category filters select the same roster rows", async ({ page, request }, info) => {
  const { year } = await session(page, request);
  const member = { id: "partial", staff_id: "100962", employee_name: "Partial Review Member",
    attribute_values: { category: "SM to SVP", job_grade: "E10", entity: "Review Entity" }, derived_attribute_values: {},
    matched_category_id: "dental-category", match_method: "rule", match_confidence: 1,
    matched_plans: [{ product_code: "GD", plan_code: "1" }], unmatched_product_codes: ["GCGP"] };
  await page.route(`**${API}/policy-years/${year.id}/member-facets`, (route) => route.fulfill({ json: {
    employees_total: 1, terminated_total: 0, categories: [], products: [{ id: "gp", code: "GCGP", covered: 0 }, { id: "dental", code: "GD", covered: 1 }],
    attributes: [{ key: "category", label: "Employee category", values: [{ value: "SM to SVP", count: 1 }, { value: "Other", count: 0 }], truncated: false },
      { key: "job_grade", label: "Job grade", values: [{ value: "E10", count: 1 }], truncated: false }],
  } }));
  await page.route(`**${API}/policy-years/${year.id}/member-query/list`, (route) => {
    const body = route.request().postDataJSON();
    const cat = body.query.attributes.find((a: { key: string }) => a.key === "category");
    const hit = !cat || cat.values.includes("SM to SVP");
    expect(body.query.match_status).toBe("unmatched");
    expect(body.query.product_codes).toEqual(["GCGP"]);
    return route.fulfill({ json: { total: hit ? 1 : 0, offset: 0, limit: 50, items: hit ? [member] : [] } });
  });
  await page.goto("/policy-admin/member-listing?tab=employees&product=GCGP&match=unmatched");
  await expect(page.getByRole("cell", { name: "Partial Review Member", exact: true })).toBeVisible();
  await expect(page.getByText("Missing GCGP", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Filters/ }).click();
  await page.getByRole("combobox", { name: "Employee category", exact: true }).click();
  await page.getByRole("option", { name: /Other/ }).click();
  await expect(page.getByText("No employees match these filters.")).toBeVisible();
  await page.getByRole("button", { name: "Employee category: Other" }).click();
  await expect(page.getByRole("cell", { name: "Partial Review Member", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Employee category", exact: true }).click();
  await page.getByRole("option", { name: /SM to SVP/ }).click();
  await expect(page.getByRole("cell", { name: "Partial Review Member", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Job grade", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("unmatched-category-filter.png"), fullPage: true });
});

test("individual activation creates access without using the roster email", async ({ page, request }) => {
  const { year } = await session(page, request);
  const employee = { id: "activation-review", staff_id: "LINK-1", employee_name: "Activation Review Member",
    attribute_values: { category: "Executives", email: "shared-hr@review.test" }, derived_attribute_values: {},
    matched_category_id: null, match_method: null, match_confidence: null, matched_plans: [] };
  await page.route(`**${API}/employees/${employee.id}/benefit-statement`, (route) => route.fulfill({ json: {
    employee, coverage: [], flex: null, attributes: [], dependants: [], policy_year: year, is_matched: false,
  } }));
  await page.route(`**${API}/employees/coverage-summary?*`, (route) => route.fulfill({ json: {
    total: 1, items: [{ id: employee.id, staff_id: employee.staff_id, employee_name: employee.employee_name,
      product_count: 0, products: [], needs_check: false, eligible_count: 0, left: false }],
  } }));
  let account: Record<string, unknown> | undefined;
  await page.route(`**${API}/member-accounts`, (route) => route.fulfill({ json: {
    total: account ? 1 : 0, items: account ? [account] : [], password_min_length: 12, set_password_ttl_hours: 24,
  } }));
  await page.route(`**${API}/employees/${employee.id}/member-account`, (route) => {
    expect(route.request().postDataJSON().delivery).toBe("individual_link");
    account = { id: "activation-account", staff_id: employee.staff_id, email: null, status: "invited", system_login_id: "REVIEW-LOGIN",
      login_username: "REVIEW-LOGIN", invite_sent_at: null, has_password: false, tenant_slug: "demo", access_state: "active" };
    return route.fulfill({ status: 201, json: { ...account, set_password_token: "synthetic-review-token" } });
  });
  await page.goto(`/policy-admin/member-coverage?employee=${employee.id}`);
  await page.getByRole("button", { name: /Portal access/ }).click();
  await page.getByRole("button", { name: "Create individual activation link", exact: true }).click();
  await expect(page.getByText(/One-time link — opens on their portal/)).toBeVisible();
  await expect(page.getByText("REVIEW-LOGIN", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy", exact: true })).toBeVisible();
});
