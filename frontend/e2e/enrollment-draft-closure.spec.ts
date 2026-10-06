import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function draftPage(page: Page) {
  await page.clock.install({ time: new Date("2026-10-06T02:00:00Z") });
  const state = { closed: false, unavailable: false, expireNotice: false, passiveRefreshes: 0, writes: 0 };
  const window = { id: "period", name: "Annual enrolment", status: "open", window_type: "open",
    opens_at: "2026-10-01T00:00:00Z", closes_at: "2026-10-06T03:00:00Z",
    allow_leave: true, allow_dependant_changes: false, member_self_service: true,
    allow_overdraft: false, product_scope: null, default_behavior: "deemed_keep_current" };
  const enrollment = { id: "draft", window_id: "period", status: "not_started", latest_event_id: null,
    elections: [], leave: null, baseline_snapshot: null, compulsory_product_codes: [] };
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/refresh")) {
      if (request.headers()["x-inspro-session-activity"] === "passive") state.passiveRefreshes++;
      return route.fulfill({ json: { token: "draft-token", expires_at: "2100-01-01T00:00:00Z",
        member: { id: "member", staff_id: "DRAFT-1", display_name: "Draft Member" } } });
    }
    if (path.endsWith("/enrollment/notices")) {
      expect(request.headers()["x-inspro-session-activity"]).toBe("passive");
      if (state.expireNotice) { state.expireNotice = false; return route.fulfill({ status: 401, json: { detail: "Expired" } }); }
      return route.fulfill({ json: { items: [], unread: 0 } });
    }
    if (path.endsWith("/enrollment/state/draft")) {
      expect(request.headers()["x-inspro-session-activity"]).toBe("passive");
      return route.fulfill(state.unavailable ? { status: 503, json: { detail: "Unavailable" } } : { json: {
        window_status: state.closed ? "closed" : "open", opens_at: window.opens_at,
        closes_at: window.closes_at, member_self_service: true, latest_event_id: null,
      } });
    }
    if (path.endsWith("/portal/enrollment")) return route.fulfill({ json: { window, enrollment, options: {
      products: [], flex_wallet: null, member_leave_rate: 100, leave: { allow_buy: true, allow_sell: true,
        min_buy_days: 0, max_buy_days: 5, min_sell_days: 0, max_sell_days: 5,
        increment_days: 1, sell_eligible: true },
    } } });
    if (path.endsWith("/enrollment/form")) return route.fulfill({ json: {
      company_name: "Review", title: "Enrolment", closes_at: window.closes_at,
      policy_start: window.opens_at, policy_end: window.closes_at, window_type: "open",
      intro_lines: [], eligibility_notes: [], clauses: [], documents: [], rules: [], compulsory: [],
      contributions: [], plans: [], dependants: [], latest: null,
      particulars: { name: "Draft Member", staff_id: "DRAFT-1", id_masked: "****", email: "draft@example.test", contact_no: "" },
    } });
    if (path.endsWith("/enrollment/draft") || path.endsWith("/enrollment/sign")) {
      state.writes++;
      return route.fulfill({ status: 409, json: { detail: "Period closed" } });
    }
    if (path.endsWith("/portal/dependants") || path.endsWith("/portal/enrollment-forms")) return route.fulfill({ json: [] });
    if (path.endsWith("/auth/security-status")) return route.fulfill({ json: { mfa_available: false, mfa_status: "none" } });
    return route.fulfill({ status: 404, json: { detail: "No record" } });
  });
  await page.goto("/portal/acme/enrollment");
  await expect(page.getByRole("textbox", { name: "Email", exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("No autosave.", { exact: false })).toBeVisible();
  return state;
}

test("half-filled form survives passive checks, warns on leaving, and locks at deadline", async ({ page }, info) => {
  const state = await draftPage(page);
  const email = page.getByRole("textbox", { name: "Email", exact: true });
  await email.fill("changed@example.test");
  await page.clock.fastForward(31000);
  await expect(email).toHaveValue("changed@example.test");
  expect(state.writes).toBe(0);
  const dialog = page.waitForEvent("dialog").then(async warning => {
    expect(warning.message()).toContain("Unsaved entries");
    await warning.dismiss();
  });
  await Promise.all([dialog, page.getByRole("link", { name: "Home", exact: true }).first().click()]);
  await expect(email).toHaveValue("changed@example.test");
  await page.clock.setSystemTime(new Date("2026-10-06T03:00:01Z"));
  await page.clock.fastForward(1100);
  await expect(page.getByText("This period is not accepting changes.", { exact: false })).toBeVisible();
  await expect(email).toBeDisabled();
  await expect(email).toHaveValue("changed@example.test");
  await page.getByRole("tab", { name: "Sign and send", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign and send", exact: true })).toHaveCount(0);
  expect(state.writes).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("deadline-partial-form.png"), fullPage: true });
});

test("early closure and status failure lock the form; passive refresh stays passive", async ({ page }) => {
  const state = await draftPage(page);
  const email = page.getByRole("textbox", { name: "Email", exact: true });
  await email.fill("retained@example.test");
  state.expireNotice = true;
  state.unavailable = true;
  await page.clock.fastForward(31000);
  await expect.poll(() => state.passiveRefreshes).toBe(1);
  await expect(page.getByText("We couldn't check whether", { exact: false })).toBeVisible();
  await expect(email).toBeDisabled();
  state.unavailable = false;
  state.closed = true;
  await page.getByRole("button", { name: "Check enrolment status" }).click();
  await expect(page.getByText("This period is not accepting changes.", { exact: false })).toBeVisible();
  await expect(email).toHaveValue("retained@example.test");
  await expect(email).toBeDisabled();
  expect(state.writes).toBe(0);
});

test("a save racing closure preserves unsent entries and immediately checks status", async ({ page }) => {
  const state = await draftPage(page);
  await page.getByRole("textbox", { name: "Email", exact: true }).fill("unsent@example.test");
  await page.getByRole("tab", { name: "Leave", exact: true }).click();
  await page.getByLabel("What would you like to do").selectOption("sell");
  await page.getByLabel("How many days").fill("2");
  await page.getByRole("tab", { name: "Review and send", exact: false }).click();
  state.closed = true;
  await page.getByRole("button", { name: "Save choices", exact: true }).click();
  await expect(page.getByText("This period is not accepting changes.", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "Your details", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Email", exact: true })).toHaveValue("unsent@example.test");
  await expect(page.getByRole("textbox", { name: "Email", exact: true })).toBeDisabled();
  expect(state.writes).toBe(1);
});
