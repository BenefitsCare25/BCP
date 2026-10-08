import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

for (const role of ["portal", "hr"] as const) {
  const target = role === "portal" ? "/portal/acme/sign-in" : "/hr/sign-in?company=acme";
  test(`${role}: validates credentials and MFA, guards duplicate submits and restarts expired challenges`, async ({ page }) => {
    const loginCalls: Record<string, unknown>[] = [];
    const mfaCalls: Record<string, unknown>[] = [];
    await page.route("**/api/v1/**", async route => {
      if (route.request().url().endsWith(`/${role}/auth/login`)) {
        loginCalls.push(route.request().postDataJSON());
        await new Promise(resolve => setTimeout(resolve, 100));
        await route.fulfill({ json: { status: "mfa_required", challenge_token: "mock-challenge" } });
      } else if (route.request().url().endsWith(`/${role}/auth/mfa`)) {
        mfaCalls.push(route.request().postDataJSON());
        await route.fulfill({ status: 401, json: { detail: {
          code: "challenge_expired", message: "Your authentication challenge expired. Sign in again.",
        } } });
      } else await route.fulfill({ status: 401, json: { detail: "No session." } });
    });
    await page.goto(target);
    const identifier = page.locator(`#${role}-identifier`);
    const password = page.locator(`#${role}-password`);
    await expect(identifier).toBeVisible();
    await expect(identifier).toHaveAttribute("maxlength", "320");
    await expect(password).toHaveAttribute("maxlength", "256");
    await page.locator("form").evaluate(form => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await expect(identifier).toHaveAttribute("aria-invalid", "true");
    await expect(password).toHaveAttribute("aria-invalid", "true");
    expect(loginCalls).toHaveLength(0);
    await identifier.evaluate(input => input.removeAttribute("maxlength"));
    await password.evaluate(input => input.removeAttribute("maxlength"));
    await identifier.fill("a".repeat(321));
    await password.fill("p".repeat(257));
    await page.locator("form").evaluate(form => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(loginCalls).toHaveLength(0);
    await identifier.evaluate(input => input.setAttribute("maxlength", "320"));
    await password.evaluate(input => input.setAttribute("maxlength", "256"));
    await identifier.fill("review@example.test");
    await password.fill("review-password");
    await page.locator("form").evaluate(form => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const code = page.locator(`#${role}-totp`);
    await expect(code).toBeVisible();
    expect(loginCalls).toHaveLength(1);
    await code.fill("abcdef");
    await expect(page.getByRole("button", { name: "Verify", exact: true })).toBeDisabled();
    await page.locator("form").evaluate(form => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(mfaCalls).toHaveLength(0);
    await code.fill("A1B2-C3D4-E5F6");
    await page.getByRole("button", { name: "Verify", exact: true }).click();
    await expect(identifier).toBeVisible();
    await expect(password).toHaveValue("");
    await expect(page.getByRole("alert")).toContainText("expired");
    expect(mfaCalls).toHaveLength(1);
    expect(await page.evaluate(() => localStorage.getItem("inspro-portal-session"))).toBeNull();
    expect(await page.evaluate(() => localStorage.getItem("inspro-hr-session"))).toBeNull();
    const axe = await new AxeBuilder({ page }).analyze();
    expect(axe.violations).toEqual([]);
  });

  test(`${role}: distinguishes outages and verified invite-expiry errors`, async ({ page }) => {
    let status = 503;
    let detail: unknown = "Service unavailable.";
    await page.route("**/api/v1/**", route => route.fulfill({ status, json: { detail } }));
    await page.goto(target);
    await page.locator(`#${role}-identifier`).fill("review@example.test");
    await page.locator(`#${role}-password`).fill("review-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("couldn't reach");
    status = 401;
    detail = { code: "invite_expired", message: "This invite has expired. Ask for a new invitation." };
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("invite has expired");
  });

  test(`${role}: restores restricted sessions into MFA setup without persistent tokens`, async ({ page }) => {
    const member = { id: "review-member", staff_id: "EMP-1", email: "review@example.test", display_name: "Review" };
    const me = { user_id: "review-hr", email: "review@example.test", display_name: "Review", role: "client_hr", client_id: "acme", company_name: "Acme", mfa_available: true, mfa_required: true, mfa_status: "none" };
    const session = role === "portal"
      ? { token: "review-token", expires_at: "2100-01-01T00:00:00Z", member, mfa_enrollment_required: true }
      : { status: "authenticated", access_token: "review-token", expires_at: "2100-01-01T00:00:00Z", me, mfa_enrollment_required: true };
    const businessCalls: string[] = [];
    await page.route("**/api/v1/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/auth/refresh")) await route.fulfill({ json: session });
      else if (url.pathname.endsWith("/auth/me")) await route.fulfill({ json: me });
      else if (url.pathname.endsWith("/auth/security-status")) await route.fulfill({ json: { mfa_available: true, mfa_required: true, mfa_status: "none", mfa_enrollment_required: true } });
      // The site's brand is public (read before anyone signs in), not business data.
      else if (url.pathname.endsWith("/public/site")) await route.fulfill({ json: {} });
      else {
        businessCalls.push(url.pathname);
        await route.fulfill({ status: 403, json: { detail: "Complete setup." } });
      }
    });
    await page.goto(target);
    await expect(page).toHaveURL(/\/security/);
    await expect(page.getByRole("button", { name: role === "portal" ? "Start setup" : "Begin setup", exact: true })).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL(/\/security/);
    expect(businessCalls).toEqual([]);
    expect(await page.evaluate(() => localStorage.getItem(`inspro-${location.pathname.startsWith("/hr") ? "hr" : "portal"}-session`))).toBeNull();
  });
  test(`${role}: retries with the new token and keeps mistyped setup codes inline`, async ({ page }) => {
    let refreshes = 0;
    const headers: string[] = [];
    const me = { user_id: "review-hr", email: "review@example.test", role: "client_hr", client_id: "acme", company_name: "Acme", mfa_available: true, mfa_required: true, mfa_status: "none" };
    await page.route("**/api/v1/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/refresh")) {
        refreshes++;
        const token = refreshes === 1 ? "old-token" : "fresh-token";
        await route.fulfill({ json: role === "hr"
          ? { status: "authenticated", access_token: token, expires_at: "2100-01-01T00:00:00Z", me, mfa_enrollment_required: true }
          : { token, expires_at: "2100-01-01T00:00:00Z", member: { id: "review-member", staff_id: "EMP-1", email: "review@example.test" }, mfa_enrollment_required: true } });
      } else if (path.endsWith("/auth/me") || path.endsWith("/auth/security-status")) {
        const auth = route.request().headers().authorization;
        headers.push(auth);
        await route.fulfill(auth === "Bearer old-token"
          ? { status: 401, json: { detail: "Expired token." } }
          : { json: role === "hr" ? me : { mfa_available: true, mfa_required: true, mfa_status: "none", mfa_enrollment_required: true } });
      } else if (path.endsWith("/mfa/enroll/start")) {
        await route.fulfill({ json: { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/review?secret=JBSWY3DPEHPK3PXP" } });
      } else await route.fulfill({ status: 401, json: { detail: "That code didn't match - try again." } });
    });
    await page.goto(target);
    await page.getByRole("button", { name: role === "hr" ? "Begin setup" : "Start setup", exact: true }).click();
    expect(headers).toContain("Bearer fresh-token");
    const before = refreshes;
    await page.getByPlaceholder("123456").fill("123456");
    await page.getByRole("button", { name: role === "hr" ? "Confirm & turn on" : "Turn on two-step sign-in", exact: true }).click();
    await expect(page.getByText(/That code didn't match/)).toBeVisible();
    await expect(page).toHaveURL(/\/security/);
    expect(refreshes).toBe(before);
  });

  test(`${role}: guards password setup and offers sign-in after an expired follow-up`, async ({ page }) => {
    let calls = 0;
    await page.route("**/api/v1/**", async route => {
      if (route.request().url().endsWith("/auth/set-password")) {
        calls++;
        await new Promise(resolve => setTimeout(resolve, 100));
        await route.fulfill({ json: { status: "mfa_required", challenge_token: "mock-challenge" } });
      } else await route.fulfill({ status: 401, json: { detail: {
        code: "challenge_expired", message: "Your authentication challenge expired. Sign in again.",
      } } });
    });
    await page.goto(role === "hr" ? "/hr/set-password?token=review&company=acme" : "/portal/acme/set-password?token=review");
    await page.locator("form").evaluate(form => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(calls).toBe(0);
    await page.locator(`#${role}-new-password`).fill("Strong!Password123");
    await page.locator(`#${role}-confirm-password`).fill("Strong!Password123");
    await expect(page.locator(`#${role}-new-password`)).toHaveAttribute("maxlength", "256");
    await page.locator("form").evaluate(form => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await page.locator(`#${role}-setpw-totp`).fill("123456");
    expect(calls).toBe(1);
    await page.getByRole("button", { name: "Verify & sign in" }).click();
    await expect(page.getByRole("link", { name: "Sign in with your new password" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Verify & sign in" })).toBeDisabled();
  });
}

test("HR company remains independent across employee tabs and blocked storage", async ({ context, page }) => {
  const requests: string[] = [];
  await context.route("**/api/v1/**", async route => {
    if (route.request().url().endsWith("/hr/auth/login")) requests.push(route.request().headers()["x-inspro-tenant-slug"]);
    await route.fulfill({ status: 401, json: { detail: "Invalid credentials." } });
  });
  await context.addInitScript(() => {
    Storage.prototype.setItem = () => { throw new Error("Storage blocked for this test."); };
    Storage.prototype.getItem = () => { throw new Error("Storage blocked for this test."); };
  });
  await page.goto("/hr/sign-in?company=acme");
  await expect(page.getByText("Company: acme")).toBeVisible();
  const employee = await context.newPage();
  await employee.goto("/portal/beta/sign-in");
  await page.locator("#hr-identifier").fill("review@example.test");
  await page.locator("#hr-password").fill("review-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(requests).toEqual(["acme"]);
  await page.getByRole("button", { name: "Change company" }).click();
  await page.locator("#hr-company").fill("gamma");
  await page.locator("#hr-identifier").fill("review@example.test");
  await page.locator("#hr-password").fill("review-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(requests).toEqual(["acme", "gamma"]);
});
