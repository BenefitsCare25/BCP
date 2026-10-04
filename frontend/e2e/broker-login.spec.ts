import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function mockMicrosoft(page: Page, fail = false) {
  await page.route("**/src/auth/msal.ts*", route => route.fulfill({
    contentType: "application/javascript",
    body: `
      export const ENTRA_ENABLED = true;
      export const getMsal = () => null;
      export const getActiveAccount = () => null;
      export const initializeMsal = async () => null;
      export const acquireAccessToken = async () => null;
      export const clearLocalSession = async () => {};
      export const signOut = async () => {};
      export const signIn = async (options) => {
        window.__brokerCalls = [...(window.__brokerCalls || []), options];
        await new Promise(resolve => setTimeout(resolve, 250));
        ${fail ? 'throw new Error("Microsoft could not be reached");' : ""}
      };
    `,
  }));
}

test("broker login: accessible layout and reduced-motion still without a video download", async ({ page }) => {
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

test("broker login: animation has no controls and settles within five seconds", async ({ page }, testInfo) => {
  await page.goto("/sign-in");
  const video = page.locator(".broker-login video");
  await expect(video).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: /background animation/ })).toHaveCount(0);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0);
  expect(await video.evaluate((v: HTMLVideoElement) => ({ width: v.videoWidth, height: v.videoHeight,
    muted: v.muted, loop: v.loop, controls: v.controls, pip: v.disablePictureInPicture,
    remote: v.disableRemotePlayback, controlsList: v.getAttribute("controlsList") }))).toEqual({ width: 1080, height: 1080,
    muted: true, loop: false, controls: false, pip: true, remote: true, controlsList: "nodownload nofullscreen noremoteplayback" });
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
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused), { timeout: 7000 }).toBe(true);
  const time = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
  expect(time).toBeLessThan(5.5);
  await page.waitForTimeout(250);
  expect(await video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBe(time);
  await page.screenshot({ path: testInfo.outputPath("broker-login-without-media-controls.png"), fullPage: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(video).toHaveCount(0);
  await expect(page.locator(".broker-login__poster")).toBeVisible();
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
});

test("broker login: data-saving mode does not request the video", async ({ page }) => {
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
  await mockMicrosoft(page);
  await page.goto("/sign-in?denied=1");
  await expect(page.getByRole("alert")).toContainText("does not have access");
  const submit = page.getByRole("button", { name: "Sign in with Microsoft" });
  await submit.evaluate(button => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await expect(page.getByRole("button", { name: "Redirecting" })).toBeDisabled();
  expect(await page.evaluate(() => (window as unknown as { __brokerCalls: unknown[] }).__brokerCalls)).toEqual([{ selectAccount: true }]);
});

test("broker login: a Microsoft failure is announced and can be retried", async ({ page }) => {
  await mockMicrosoft(page, true);
  await page.goto("/sign-in");
  const submit = page.getByRole("button", { name: "Sign in with Microsoft" });
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("Microsoft could not be reached");
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(submit).toBeEnabled();
  expect(await page.evaluate(() => (window as unknown as { __brokerCalls: unknown[] }).__brokerCalls)).toEqual([{ selectAccount: false }, { selectAccount: false }]);
});
