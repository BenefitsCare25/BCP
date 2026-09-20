import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  const meResponse = await request.get("/api/v1/me");
  expect(meResponse.ok()).toBe(true);
  const me = await meResponse.json();
  expect(me.active_client_id).toBeTruthy();
  const yearResponse = await request.get("/api/v1/policy-years", {
    headers: { "X-Inspro-Client": me.active_client_id },
  });
  expect(yearResponse.ok()).toBe(true);
  const years = await yearResponse.json();
  expect(years.length).toBeGreaterThan(0);
  // Persist a real authorized company/year before rendering either scoped page.
  await page.addInitScript(({ clientId, yearId }) => {
    localStorage.setItem("inspro-session", JSON.stringify({
      state: { activeClientId: clientId, currentPolicyYearId: yearId, policyYearClientId: clientId },
      version: 0,
    }));
  }, { clientId: me.active_client_id, yearId: years[0].id });
});

test("Autofill measurements and synthetic messaging render without contacting members", async ({ page }, testInfo) => {
  await page.route("**/api/v1/claims/intake-quality?*", async (route) => {
    await route.fulfill({ json: {
      claims: 4, suggested_fields: 20, corrected_fields: 3, correction_rate: 0.15,
      corrections_by_field: { provider_name: 2, amount: 1 },
    } });
  });
  // All simulation requests go to the actual local endpoint. No claim/member
  // messaging or email endpoint is called by this test.
  const writes: string[] = [];
  page.on("request", (request) => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) {
      writes.push(new URL(request.url()).pathname);
    }
  });
  await page.goto("/claims/review?tab=settings");
  await expect(page.getByRole("tab", { name: "Doc settings" })).toHaveAttribute("aria-selected", "true");
  const quality = page.getByRole("heading", { name: "Document autofill quality" }).locator("..").locator("..");
  await expect(quality).toContainText("4 submitted claims");
  await expect(quality).toContainText("3 of 20 suggested fields");
  await expect(quality).toContainText("15.0%");
  await expect(quality).toContainText("provider name: 2");
  await quality.screenshot({ path: testInfo.outputPath("intake-quality.png") });

  const simulation = page.getByRole("heading", { name: "Test message conversation" }).locator("..").locator("..");
  await simulation.getByLabel("Claim category").selectOption("inpatient");
  await simulation.getByLabel("Test message", { exact: true }).fill("Please attach the synthetic itemised bill.");
  const firstResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/conversations/simulate");
  await simulation.getByRole("button", { name: "Add test message", exact: true }).click();
  const response = await firstResponse;
  expect(response.status()).toBe(200);
  const responseBody = await response.json();
  expect(responseBody.simulation).toBe(true);
  expect(responseBody.recipient).toBe("test.member@example.invalid");
  expect(responseBody.subject.claim_category).toBe("inpatient");
  const memberView = simulation.getByRole("region", { name: "Member view", exact: true });
  const brokerView = simulation.getByRole("region", { name: "Broker view", exact: true });
  await expect(memberView).toContainText("Claims team");
  await expect(memberView).not.toContainText("Test adviser");
  await expect(brokerView).toContainText("Test adviser");
  await expect(memberView).toContainText("Please attach the synthetic itemised bill.");

  await simulation.getByLabel("Test sender").selectOption("member");
  await simulation.getByLabel("Test message", { exact: true }).fill("This is the synthetic member response.");
  await simulation.getByRole("button", { name: "Add test message", exact: true }).click();
  await expect(memberView.getByRole("listitem")).toHaveCount(2);
  await expect(memberView).toContainText("Test member");
  await expect(brokerView).toContainText("This is the synthetic member response.");
  await simulation.screenshot({ path: testInfo.outputPath("message-simulation.png") });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await simulation.getByRole("button", { name: "Clear simulation" }).click();
  await expect(memberView).toHaveCount(0);
  await expect(brokerView).toHaveCount(0);
  await expect(simulation.getByLabel("Test message", { exact: true })).toHaveValue("");
  expect(writes.filter((path) => path.includes("/claims/") && path.endsWith("/messages"))).toEqual([]);
  expect(writes.filter((path) => /send|notification|reminder|invite|campaign/.test(path))).toEqual([]);
});

