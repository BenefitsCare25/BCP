import { expect, test } from "@playwright/test";

test("company notification settings save digest preferences and explain urgent delivery", async ({ page, request }) => {
  const me = await (await request.get("/api/v1/me")).json();
  await page.addInitScript(clientId => {
    localStorage.setItem("inspro-session", JSON.stringify({ state: { activeClientId: clientId, currentPolicyYearId: null, policyYearClientId: null }, version: 0 }));
  }, me.active_client_id);
  let settings = { claim_delivery: "immediate", digest_minutes: 60, revision: 0 };
  await page.route("**/api/v1/workflow-notifications/settings", async route => {
    if (route.request().method() === "PUT") {
      expect(route.request().headers()["x-inspro-client"]).toBe(me.active_client_id);
      const body = route.request().postDataJSON();
      expect(body.claim_delivery).toBe("digest");
      expect(body.digest_minutes).toBe(120);
      settings = { ...body, revision: 1 };
    }
    await route.fulfill({ json: settings });
  });
  await page.goto("/settings/company?tab=notifications");
  await page.getByLabel("Delivery", { exact: true }).selectOption("digest");
  await page.getByLabel("Digest interval (minutes)").fill("120");
  await expect(page.getByText(/Requests for information are urgent and always sent immediately/)).toBeVisible();
  await page.getByRole("button", { name: "Save notification settings" }).click();
  await expect(page.getByText("Notification settings saved.").first()).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Delivery", { exact: true })).toHaveValue("digest");
  await expect(page.getByLabel("Digest interval (minutes)")).toHaveValue("120");
});

test("digest sign-in link preserves the selected claim after authentication", async ({ page }) => {
  const claimId = "00000000-0000-0000-0000-000000000123";
  const yearId = "00000000-0000-0000-0000-000000000124";
  await page.route("**/api/v1/portal/auth/login", async route => {
    await route.fulfill({ json: {
      token: "fixture-member-token",
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      member: { id: "member", email: "member@test.invalid", staff_id: "employee", display_name: "Member" },
    } });
  });
  // The claim itself remains authorized by the server; use a 404 fixture to
  // confirm destination navigation without introducing a real claim.
  await page.route("**/api/v1/portal/**", async route => {
    if (route.request().url().endsWith("/auth/login")) return route.fallback();
    await route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto(`/portal/example/sign-in?claim=${claimId}&claim_year=${yearId}`);
  await page.locator("#portal-identifier").fill("member@test.invalid");
  await page.getByLabel("Password", { exact: true }).fill("a-test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/portal/example/claims/${claimId}$`));
  expect(await page.evaluate(() => sessionStorage.getItem("inspro-claim-period:member"))).toBe(yearId);
});
