import { expect, test } from "@playwright/test";

function calendarDateInZone(timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

test("Home keeps stale matching work visible and treats renewals as calendar dates", async ({
  browser,
  baseURL,
}) => {
  const timeZone = "America/Los_Angeles";
  const today = calendarDateInZone(timeZone);
  const year = today.slice(0, 4);
  const context = await browser.newContext({ baseURL, timezoneId: timeZone });
  const page = await context.newPage();

  await page.route("**/api/v1/dashboard/summary*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        firm: {
          company_count: 1,
          member_count: 12,
          dependant_count: 3,
          claims_to_review: 0,
          verification_pending: 0,
          insured_claims_to_review: 0,
          wallet_claims_to_review: 0,
          claims_with_insurer: 0,
          claims_overdue: 0,
          messages_awaiting_reply: 0,
          dependants_pending: 0,
          employees_unmatched: 0,
          underwriting_pending: 0,
          windows_open: 0,
        },
        companies: [
          {
            id: "00000000-0000-0000-0000-0000000000aa",
            name: "Stale Matching Co",
            current_year: {
              id: "00000000-0000-0000-0000-0000000000ab",
              year: Number(year),
              status: "active",
              start_date: `${year}-01-01`,
              end_date: today,
            },
            member_count: 12,
            dependant_count: 3,
            claims_to_review: 0,
            verification_pending: 0,
            insured_claims_to_review: 0,
            wallet_claims_to_review: 0,
            claims_with_insurer: 0,
            claims_overdue: 0,
            messages_awaiting_reply: 0,
            dependants_pending: 0,
            employees_unmatched: 0,
            matching_stale: true,
            underwriting_pending: 0,
            enrollment_open: false,
            enrollment_closes_at: null,
          },
        ],
      },
    });
  });

  try {
    await page.goto("/home");

    const renewalMetric = page
      .getByText("Upcoming Renewal · 30 days", { exact: true })
      .locator("../..");
    await expect(renewalMetric.getByText("1", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "Member matching" }).click();
    const staleRow = page
      .getByRole("tabpanel", { name: "Member matching" })
      .getByRole("row")
      .filter({ hasText: "Stale Matching Co" });
    await expect(staleRow).toBeVisible();
    await expect(staleRow).toContainText("Matching stale");
    await expect(staleRow).toContainText("0");
  } finally {
    await context.close();
  }
});