test("Conversation category and all-year scope are sent together and display claim context", async ({ page }, testInfo) => {
  await page.route("**/api/v1/conversations?*", async (route) => {
    const url = new URL(route.request().url());
    const category = url.searchParams.get("category") ?? "inpatient";
    await route.fulfill({ json: {
      total: 1, unread_total: 0, offset: 0, limit: Number(url.searchParams.get("limit") ?? 25),
      items: [{
        subject: {
          id: "synthetic-browser-claim", kind: "claim", claim_kind: category === "flex" ? "flex" : "insured",
          claim_category: category, claim_type: "Synthetic hospital visit", reference_no: "TEST-CONTEXT-1",
          policy_year_id: url.searchParams.get("policy_year_id"), policy_year_label: "2025-01-01 to 2025-12-31",
          incurred_date: "2025-05-01", amount_claimed: 50, currency: "SGD", status: "submitted",
        },
        employee: { id: "synthetic-browser-member", employee_name: "Synthetic Member", staff_id: "TEST-1" },
        message_count: 1, unread: 0,
        last_message: { id: "synthetic-browser-message", author_type: "broker", author_name: "Test adviser", subject: "Test context", body: "Synthetic context only.", created_at: "2026-09-19T01:00:00Z", mine: true, unread: false },
      }],
    } });
  });
  await page.route("**/api/v1/claims/synthetic-browser-claim/messages", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.goto("/claims/review?tab=messages");
  const inbox = page.getByRole("complementary", { name: "Conversation inbox" });
  await expect(inbox.getByText("TEST-CONTEXT-1", { exact: true })).toBeVisible();
  await expect(inbox).toContainText("Benefit year 2025-01-01 to 2025-12-31");
  const allYears = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/v1/conversations" && url.searchParams.get("all_years") === "true";
  });
  await inbox.getByLabel("Benefit years").selectOption("all");
  const yearRequest = new URL((await allYears).url());
  expect(yearRequest.searchParams.get("policy_year_id")).toBeTruthy();
  const filtered = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/v1/conversations" && url.searchParams.get("category") === "flex";
  });
  await inbox.getByLabel("Claim category").selectOption("flex");
  const filteredUrl = new URL((await filtered).url());
  expect(filteredUrl.searchParams.get("all_years")).toBe("true");
  expect(filteredUrl.searchParams.get("policy_year_id")).toBe(yearRequest.searchParams.get("policy_year_id"));
  await expect(inbox.locator("span").filter({ hasText: /^Flex$/ })).toBeVisible();
  await inbox.screenshot({ path: testInfo.outputPath("conversation-context.png") });
});

test("Servicer activity separates human and automated work and validates dates", async ({ page }, testInfo) => {
  const workloadRequests: URL[] = [];
  await page.route("**/api/v1/audit-log/claim-workload?*", async (route) => {
    workloadRequests.push(new URL(route.request().url()));
    await route.fulfill({ json: { items: [
      {
        actor_id: "test-human", actor_type: "human", name: "Synthetic Servicer",
        claims_handled: 3, actions: 5, repeat_actions: 2,
        average_decision_hours: 12.5, decision_samples: 2,
        by_status: { approved: 2, rejected: 1, needs_info: 1, paid: 1 },
      },
      {
        actor_id: "test-automation", actor_type: "automation", name: "Synthetic Review Worker",
        claims_handled: 4, actions: 4, repeat_actions: 0,
        average_decision_hours: null, decision_samples: 0,
        by_status: { ai_verified: 4 },
      },
    ] } });
  });
  await page.goto("/claims/reports?tab=claims");
  const activity = page.getByRole("region", { name: "Servicer activity", exact: true });
  await expect(activity.getByRole("row", { name: /Synthetic Servicer Human/ })).toBeVisible();
  await expect(activity.getByRole("row", { name: /Synthetic Review Worker Automation/ })).toBeVisible();
  const humanRow = activity.getByRole("row", { name: /Synthetic Servicer Human/ });
  await expect(humanRow.getByRole("cell")).toHaveText(["3", "5", "2", "1", "1", "1", "2", "12.5"]);
  await expect(activity).toContainText("not case ownership or a productivity score");
  await activity.screenshot({ path: testInfo.outputPath("servicer-activity.png") });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  // An invalid range is blocked locally rather than displaying stale metrics.
  const beforeInvalid = workloadRequests.length;
  await activity.getByLabel("From", { exact: true }).fill("2099-12-31");
  await expect(activity.getByRole("alert")).toHaveText("Choose a start date on or before the end date.");
  await expect(activity.getByRole("table")).toHaveCount(0);
  await activity.screenshot({ path: testInfo.outputPath("servicer-invalid-range.png") });
  expect(workloadRequests).toHaveLength(beforeInvalid);
  const validRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/v1/audit-log/claim-workload" && url.searchParams.get("to_date") === "2099-12-31";
  });
  await activity.getByLabel("To", { exact: true }).fill("2099-12-31");
  const restoredUrl = new URL((await validRequest).url());
  expect(restoredUrl.searchParams.get("from_date")).toBe("2099-12-31");
  await expect(humanRow).toBeVisible();
});
