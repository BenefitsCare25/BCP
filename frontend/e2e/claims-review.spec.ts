import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

function monitorRuntime(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      errors.push(`http ${response.status()}: ${response.url()}`);
    }
  });
  return errors;
}

async function assertAccessible(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(result.violations).toEqual([]);
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({
    path: testInfo.outputPath(`${name}.png`),
    fullPage: true,
    animations: "disabled",
  });
}

async function openClaimsReview(page: Page, tab: string) {
  await page.goto(`/claims/review?tab=${tab}`);
  await page.getByRole("combobox", { name: "Select company" }).waitFor();
  const companyGate = page.getByRole("heading", { name: "Select a company" });
  if (await companyGate.isVisible()) {
    await page.getByRole("button", { name: /CDL 2026/ }).click();
  }
}

test("signed-in administrator can use every Claims Review tab", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "queue");

  const queue = page.getByRole("tab", { name: "Queue" });
  const messages = page.getByRole("tab", { name: /Messages/ });
  const reviewRules = page.getByRole("tab", { name: "Review rules" });
  const claimSettings = page.getByRole("tab", { name: "Claim settings" });
  await expect(queue).toHaveAttribute("aria-selected", "true");
  await expect(messages).toBeVisible();
  await expect(reviewRules).toBeVisible();
  await expect(claimSettings).toBeVisible();
  await expect(page.getByText("Claims review queue")).toBeVisible();

  await messages.click();
  await expect(messages).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Messages" })).toBeVisible();
  const conversationSearch = page.getByRole("searchbox", {
    name: "Search conversations",
  });
  await expect(conversationSearch).toBeVisible();
  await expect(page.getByLabel("Conversation view")).toBeVisible();

  await reviewRules.click();
  await expect(page.getByText("AI review rules by claim type")).toBeVisible();
  await expect(page.getByText("GP (General Practitioner)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Customize" }).first().click();
  await expect(page.getByText("Use this setup")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save rules" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await screenshot(page, testInfo, "review-rules");
  await assertAccessible(page);

  await claimSettings.click();
  await expect(page.getByText("Claim document types")).toBeVisible();
  await expect(page.getByText("Discharge Summary", { exact: true })).toBeVisible();
  await screenshot(page, testInfo, "claim-settings");
  await assertAccessible(page);

  expect(runtimeErrors).toEqual([]);
});

test("message workbench supports high-volume triage", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "messages");

  const search = page.getByRole("searchbox", { name: "Search conversations" });
  await expect(search).toBeVisible();
  await search.fill("no-such-conversation");
  await expect(page.getByText("No matching conversations")).toBeVisible();
  await page.getByRole("button", { name: "Clear search field" }).click();

  await page.getByRole("button", { name: "All", exact: true }).click();
  const conversationList = page.getByRole("list", { name: "Conversations" });
  await expect(conversationList).toBeVisible();
  await screenshot(page, testInfo, "message-inbox");
  await assertAccessible(page);

  const firstConversation = conversationList.getByRole("button").first();
  if ((await firstConversation.count()) > 0) {
    await firstConversation.click();
    await expect(page.getByLabel("Selected conversation")).toBeVisible();
    await screenshot(page, testInfo, "message-thread");
    const back = page.getByRole("button", { name: "Back to message inbox" });
    if (await back.isVisible()) await back.click();
  }

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  expect(runtimeErrors).toEqual([]);
});

test("message workbench recovers when the final pagination item is replied to", async ({
  page,
}, testInfo) => {
  let total = 26;
  const conversations = Array.from({ length: 26 }, (_, index) => ({
    subject: {
      kind: "claim",
      id: `mock-${index + 1}`,
      claim_kind: "standard",
      claim_type: "Outpatient treatment",
      product_code: "GHS",
      incurred_date: "2026-08-01",
      amount_claimed: 100 + index,
      currency: "SGD",
      status: "submitted",
    },
    last_message: {
      id: `message-${index + 1}`,
      claim_id: `mock-${index + 1}`,
      enquiry_id: null,
      author_type: "member",
      author_name: `Member ${index + 1}`,
      subject: "Question about my claim",
      body: `Could you check claim ${index + 1}?`,
      event: null,
      created_at: new Date(Date.now() - (26 - index) * 60_000).toISOString(),
      mine: false,
      unread: false,
    },
    message_count: 1,
    unread: 0,
    employee: {
      id: `employee-${index + 1}`,
      staff_id: `STAFF-${String(index + 1).padStart(3, "0")}`,
      employee_name: `Member ${index + 1}`,
    },
  }));

  await page.route(/\/api\/v1\/conversations\?/, async (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 25);
    const visible = conversations.slice(0, total);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total,
        offset,
        limit,
        unread_total: 0,
        items: visible.slice(offset, offset + limit),
      }),
    });
  });

  await page.route(
    /\/api\/v1\/claims\/mock-\d+\/messages(?:\/read)?$/,
    async (route) => {
      const url = route.request().url();
      if (url.endsWith("/read")) {
        await route.fulfill({ status: 200, json: { marked: 0 } });
        return;
      }
      const claimId = url.match(/claims\/(mock-\d+)\//)?.[1] ?? "mock-26";
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          json: [
            {
              id: "message-last",
              claim_id: claimId,
              enquiry_id: null,
              author_type: "member",
              author_name: "Member 26",
              subject: "Question about my claim",
              body: "Could you check this claim?",
              event: null,
              created_at: new Date().toISOString(),
              mine: false,
              unread: false,
            },
          ],
        });
        return;
      }
      total = 25;
      await route.fulfill({
        status: 201,
        json: {
          id: "message-reply",
          claim_id: claimId,
          enquiry_id: null,
          author_type: "broker",
          author_name: "Demo Broker Admin",
          subject: "A message about your claim",
          body: "We are checking this now.",
          event: null,
          created_at: new Date().toISOString(),
          mine: true,
          unread: false,
        },
      });
    },
  );

  await openClaimsReview(page, "messages");
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("26–26 of 26")).toBeVisible();

  const lastConversation = page
    .getByRole("list", { name: "Conversations" })
    .getByRole("button")
    .first();
  if (await lastConversation.isVisible()) await lastConversation.click();

  const reply = page.getByRole("textbox", {
    name: /Write to the member/,
  });
  await screenshot(page, testInfo, "message-last-page-thread");
  await reply.fill("We are checking this now.");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByText("1–25 of 25")).toBeVisible();
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled();
  await expect(page.getByText("Sent to the member")).toBeHidden({ timeout: 10_000 });
  await assertAccessible(page);
});

test("Claims Review stays within the viewport", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "ai-extraction");
  await expect(page.getByText("AI review rules by claim type")).toBeVisible();
  await expect(page.getByText("GP (General Practitioner)", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await screenshot(page, testInfo, "review-rules-mobile");
  await assertAccessible(page);
  expect(runtimeErrors).toEqual([]);
});
