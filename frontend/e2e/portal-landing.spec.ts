import { expect, test } from "@playwright/test";

for (const role of ["portal", "hr"] as const) {
  for (const flow of ["password", "mfa", "set-password", "set-password-mfa", "refresh"] as const) {
    test(`${role}: ${flow} lands on its home page`, async ({ page }, testInfo) => {
      const errors: string[] = [];
      page.on("pageerror", error => errors.push(error.message));
      const me = {
        user_id: "landing-hr", email: "landing@example.test", display_name: "Landing Review",
        role: "client_hr", client_id: "acme", company_name: "Acme",
        mfa_available: true, mfa_status: "confirmed",
      };
      const session = role === "portal"
        ? { token: "landing-token", expires_at: "2100-01-01T00:00:00Z", member: {
          id: "landing-member", staff_id: "LANDING-1", email: me.email, display_name: me.display_name,
        } }
        : { status: "authenticated", access_token: "landing-token", expires_at: "2100-01-01T00:00:00Z", me };
      await page.route("**/api/v1/**", async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith("/auth/refresh")) {
          await route.fulfill(flow === "refresh" ? { json: session } : { status: 401, json: { detail: "No session." } });
        } else if (path.endsWith("/auth/login") || path.endsWith("/auth/set-password")) {
          await route.fulfill({ json: flow.includes("mfa")
            ? { status: "mfa_required", challenge_token: "landing-challenge" } : session });
        } else if (path.endsWith("/auth/mfa")) {
          await route.fulfill({ json: session });
        } else if (path.endsWith("/auth/me")) {
          await route.fulfill({ json: me });
        } else if (path.endsWith("/auth/security-status")) {
          await route.fulfill({ json: { mfa_available: true, mfa_status: "confirmed" } });
        } else {
          // A newly invited employee may have no coverage yet; Home still opens.
          await route.fulfill({ status: 404, json: { detail: "No active coverage." } });
        }
      });
      const root = role === "portal" ? "/portal/acme" : "/hr";
      const settingPassword = flow.startsWith("set-password");
      await page.goto(`${root}/${settingPassword ? "set-password?token=landing&" : "sign-in?"}company=acme`);
      if (settingPassword) {
        await page.locator(`#${role}-new-password`).fill("Landing!Password123");
        await page.locator(`#${role}-confirm-password`).fill("Landing!Password123");
        await page.getByRole("button", { name: "Set password & sign in" }).click();
      } else if (flow !== "refresh") {
        await page.locator(`#${role}-identifier`).fill(me.email);
        await page.locator(`#${role}-password`).fill("Landing!Password123");
        await page.getByRole("button", { name: "Sign in", exact: true }).click();
      }
      if (flow.includes("mfa")) {
        await page.locator(`#${role}-${settingPassword ? "setpw-" : ""}totp`).fill("123456");
        await page.getByRole("button", { name: settingPassword ? "Verify & sign in" : "Verify", exact: true }).click();
      }
      await expect(page).toHaveURL(role === "portal" ? /\/portal\/acme\/?$/ : /\/hr\/dashboard$/);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toContainText(role === "portal" ? "Landing" : "Welcome, Landing Review");
      if (role === "hr") {
        await expect(page.getByText("Your workspace", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("region", { name: "HR services" })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Claims", level: 2, exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Enrolment forms", level: 2, exact: true })).toBeVisible();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(errors).toEqual([]);
      if (flow === "password") await page.screenshot({ path: testInfo.outputPath(`${role}-home.png`), fullPage: true, animations: "disabled" });
    });
  }
}
