import { expect, test } from "@playwright/test";

test("Home carries insurer, queue and selected company/year into claims", async ({ page }) => {
  let companyId = "";
  let yearId = "";
  let companyName = "";
  await page.route("**/api/v1/dashboard/summary*", async (route) => {
    const sourceUrl = new URL(route.request().url());
    sourceUrl.searchParams.delete("insurer");
    const response = await route.fetch({ url: sourceUrl.toString() });
    const body = await response.json();
    const company = body.companies.find((item: { current_year: unknown }) => item.current_year);
    companyId = company.id;
    yearId = company.current_year.id;
    companyName = company.name;
    Object.assign(company, {
      claims_to_review: 2, insured_claims_to_review: 1, wallet_claims_to_review: 1,
      claims_with_insurer: 2, claims_overdue: 1, messages_awaiting_reply: 1,
    });
    await route.fulfill({ response, json: { ...body, companies: [company], insurers: ["Alpha"] } });
  });
  await page.goto("/home");
  await page.getByLabel("Insurer", { exact: true }).selectOption("Alpha");
  await expect(page.getByText("Claims counts match Alpha.", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "Pending Insurer", exact: true }).click();
  const requestPromise = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/v1/claims" && url.searchParams.get("queue") === "insurer";
  });
  await page.getByRole("button", { name: `Open Pending Insurer for ${companyName}`, exact: true }).click();
  const request = await requestPromise;
  const url = new URL(request.url());
  expect(url.searchParams.get("insurer")).toBe("Alpha");
  expect(url.searchParams.get("policy_year_id")).toBe(yearId);
  expect(request.headers()["x-inspro-client"]).toBe(companyId);
  await expect(page).toHaveURL(/queue=insurer/);
  await expect(page).toHaveURL(/insurer=Alpha/);
});

test("Home count links select review kind, overdue or conversations awaiting us", async ({ page }) => {
  let companyName = "";
  await page.route("**/api/v1/dashboard/summary*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const company = body.companies.find((item: { current_year: unknown }) => item.current_year);
    companyName = company.name;
    Object.assign(company, {
      claims_to_review: 2, insured_claims_to_review: 1, wallet_claims_to_review: 1,
      claims_with_insurer: 2, claims_overdue: 1, messages_awaiting_reply: 1,
    });
    await route.fulfill({ response, json: { ...body, companies: [company] } });
  });
  await page.goto("/home");
  await page.getByRole("tabpanel").getByRole("button", { name: /Open insurer claims awaiting review/ }).click();
  await expect(page).toHaveURL(/queue=review/);
  await expect(page).toHaveURL(/kind=insured/);

  await page.goto("/home");
  await page.getByRole("tab", { name: "Pending Insurer", exact: true }).click();
  await page.getByRole("button", { name: `Open overdue insurer claims for ${companyName}` }).click();
  await expect(page).toHaveURL(/queue=overdue/);

  await page.goto("/home");
  await page.getByRole("tab", { name: "New Message", exact: true }).click();
  await page.getByRole("button", { name: `Open New Message for ${companyName}` }).click();
  await expect(page).toHaveURL(/tab=messages/);
  await expect(page).toHaveURL(/awaiting=us/);
});
