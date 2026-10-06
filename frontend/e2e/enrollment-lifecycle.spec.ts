import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const event = {
  id: "notice-1", enrollment_id: "review-enrollment", kind: "returned",
  title: "Enrolment returned for correction", message: "Your choices are saved. Review the reason, make corrections, then sign and send again before the deadline.",
  reason: "Please review your family cover before signing again.",
  created_at: "2026-10-06T01:25:00Z", read_at: null as string | null,
  email_status: "unavailable", email_detail: "Email delivery is not configured.",
  window_name: "Annual benefits review", closes_at: "2035-10-28T23:59:59Z",
};

test("employee sees durable enrolment notices and cancelled form standing", async ({ page }, info) => {
  let read = false;
  let unavailable = false;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/refresh")) return route.fulfill({ json: {
      token: "review-token", expires_at: "2100-01-01T00:00:00Z", member: {
        id: "review-member", staff_id: "REVIEW-1", email: "review@example.test", display_name: "Review Member",
      },
    } });
    if (path.endsWith("/enrollment/notices/notice-1/read")) {
      read = true;
      return route.fulfill({ json: { read: true } });
    }
    if (path.endsWith("/enrollment/notices")) return route.fulfill(unavailable
      ? { status: 503, json: { detail: "Unavailable" } }
      : { json: { items: [{ ...event, read_at: read ? event.created_at : null }], unread: read ? 0 : 1 } });
    if (path.endsWith("/portal/enrollment")) return route.fulfill({ json: { window: null, enrollment: null, options: null } });
    if (path.endsWith("/portal/dependants")) return route.fulfill({ json: [] });
    if (path.endsWith("/portal/enrollment-forms")) return route.fulfill({ json: [{
      id: "form-1", reference_no: "EF-2026-REVIEW", version: 1, source: "portal", status: "cancelled",
      submitted_at: event.created_at, signature_name: "Review Member", acknowledged_at: null,
      has_pdf: false, enrollment_status: "not_started",
    }] });
    if (path.endsWith("/auth/security-status")) return route.fulfill({ json: { mfa_available: false, mfa_status: "none" } });
    return route.fulfill({ status: 404, json: { detail: "No active coverage." } });
  });
  await page.goto("/portal/acme/enrollment");
  await expect(page.getByText("Enrolment returned for correction", { exact: false })).toBeVisible();
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^Notifications/ }).click();
  const notices = page.getByRole("dialog", { name: "Notifications" });
  await expect(notices).toContainText(event.reason);
  await notices.getByRole("button", { name: "Mark as read" }).click();
  await expect(notices.getByRole("button", { name: "Mark as read" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(notices).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("Enrolment returned for correction", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mark as read" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("employee-enrolment-notice.png"), fullPage: true });
  unavailable = true;
  await page.reload();
  await expect(page.getByRole("button", { name: "Enrolment updates unavailable. Retry" })).toBeVisible();
  unavailable = false;
  await page.getByRole("button", { name: "Enrolment updates unavailable. Retry" }).click();
  await expect(page.getByText(event.reason, { exact: false })).toBeVisible();
  expect(errors).toEqual([]);
});

test("broker returns with a reason, sees delivery status, and cannot silently edit a signature", async ({ page, request }, info) => {
  const me = await (await request.get("/api/v1/me")).json();
  const client = me.accessible_clients[0];
  const years = await (await request.get("/api/v1/policy-years", { headers: { "X-Inspro-Client": client.id } })).json();
  const year = years[0];
  await page.addInitScript(({ clientId, yearId }) => localStorage.setItem("inspro-session", JSON.stringify({
    state: { activeClientId: clientId, currentPolicyYearId: yearId, policyYearClientId: clientId }, version: 0,
  })), { clientId: client.id, yearId: year.id });
  let returned = false;
  let fail = true;
  let calls = 0;
  const detail = () => ({
    id: "review-enrollment", window_id: "review-period", policy_year_id: year.id,
    employee_id: "review-employee", staff_id: "REVIEW-1", employee_name: "Review Member",
    status: returned ? "returned" : "submitted", baseline_snapshot: null,
    submitted_at: event.created_at, confirmed_at: null, elections: [], leave: null,
    compulsory_product_codes: [],
  });
  await page.route(`**/api/v1/policy-years/${year.id}/enrollment-windows`, route => route.fulfill({ json: [{
    id: "review-period", policy_year_id: year.id, name: "Annual benefits review", window_type: "open",
    status: "open", phase: "open", opens_at: "2020-01-01T00:00:00Z", closes_at: event.closes_at,
    default_behavior: "deemed_keep_current", allow_plan_change: true, allow_leave: false,
    allow_dependant_changes: false, member_self_service: true, uses_flex: false, product_scope: null,
  }] }));
  await page.route("**/api/v1/enrollment-windows/review-period/progress", route => route.fulfill({ json: {
    total: 1, submitted: returned ? 0 : 1, returned: returned ? 1 : 0, confirmed: 0, deemed: 0, in_progress: 0, not_started: 0, declined: 0, not_in_period: 0,
  } }));
  await page.route("**/api/v1/enrollment-windows/review-period/enrollments?*", route => route.fulfill({ json: { items: [detail()], total: 1, offset: 0, limit: 50 } }));
  await page.route("**/api/v1/enrollments/review-enrollment", route => route.fulfill({ json: detail() }));
  await page.route("**/api/v1/enrollments/review-enrollment/options", route => route.fulfill({ json: { products: [], leave: null, flex_wallet: null } }));
  await page.route("**/api/v1/enrollments/review-enrollment/events", route => route.fulfill({ json: returned ? [event] : [] }));
  await page.route("**/api/v1/enrollments/review-enrollment/return", route => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ reason: event.reason });
    if (fail) return route.fulfill({ status: 503, json: { detail: "Temporary review failure. Please retry." } });
    returned = true;
    return route.fulfill({ json: detail() });
  });
  await page.goto("/client-relations/enrollment?tab=members&window=review-period&member=review-enrollment");
  await expect(page.getByRole("button", { name: "Return for correction", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cancel and clear choices", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Return for correction", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Return for correction?" });
  await expect(dialog.getByRole("button", { name: "Return and notify" })).toBeDisabled();
  await dialog.getByRole("textbox", { name: "Reason for the employee" }).fill(event.reason);
  const accessibility = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("broker-return-reason.png"), fullPage: true });
  await dialog.getByRole("button", { name: "Return and notify" }).click();
  await expect(dialog.getByRole("textbox")).toHaveValue(event.reason);
  fail = false;
  await dialog.getByRole("button", { name: "Return and notify" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Email delivery is not configured.", { exact: true })).toBeVisible();
  await expect(page.getByText("Enrolment returned for correction", { exact: true })).toBeVisible();
  expect(calls).toBe(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("broker-return-activity.png"), fullPage: true });
});
