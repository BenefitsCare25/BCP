import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("underwriting reminders queue safely and expose delivery history", async ({
  page,
  request,
}) => {
  const meResponse = await request.get("/api/v1/me");
  expect(meResponse.ok()).toBe(true);
  const me = await meResponse.json();
  const yearsResponse = await request.get("/api/v1/policy-years", {
    headers: { "X-Inspro-Client": me.active_client_id },
  });
  expect(yearsResponse.ok()).toBe(true);
  const [year] = await yearsResponse.json();
  expect(year?.id).toBeTruthy();
  await page.addInitScript(
    ({ clientId, yearId }) => {
      localStorage.setItem(
        "inspro-session",
        JSON.stringify({
          state: {
            activeClientId: clientId,
            currentPolicyYearId: yearId,
            policyYearClientId: clientId,
          },
          version: 0,
        }),
      );
    },
    { clientId: me.active_client_id, yearId: year.id },
  );

  const reviewId = "00000000-0000-0000-0000-000000000911";
  await page.route("**/api/v1/policy-years/*/underwriting/cases", async (route) => {
    await route.fulfill({
      json: {
        total: 1,
        open: 1,
        pending_amount: 50_000,
        items: [
          {
            id: reviewId,
            insurer: "Test Insurer",
            subject_type: "employee",
            subject_name: "Synthetic Member",
            relationship: "Employee",
            staff_id: "TASK11-1",
            identification_no: "S0000001A",
            status: "pending_employee",
            requirements: "Kept inside the authenticated workspace",
            cases: [
              {
                id: "00000000-0000-0000-0000-000000000912",
                product_id: "product-task11",
                product_code: "GTL",
                product_name: "Group Term Life",
                requested_si: 100_000,
                guaranteed_si: 50_000,
                pending_si: 50_000,
                accepted_si: 50_000,
                status: "pending",
                decided_on: null,
                remarks: null,
              },
            ],
          },
        ],
      },
    });
  });

  let history = {
    delivery_enabled: true,
    suggested_email: "member@test.invalid",
    sent_count: 0,
    last_sent_at: null as string | null,
    next_follow_up: null as string | null,
    items: [] as Array<Record<string, unknown>>,
  };
  await page.route(`**/api/v1/underwriting/reviews/${reviewId}/reminders`, async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().headers()["x-inspro-client"]).toBe(me.active_client_id);
      const body = route.request().postDataJSON();
      expect(body.recipient_email).toBe("member@test.invalid");
      expect(body.follow_up_on).toBe("2099-01-15");
      expect(JSON.stringify(body)).not.toContain("Kept inside");
      history = {
        ...history,
        next_follow_up: body.follow_up_on,
        items: [
          {
            id: body.id,
            recipient_email: body.recipient_email,
            status: "queued",
            created_at: "2026-09-19T12:00:00Z",
            sent_at: null,
            follow_up_on: body.follow_up_on,
            attempts: 0,
            last_error: null,
          },
        ],
      };
    }
    await route.fulfill({ json: history });
  });

  await page.goto("/policy-admin/underwriting");
  await page
    .getByRole("button", { name: "Open underwriting case for Synthetic Member" })
    .click();
  const reminders = page.getByRole("region", { name: "Underwriting reminders" });
  await expect(reminders.getByLabel("Recipient email")).toHaveValue(
    "member@test.invalid",
  );
  await reminders.getByLabel("Next follow-up").fill("2099-01-15");
  await reminders.getByRole("button", { name: "Queue reminder" }).click();
  await expect(page.getByText("Reminder queued. Delivery is tracked below.")).toBeVisible();
  await expect(reminders.getByRole("row", { name: /member@test.invalid queued/ })).toBeVisible();
  await expect(reminders).toContainText("Next follow-up 2099-01-15");
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
  const accessibility = await new AxeBuilder({ page })
    .include('section[aria-label="Underwriting reminders"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});
