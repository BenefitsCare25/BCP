import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const session = {
  access_token: "broker-memory-only-token", expires_at: "2100-01-01T00:00:00Z",
  user: { id: "broker-review", email: "broker@example.test", display_name: "Review Broker" },
  mfa_verified: false,
  mfa_required: true,
};

async function broker(page: Page, enrolled = false, required = true) {
  await page.route("**/src/auth/msal.ts*", route => route.fulfill({
    contentType: "application/javascript",
    body: `
      import { brokerAccount, useBrokerSession } from '/src/stores/brokerSession.ts';
      import { brokerAccessToken, brokerAuthRequest, refreshBrokerSession } from '/src/auth/brokerSession.ts';
      export const ENTRA_ENABLED = true;
      export const getMsal = () => null;
      export const getActiveAccount = brokerAccount;
      export const acquireAccessToken = brokerAccessToken;
      export const initializeMsal = async () => { await refreshBrokerSession(); return null; };
      export const clearLocalSession = async () => useBrokerSession.getState().set(null);
      export const signIn = async () => {};
      export const signOut = async () => {
        const token = useBrokerSession.getState().session?.access_token;
        await brokerAuthRequest('/logout', {}, token);
        useBrokerSession.getState().set(null);
        window.location.assign('/sign-in');
      };
    `,
  }));
  let current = { ...session, mfa_required: required };
  let ended = false;
  await page.route("**/api/v1/broker/auth/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/logout")) { ended = true; await route.fulfill({ status: 204 }); return; }
    if (ended) { await route.fulfill({ status: 401, json: { detail: "Session ended." } }); return; }
    if (path.endsWith("/refresh")) { await route.fulfill({ json: current }); return; }
    if (path.endsWith("/mfa")) { await route.fulfill({ json: { status: enrolled ? "confirmed" : "none", verified: current.mfa_verified } }); return; }
    if (path.endsWith("/start")) { await route.fulfill({ json: { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Review?secret=JBSWY3DPEHPK3PXP" } }); return; }
    if (path.endsWith("/confirm") || path.endsWith("/verify")) {
      const body = route.request().postDataJSON() as { code: string };
      if (body.code !== "123456") { await route.fulfill({ status: 401, json: { detail: "That code did not match. Try again." } }); return; }
      current = { ...current, mfa_verified: true };
      await route.fulfill({ json: { ...current, ...(path.endsWith("/confirm") ? { recovery_codes: ["aaaa-bbbb-cccc", "dddd-eeee-ffff"] } : {}) } });
    }
  });
  await page.route("**/api/v1/me", route => {
    expect(route.request().headers().authorization).toBe("Bearer broker-memory-only-token");
    return route.fulfill({ json: {
    user_id: session.user.id, email: session.user.email, display_name: session.user.display_name,
    role: "broker_admin", broker_firm_id: "review-firm", active_client_id: null, accessible_clients: [],
    } });
  });
  return { expire: () => { ended = true; } };
}

test("broker session: an account with authenticator off can enter without a code", async ({ page }) => {
  await broker(page, true, false);
  await page.goto("/home");
  await expect(page).toHaveURL(/\/home$/);
  await expect(page.getByRole("heading", { name: "Secure your broker account" })).toHaveCount(0);
  await page.goto("/broker/security");
  await expect(page.getByText(/Your administrator has not required an authenticator/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Set up authenticator" })).toHaveCount(0);
  await expect(page.getByLabel("Authenticator or recovery code")).toHaveCount(0);
  await page.getByRole("button", { name: "Continue to broker platform" }).click();
  await expect(page).not.toHaveURL(/\/broker\/security/);
});

async function expireLocalAccess(page: Page) {
  await page.evaluate(async () => {
    const { useBrokerSession } = await import(/* @vite-ignore */ "/src/stores/brokerSession.ts");
    const current = useBrokerSession.getState().session;
    useBrokerSession.getState().set({ ...current, expires_at: "2000-01-01T00:00:00Z" });
  });
}

for (const operation of ["start", "confirm", "verify", "status-retry", "server-expiry"] as const) {
  test(`broker session: expired MFA ${operation} returns to sign-in`, async ({ page }) => {
    const account = await broker(page, operation === "verify");
    let statusCalls = 0;
    if (operation === "status-retry") {
      await page.route("**/api/v1/broker/auth/mfa", async route => {
        statusCalls++;
        await route.fulfill({ status: 503, json: { detail: "Temporarily unavailable." } });
      });
    }
    await page.goto("/broker/security");
    if (operation === "confirm") {
      await page.getByRole("button", { name: "Set up authenticator" }).click();
      await page.getByLabel("Six-digit authenticator code").fill("123456");
    } else if (operation === "verify") {
      await page.getByLabel("Authenticator or recovery code").fill("123456");
    } else if (operation === "status-retry") {
      await expect(page.getByRole("button", { name: "Try again", exact: true })).toBeVisible();
    } else await expect(page.getByRole("button", { name: "Set up authenticator" })).toBeVisible();
    account.expire();
    if (operation !== "server-expiry") await expireLocalAccess(page);
    await page.getByRole("button", { name: operation === "status-retry" ? "Try again"
      : operation === "confirm" || operation === "verify" ? "Verify and continue" : "Set up authenticator", exact: true }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
    await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
    if (operation === "status-retry") expect(statusCalls).toBe(1);
  });
}

for (const storageBlocked of [false, true]) {
test(`broker real MSAL: explicit logout stays signed out while another tab retains its account (${storageBlocked ? "marker storage blocked" : "tab storage"})`, async ({ page, context, baseURL }) => {
  if (storageBlocked) await page.addInitScript(() => {
    for (const method of ["getItem", "setItem", "removeItem"] as const) {
      const original = Storage.prototype[method];
      Storage.prototype[method] = function(key: string, value?: string) {
        if (key === "inspro-broker-signed-out") throw new DOMException("Storage blocked", "SecurityError");
        return original.call(this, key, value!);
      } as typeof original;
    }
  });
  const other = await context.newPage();
  for (const tab of [page, other]) {
    await tab.route("**/src/auth/msal.ts*", async route => {
      const response = await route.fetch();
      const source = (await response.text())
        .replace(/const tenantId = .*?;/, "const tenantId = '11111111-1111-4111-8111-111111111111';")
        .replace(/const clientId = .*?;/, "const clientId = '22222222-2222-4222-8222-222222222222';");
      await route.fulfill({ response, body: source });
    });
  }
  let loggedOut = false;
  let refreshesAfterLogout = 0;
  await context.route("**/api/v1/broker/auth/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/logout")) {
      expect(route.request().headers().authorization).toBe("Bearer broker-memory-only-token");
      loggedOut = true;
      await route.fulfill({ status: 204 });
    } else if (path.endsWith("/refresh")) {
      if (loggedOut && route.request().frame().page() === page) refreshesAfterLogout++;
      const otherCookie = (route.request().headers().cookie ?? "").includes("inspro_broker_refresh=other-family");
      await route.fulfill({ json: otherCookie ? { ...session, access_token: "other-broker-token",
        user: { id: "other-broker", email: "other@example.test", display_name: "Other Broker" } } : session });
    } else await route.fulfill({ json: { status: "none", verified: false } });
  });
  await page.goto("/broker/security");
  await expect(page.getByText(session.user.email, { exact: true })).toBeVisible();
  await context.addCookies([{ name: "inspro_broker_refresh", value: "other-family", url: baseURL!, httpOnly: true, sameSite: "Strict" }]);
  await other.goto("/broker/security");
  await expect(other.getByText("other@example.test", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/sign-in(?:\?signed_out=1)?$/);
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
  expect(refreshesAfterLogout).toBe(0);
  expect((await context.cookies()).find(cookie => cookie.name === "inspro_broker_refresh")?.value).toBe("other-family");
  await other.reload();
  await expect(other.getByText("other@example.test", { exact: true })).toBeVisible();
  await other.close();
});
}

test("broker session: unverified user is restricted to accessible MFA setup and saves recovery codes", async ({ page }, testInfo) => {
  await broker(page);
  await page.goto("/home");
  await expect(page).toHaveURL(/\/broker\/security$/);
  await expect(page.getByRole("heading", { name: "Secure your broker account" })).toBeVisible();
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  await page.screenshot({ path: testInfo.outputPath("broker-mfa-enrollment.png"), fullPage: true });
  const enrollment = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(enrollment.violations).toEqual([]);
  const submit = page.getByRole("button", { name: "Verify and continue" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("Six-digit authenticator code").fill("000000");
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("did not match");
  await page.getByLabel("Six-digit authenticator code").fill("123456");
  await submit.click();
  await expect(page.getByRole("heading", { name: "Save your recovery codes" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("broker-recovery-codes.png"), fullPage: true });
  await expect(page).toHaveURL(/\/broker\/security$/);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }).includes("broker-memory-only-token"))).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(scan.violations).toEqual([]);
  await page.getByRole("button", { name: "I've saved them" }).click();
  await expect(page).toHaveURL(/\/home$/);
});

test("broker session: restored enrolled account verifies and logout uses this tab's bearer", async ({ page }) => {
  await broker(page, true);
  await page.goto("/broker/security");
  await page.getByLabel("Authenticator or recovery code").fill("123456");
  await page.getByRole("button", { name: "Verify and continue" }).click();
  await expect(page).toHaveURL(/\/home$/);
  await page.getByRole("button", { name: "Account menu for Review Broker" }).click();
  const logout = page.waitForRequest(request => request.url().endsWith("/broker/auth/logout"));
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  expect((await logout).headers().authorization).toBe("Bearer broker-memory-only-token");
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("broker MSAL configuration uses supported redirect storage", async ({ page }) => {
  await page.goto("/sign-in");
  const config = await page.evaluate(async () => {
    // Inspect the real application module, independently of the mocked MFA flows.
    const module = await import(/* @vite-ignore */ "/src/auth/msal.ts");
    return module.msalConfig.cache.cacheLocation;
  });
  expect(config).toBe("sessionStorage");
});

test("broker cookie writers use the same browser lock", async ({ page }) => {
  await page.goto("/sign-in");
  const operations = await page.evaluate(async () => {
    const module = await import(/* @vite-ignore */ "/src/auth/brokerSession.ts");
    const locks: string[] = [];
    const realFetch = window.fetch;
    const realRequest = navigator.locks.request.bind(navigator.locks);
    // Observe actual requests to the browser's lock manager, not a source-string check.
    navigator.locks.request = (async (name: string, callback: (lock: Lock) => unknown) => {
      locks.push(name);
      return await realRequest(name, callback);
    }) as typeof navigator.locks.request;
    window.fetch = async () => new Response("{}", { headers: { "Content-Type": "application/json" } });
    try {
      await Promise.all(["/exchange", "/refresh", "/logout", "/mfa/confirm", "/mfa/verify"].map(
        path => module.brokerAuthRequest(path, {}, "synthetic-broker-token"),
      ));
      return locks;
    } finally { window.fetch = realFetch; navigator.locks.request = realRequest; }
  });
  expect(operations).toEqual(Array(5).fill("inspro-refresh:broker"));
});

for (const outcome of ["allowed", "refused"] as const) {
test(`broker real MSAL: PKCE ${outcome} callback clears tokens and permits account switching`, async ({ page, baseURL }) => {
  await page.addInitScript(() => sessionStorage.setItem("inspro-broker-signed-out", "1"));
  const tenant = "11111111-1111-4111-8111-111111111111";
  const client = "22222222-2222-4222-8222-222222222222";
  const authority = `https://login.microsoftonline.com/${tenant}`;
  let nonce = "";
  let exchanged = false;
  let tokenCalls = 0;
  let authorizeCalls = 0;
  let exchangeCalls = 0;
  await page.route("**/src/auth/msal.ts*", async route => {
    const response = await route.fetch();
    const source = (await response.text())
      .replace(/const tenantId = .*?;/, `const tenantId = '${tenant}';`)
      .replace(/const clientId = .*?;/, `const clientId = '${client}';`)
      .replace(/const audience = .*?;/, `const audience = 'api://${client}';`);
    await route.fulfill({ response, body: source });
  });
  await page.route("https://login.microsoftonline.com/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.includes(".well-known")) {
      await route.fulfill({ json: {
        issuer: authority + "/v2.0", authorization_endpoint: authority + "/oauth2/v2.0/authorize",
        token_endpoint: authority + "/oauth2/v2.0/token", end_session_endpoint: authority + "/oauth2/v2.0/logout",
        jwks_uri: authority + "/discovery/v2.0/keys",
      } });
    } else if (url.pathname.endsWith("/authorize")) {
      authorizeCalls++;
      if (outcome === "refused" && authorizeCalls > 1) {
        expect(url.searchParams.get("prompt")).toBe("select_account");
        await route.fulfill({ contentType: "text/html", body: "<main>Choose Microsoft account</main>" });
        return;
      }
      nonce = url.searchParams.get("nonce") ?? "";
      expect(url.searchParams.get("code_challenge_method")).toBe("S256");
      expect(url.searchParams.get("response_type")).toBe("code");
      const state = encodeURIComponent(url.searchParams.get("state") ?? "");
      const callback = url.searchParams.get("redirect_uri");
      await route.fulfill({ status: 302, headers: { location: `${callback}#code=review-code&state=${state}` }, body: "" });
    } else if (url.pathname.endsWith("/token")) {
      tokenCalls++;
      const body = new URLSearchParams(route.request().postData() ?? "");
      expect(body.get("code_verifier")).toBeTruthy();
      const now = Math.floor(Date.now() / 1000);
      const encoded = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
      const idToken = `${encoded({ alg: "RS256", typ: "JWT" })}.${encoded({
        iss: authority + "/v2.0", aud: client, tid: tenant, oid: "review-oid", sub: "review-sub",
        nonce, iat: now, nbf: now, exp: now + 3600, preferred_username: "broker@example.test",
      })}.synthetic-signature`;
      await route.fulfill({ json: {
        token_type: "Bearer", scope: `api://${client}/access_as_user`, expires_in: 3600,
        access_token: "synthetic-microsoft-access", refresh_token: "synthetic-microsoft-refresh",
        id_token: idToken, client_info: encoded({ uid: "review-uid", utid: tenant }),
      } });
    } else {
      await route.fulfill({ json: { tenant_discovery_endpoint: authority + "/v2.0/.well-known/openid-configuration", metadata: [{
        preferred_network: "login.microsoftonline.com", preferred_cache: "login.windows.net",
        aliases: ["login.microsoftonline.com", "login.windows.net"],
      }] } });
    }
  });
  await page.route("**/api/v1/broker/auth/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/exchange")) {
      exchangeCalls++;
      expect(route.request().postDataJSON()).toEqual({ access_token: "synthetic-microsoft-access" });
      const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
      expect(stored).not.toContain("synthetic-microsoft-access");
      expect(stored).not.toContain("synthetic-microsoft-refresh");
      if (outcome === "refused") {
        await route.fulfill({ status: 403, json: { detail: { code: "no_access", message: "Account not provisioned" } } });
        return;
      }
      exchanged = true;
      await route.fulfill({ json: session });
    } else if (path.endsWith("/refresh")) {
      await route.fulfill({ status: exchanged ? 200 : 401, json: exchanged ? session : { detail: "No session" } });
    } else await route.fulfill({ json: { status: "none", verified: false } });
  });
  await page.goto("/sign-in");
  await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
  if (outcome === "refused") {
    await expect(page).toHaveURL(/\/sign-in\?denied/);
    await expect(page.getByRole("alert")).toContainText("does not have access");
    await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
    await expect(page).toHaveURL(/login\.microsoftonline\.com\/.+\/authorize/);
    expect(authorizeCalls).toBe(2);
    expect(exchangeCalls).toBe(1);
    expect(tokenCalls).toBe(1);
    return;
  }
  await expect(page).toHaveURL(new RegExp(`${new URL(baseURL!).origin}/broker/security$`), { timeout: 30_000 });
  expect(tokenCalls).toBe(1);
  expect(exchanged).toBe(true);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(storage).not.toContain("synthetic-microsoft-access");
  expect(storage).not.toContain("synthetic-microsoft-refresh");
  expect(storage).not.toContain("broker-memory-only-token");
  expect(await page.evaluate(() => sessionStorage.getItem("inspro-broker-signed-out"))).toBeNull();
});
}
