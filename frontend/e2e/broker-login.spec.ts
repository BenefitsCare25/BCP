import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const TENANT = "11111111-1111-4111-8111-111111111111";
const CLIENT = "22222222-2222-4222-8222-222222222222";
const AUTHORITY = `https://login.microsoftonline.com/${TENANT}`;

/** What `/public/site` reports for the host: the firm's staff sign-in methods. */
async function staffSite(page: Page, methods: { entra?: boolean; local?: boolean } = { entra: true }) {
  await page.route("**/api/v1/public/site", route => route.fulfill({ json: {
    firm: { name: "Review Brokerage", slug: "review" },
    staff_sign_in: {
      entra: methods.entra ? {
        tenant_id: TENANT, client_id: CLIENT, authority: AUTHORITY,
        scopes: ["openid", "profile", "email", `api://${CLIENT}/access_as_user`],
      } : null,
      local: methods.local === true,
    },
  } }));
  // Nobody is signed in: the boot sequence's session restore finds no cookie.
  await page.route("**/api/v1/broker/auth/refresh", route => route.fulfill({ status: 401, json: { detail: "No session" } }));
}

/** Stand in for Microsoft's endpoints so the real MSAL sign-in code runs. The
 *  authorize request is recorded and answered 204, which a browser treats as
 *  "stay on this page", like a redirect that has not yet arrived. */
async function microsoft(page: Page) {
  const calls = { authorize: [] as URL[] };
  await page.route("https://login.microsoftonline.com/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/authorize")) { calls.authorize.push(url); await route.fulfill({ status: 204 }); return; }
    if (url.pathname.includes(".well-known")) {
      await route.fulfill({ json: {
        issuer: AUTHORITY + "/v2.0", authorization_endpoint: AUTHORITY + "/oauth2/v2.0/authorize",
        token_endpoint: AUTHORITY + "/oauth2/v2.0/token", end_session_endpoint: AUTHORITY + "/oauth2/v2.0/logout",
        jwks_uri: AUTHORITY + "/discovery/v2.0/keys",
      } });
    } else {
      await route.fulfill({ json: { tenant_discovery_endpoint: AUTHORITY + "/v2.0/.well-known/openid-configuration", metadata: [{
        preferred_network: "login.microsoftonline.com", preferred_cache: "login.windows.net",
        aliases: ["login.microsoftonline.com", "login.windows.net"],
      }] } });
    }
  });
  return calls;
}

test("broker login: accessible layout and reduced-motion still without a video download", async ({ page }) => {
  await staffSite(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const media: string[] = [];
  page.on("request", request => { if (request.url().includes("/broker/login/") && request.url().endsWith(".mp4")) media.push(request.url()); });
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
  await expect(page.locator(".broker-login__poster")).toBeVisible();
  await page.locator(".broker-login__poster").evaluate((image: HTMLImageElement) => image.decode());
  await expect(page.locator(".broker-login video")).toHaveCount(0);
  expect(media).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(scan.violations).toEqual([]);
});

test("broker login: animation loops continuously without media controls", async ({ page }, testInfo) => {
  await staffSite(page);
  await page.goto("/sign-in");
  const video = page.locator(".broker-login video");
  await expect(video).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: /background animation/ })).toHaveCount(0);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0);
  expect(await video.evaluate((v: HTMLVideoElement) => ({ width: v.videoWidth, height: v.videoHeight,
    muted: v.muted, controls: v.controls, pip: v.disablePictureInPicture,
    remote: v.disableRemotePlayback, controlsList: v.getAttribute("controlsList") }))).toEqual({ width: 1080, height: 1080,
    muted: true, controls: false, pip: true, remote: true, controlsList: "nodownload nofullscreen noremoteplayback" });
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  // Reproduce the unwanted five-second freeze, then exercise a real native wrap.
  await page.waitForTimeout(5500);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  expect(await video.evaluate((v: HTMLVideoElement) => v.loop)).toBe(true);
  await video.evaluate((v: HTMLVideoElement) => { v.currentTime = v.duration - 0.3; });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(1.5);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("broker-login-without-media-controls.png"), fullPage: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(video).toHaveCount(0);
  await expect(page.locator(".broker-login__poster")).toBeVisible();
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0);
});

test("broker login: data-saving mode does not request the video", async ({ page }) => {
  await staffSite(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "connection", { configurable: true, value: Object.assign(new EventTarget(), { saveData: true }) });
  });
  const requests: string[] = [];
  page.on("request", request => { if (request.url().endsWith(".mp4")) requests.push(request.url()); });
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.locator(".broker-login video")).toHaveCount(0);
  expect(requests).toEqual([]);
});

