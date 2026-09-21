import { expect, test } from "@playwright/test";

test("company settings omits notification preferences and retires old deep links", async ({ page, request }) => {
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
  await page.addInitScript(({ clientId, yearId }) => {
    localStorage.setItem("inspro-session", JSON.stringify({
      state: {
        activeClientId: clientId,
        currentPolicyYearId: yearId,
        policyYearClientId: clientId,
      },
      version: 0,
    }));
  }, { clientId: me.active_client_id, yearId: years[0].id });

  const settingsRequests: string[] = [];
  page.on("request", request => {
    if (request.url().includes("/workflow-notifications/settings")) {
      settingsRequests.push(request.url());
    }
  });

  await page.goto("/settings/company?tab=notifications");

  await expect(page.getByRole("tab", { name: "Entity aliases" })).toHaveAttribute(
    "data-state",
    "active",
  );
  await expect(page.getByRole("tab", { name: "WICA" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Authentication" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Notifications" })).toHaveCount(0);
  expect(settingsRequests).toEqual([]);
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
