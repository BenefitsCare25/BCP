import { expect, test } from "@playwright/test";

for (const role of ["portal", "hr"] as const) {
  const security = role === "portal" ? "/portal/acme/security" : "/hr/security?company=acme";
  const member = { id: "account-a", email: "a@example.test", staff_id: "EMP-A", display_name: "Account A" };
  const me = { user_id: "account-a", email: "a@example.test", display_name: "Account A", role: "client_hr", client_id: "acme", company_name: "Acme", mfa_available: true, mfa_required: true, mfa_status: "none" };
  const session = (token: string, required = true, other = false) => role === "portal"
    ? { token, expires_at: "2100-01-01T00:00:00Z", member: { ...member, id: other ? "account-b" : member.id }, mfa_enrollment_required: required }
    : { access_token: token, expires_at: "2100-01-01T00:00:00Z", me: { ...me, user_id: other ? "account-b" : me.user_id }, mfa_enrollment_required: required };

  test(`${role}: logout sends the tab token without refreshing another account's cookie`, async ({ page }) => {
    let refreshes = 0;
    const logoutHeaders: Record<string, string>[] = [];
    await page.route("**/api/v1/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/refresh")) {
        refreshes++;
        await route.fulfill({ json: session("account-a-token") });
      } else if (path.endsWith("/auth/me")) await route.fulfill({ json: me });
      else if (path.endsWith("/auth/security-status")) await route.fulfill({ json: { mfa_available: true, mfa_required: true, mfa_status: "none", mfa_enrollment_required: true } });
      else if (path.endsWith("/auth/logout")) {
        logoutHeaders.push(route.request().headers());
        await route.fulfill({ json: { status: "signed_out" } });
      } else await route.fulfill({ status: 404, json: {} });
    });
    await page.goto(security);
    await expect(page.getByRole("button", { name: role === "portal" ? "Start setup" : "Begin setup", exact: true })).toBeVisible();
    const before = refreshes;
    // Another tab replaces the shared cookie while this tab retains A's token.
    await page.context().addCookies([{
      name: `inspro_${role}_refresh_acme`, value: "account-b-refresh",
      url: page.url(), httpOnly: true, sameSite: "Strict",
    }]);
    await page.evaluate(async surface => {
      const path = `/src/api/${surface === "portal" ? "portalClient" : "hrClient"}.ts`;
      const client = await import(path);
      await client[surface === "portal" ? "portalApi" : "hrApi"].logout();
    }, role);
    expect(logoutHeaders).toHaveLength(1);
    expect(logoutHeaders[0].authorization).toBe("Bearer account-a-token");
    expect(logoutHeaders[0]["x-inspro-tenant-slug"]).toBe("acme");
    expect(refreshes).toBe(before);
  });

  test(`${role}: never replays a pending POST as another account or revokes its cookie`, async ({ page }) => {
    let switched = false;
    let logouts = 0;
    const attempts: string[] = [];
    await page.route("**/api/v1/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/refresh")) await route.fulfill({ json: session(switched ? "account-b-token" : "account-a-token", true, switched) });
      else if (path.endsWith("/auth/me")) await route.fulfill({ json: me });
      else if (path.endsWith("/auth/security-status")) await route.fulfill({ json: { mfa_available: true, mfa_required: true, mfa_status: "none", mfa_enrollment_required: true } });
      else if (path.endsWith("/auth/logout")) { logouts++; await route.fulfill({ json: {} }); }
      else if (path.endsWith("/review-pending-post")) {
        attempts.push(route.request().headers().authorization);
        await route.fulfill({ status: 401, json: { detail: "Expired access." } });
      } else await route.fulfill({ status: 404, json: {} });
    });
    await page.goto(security);
    await expect(page.getByRole("button", { name: role === "portal" ? "Start setup" : "Begin setup", exact: true })).toBeVisible();
    await page.route(role === "portal" ? "**/portal/acme/sign-in" : "**/hr/sign-in?*", route =>
      route.fulfill({ contentType: "text/html", body: "<h1>Sign in again</h1>" }));
    switched = true;
    await page.evaluate(async surface => {
      const clientPath = `/src/api/${surface === "portal" ? "portalClient" : "hrClient"}.ts`;
      const storePath = `/src/stores/${surface === "portal" ? "portalSession" : "hrSession"}.ts`;
      const cachePath = "/src/lib/queryClient.ts";
      const client = await import(clientPath);
      const store = await import(storePath);
      const { queryClient } = await import(cachePath);
      const key = [surface, "private-old-account"];
      queryClient.setQueryData(key, { private: "Account A data" });
      try {
        await client[surface === "portal" ? "portalApi" : "hrApi"].post(`/${surface}/review-pending-post`, { owner: "account-a" });
      } catch {
        const state = store[surface === "portal" ? "usePortalSession" : "useHrSession"].getState();
        sessionStorage.setItem("review-mismatch", JSON.stringify({ token: state.token, cached: !!queryClient.getQueryData(key) }));
      }
    }, role).catch(() => { /* The deliberate full-page sign-in navigation may destroy the context. */ });
    await expect(page.getByRole("heading", { name: "Sign in again" })).toBeVisible();
    expect(attempts).toEqual(["Bearer account-a-token"]);
    expect(logouts).toBe(0);
    expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("review-mismatch") ?? "null"))).toEqual({ token: null, cached: false });
  });

  test(`${role}: refreshes expired MFA start and retains recovery codes through focus refresh`, async ({ page }) => {
    let refreshes = 0;
    let confirmed = false;
    let expireStatus = false;
    const startHeaders: string[] = [];
    const codes = ["a1b2-c3d4-e5f6", "1234-abcd-5678"];
    await page.route("**/api/v1/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/refresh")) {
        refreshes++;
        await route.fulfill({ json: session(`token-${refreshes}`, !confirmed) });
      } else if (path.endsWith("/auth/me") || path.endsWith("/auth/security-status")) {
        if (expireStatus) {
          expireStatus = false;
          await route.fulfill({ status: 401, json: { detail: "Expired access." } });
        } else await route.fulfill({ json: role === "hr" ? { ...me, mfa_status: confirmed ? "confirmed" : "none" }
          : { mfa_available: true, mfa_required: true, mfa_status: confirmed ? "confirmed" : "none", mfa_enrollment_required: !confirmed } });
      } else if (path.endsWith("/mfa/enroll/start")) {
        const auth = route.request().headers().authorization;
        startHeaders.push(auth);
        await route.fulfill(auth === "Bearer token-1"
          ? { status: 401, json: { detail: "Expired access." } }
          : { json: { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/review?secret=JBSWY3DPEHPK3PXP" } });
      } else if (path.endsWith("/mfa/enroll/confirm")) {
        confirmed = true;
        await route.fulfill({ json: { recovery_codes: codes } });
      } else await route.fulfill({ status: 404, json: {} });
    });
    await page.goto(security);
    await page.getByRole("button", { name: role === "portal" ? "Start setup" : "Begin setup", exact: true }).click();
    await expect(page.getByPlaceholder("123456")).toBeVisible();
    expect(startHeaders).toEqual(["Bearer token-1", "Bearer token-2"]);
    await page.getByPlaceholder("123456").fill("123456");
    await page.getByRole("button", { name: role === "portal" ? "Turn on two-step sign-in" : "Confirm & turn on", exact: true }).click();
    await expect(page.getByText(codes[0], { exact: true })).toBeVisible();
    const before = refreshes;
    expireStatus = true;
    await page.evaluate(async surface => {
      const path = "/src/lib/queryClient.ts";
      const { queryClient } = await import(path);
      await queryClient.invalidateQueries({ predicate: (query: { queryKey: readonly unknown[] }) =>
        surface === "portal" ? query.queryKey[0] === "portal" : query.queryKey[0] === "hr-me" || query.queryKey[0] === "hr" });
      window.dispatchEvent(new Event("focus"));
    }, role);
    await expect.poll(() => refreshes).toBeGreaterThan(before);
    for (const code of codes) await expect(page.getByText(code, { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "I've saved them", exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/security/);
    await page.getByRole("button", { name: "I've saved them", exact: true }).click();
    await expect(page).not.toHaveURL(/\/security/);
    expect(await page.evaluate(() => localStorage.getItem("inspro-portal-session"))).toBeNull();
    expect(await page.evaluate(() => localStorage.getItem("inspro-hr-session"))).toBeNull();
  });
}