for (const failure of ["media", "autoplay"] as const) {
  test(`broker login: ${failure} failure retains the still and sign-in`, async ({ page }) => {
    await staffSite(page);
    if (failure === "media") await page.route("**/broker/login/*.mp4", route => route.abort());
    else await page.addInitScript(() => {
      HTMLMediaElement.prototype.play = () => Promise.reject(new DOMException("Autoplay denied", "NotAllowedError"));
    });
    await page.goto("/sign-in");
    await expect(page.locator(".broker-login video")).toHaveCount(0);
    await expect(page.locator(".broker-login__poster")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause background animation" })).toHaveCount(0);
  });
}

test("broker login: denied access keeps the Microsoft account-picker flow and pending state", async ({ page }) => {
  await staffSite(page);
  const calls = await microsoft(page);
  await page.goto("/sign-in?denied=1");
  await expect(page.getByRole("alert")).toContainText("does not have access");
  const submit = page.getByRole("button", { name: "Sign in with Microsoft" });
  await submit.evaluate(button => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await expect(page.getByRole("button", { name: "Redirecting" })).toBeDisabled();
  // The directory comes from /public/site, and the picker is forced once.
  await expect.poll(() => calls.authorize.length).toBe(1);
  expect(calls.authorize[0].pathname).toBe(`/${TENANT}/oauth2/v2.0/authorize`);
  expect(calls.authorize[0].searchParams.get("client_id")).toBe(CLIENT);
  expect(calls.authorize[0].searchParams.get("prompt")).toBe("select_account");
});

test("broker login: a Microsoft failure is announced and can be retried", async ({ page }) => {
  await staffSite(page);
  const calls = await microsoft(page);
  await page.goto("/sign-in");
  const submit = page.getByRole("button", { name: "Sign in with Microsoft" });
  // The first attempt cannot start (the browser refuses to derive the PKCE
  // challenge); the real MSAL error reaches the page, and the next attempt works.
  await page.evaluate(() => {
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    let refuse = true;
    crypto.subtle.digest = ((...args: Parameters<SubtleCrypto["digest"]>) => {
      if (refuse) { refuse = false; return Promise.reject(new Error("Microsoft could not be reached")); }
      return digest(...args);
    }) as SubtleCrypto["digest"];
  });
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("Sign-in failed");
  await expect(submit).toBeEnabled();
  expect(calls.authorize).toEqual([]);
  await submit.click();
  await expect(page.getByRole("button", { name: "Redirecting" })).toBeDisabled();
  await expect.poll(() => calls.authorize.length).toBe(1);
});

const passwordSession = {
  access_token: "broker-password-memory-only-token", expires_at: "2100-01-01T00:00:00Z",
  user: { id: "broker-password", email: "staff@example.test", display_name: "Password Broker" },
  mfa_verified: true, mfa_required: true,
};

test("broker login: email and password sign-in asks for the authenticator code", async ({ page }) => {
  await staffSite(page, { local: true });
  const bodies: { login?: unknown; mfa?: unknown } = {};
  await page.route("**/api/v1/broker/auth/login", route => {
    bodies.login = route.request().postDataJSON();
    return route.fulfill({ json: { mfa_required: true, challenge_token: "challenge-1" } });
  });
  await page.route("**/api/v1/broker/auth/login/mfa", route => {
    bodies.mfa = route.request().postDataJSON();
    return route.fulfill({ json: passwordSession });
  });
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toHaveCount(0);
  await page.getByLabel("Work email").fill("staff@example.test");
  await page.getByLabel("Password", { exact: true }).fill("a-synthetic-passphrase");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Authentication code")).toBeVisible();
  expect(bodies.login).toEqual({ email: "staff@example.test", password: "a-synthetic-passphrase" });
  await page.getByLabel("Authentication code").fill("123456");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).not.toHaveURL(/\/sign-in/);
  expect(bodies.mfa).toEqual({ challenge_token: "challenge-1", code: "123456" });
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(storage).not.toContain("broker-password-memory-only-token");
  expect(storage).not.toContain("a-synthetic-passphrase");
});

test("broker login: a wrong password is refused without revealing whether the account exists", async ({ page }) => {
  await staffSite(page, { local: true });
  await page.route("**/api/v1/broker/auth/login", route =>
    route.fulfill({ status: 401, json: { detail: "Invalid credentials." } }));
  await page.goto("/sign-in");
  await page.getByLabel("Work email").fill("staff@example.test");
  await page.getByLabel("Password", { exact: true }).fill("wrong-passphrase");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("That email and password didn't work");
  await expect(page.getByLabel("Authentication code")).toHaveCount(0);
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("broker login: a firm offering both methods shows Microsoft and the password form", async ({ page }) => {
  await staffSite(page, { entra: true, local: true });
  await page.goto("/sign-in");
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
  await expect(page.getByLabel("Work email")).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
});
