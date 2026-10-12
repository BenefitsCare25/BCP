import { expect, test, type Page } from "@playwright/test";

async function signedOut(page: Page) {
  await page.route("**/api/v1/**", route => route.request().url().endsWith("/public/site")
    ? route.fulfill({ json: {} })
    : route.fulfill({ status: 401, json: { detail: "No session" } }));
}

for (const role of ["portal", "hr"] as const) {
  const target = role === "portal" ? "/portal/acme/sign-in" : "/hr/sign-in?company=acme";
  test.describe(`${role} login scenery`, () => {
    test.use({ timezoneId: "America/Los_Angeles" });

    for (const [period, time] of [
      ["sunrise", "2026-10-12T07:00:00+08:00"],
      ["morning", "2026-10-12T09:00:00+08:00"],
      ["afternoon", "2026-10-12T15:00:00+08:00"],
      ["sunset", "2026-10-12T18:50:00+08:00"],
      ["night", "2026-10-12T22:00:00+08:00"],
    ] as const) {
      test(`${period}: shares the matching corrected clip and preserves sign-in`, async ({ page }, testInfo) => {
        await signedOut(page);
        await page.clock.setFixedTime(new Date(time));
        const downloads: string[] = [];
        page.on("request", request => {
          if (request.url().endsWith(".mp4")) downloads.push(new URL(request.url()).pathname);
        });
        await page.goto(target);
        const scenery = page.locator(".portal-login__scenery");
        await expect(scenery).toHaveAttribute("data-period", period);
        const video = page.locator(".portal-login video");
        await expect(video).toBeVisible({ timeout: 20_000 });
        await expect(video).toHaveAttribute("src", `/broker/login/marina-bay-${period}-v3.mp4`);
        await expect(page.locator(".portal-login__plate")).toHaveAttribute("src", `/broker/login/marina-bay-${period}-v3.webp`);
        expect(await video.evaluate((v: HTMLVideoElement) => ({
          duration: v.duration, width: v.videoWidth, height: v.videoHeight,
          loop: v.loop, muted: v.muted, controls: v.controls,
        }))).toEqual({ duration: 5, width: 1080, height: 1080, loop: true, muted: true, controls: false });
        expect(new Set(downloads)).toEqual(new Set([`/broker/login/marina-bay-${period}-v3.mp4`]));
        await expect(page.locator(`#${role}-identifier`)).toBeVisible();
        await expect(page.locator(`#${role}-password`)).toBeVisible();
        await expect(page.locator(".portal-login__roles a[aria-current]"))
          .toHaveText(role === "portal" ? "Employee" : "HR admin");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        const curve = page.locator(".portal-login__curve");
        if (testInfo.project.name.startsWith("desktop")) await expect(curve).toBeVisible();
        else await expect(curve).toBeHidden();
        await page.screenshot({ path: testInfo.outputPath(`${role}-${period}.png`), fullPage: true });
      });
    }

    for (const preference of ["reduced motion", "data saving"] as const) {
      test(`${preference}: retains the matching still without fetching video`, async ({ page }) => {
        await signedOut(page);
        await page.clock.setFixedTime(new Date("2026-10-12T22:00:00+08:00"));
        if (preference === "reduced motion") await page.emulateMedia({ reducedMotion: "reduce" });
        else await page.addInitScript(() => {
          Object.defineProperty(navigator, "connection", { configurable: true, value: Object.assign(new EventTarget(), { saveData: true }) });
        });
        const downloads: string[] = [];
        page.on("request", request => { if (request.url().endsWith(".mp4")) downloads.push(request.url()); });
        await page.goto(target);
        await expect(page.locator(".portal-login__plate")).toBeVisible();
        await page.locator(".portal-login__plate").evaluate((image: HTMLImageElement) => image.decode());
        await expect(page.locator(".portal-login__plate")).toHaveAttribute("src", "/broker/login/marina-bay-night-v3.webp");
        await expect(page.locator(".portal-login video")).toHaveCount(0);
        expect(downloads).toEqual([]);
        await expect(page.locator(`#${role}-password`)).toBeVisible();
      });
    }

    test("media failure retains sign-in and a later period can play", async ({ page }) => {
      await signedOut(page);
      await page.clock.setFixedTime(new Date("2026-10-12T09:00:00+08:00"));
      await page.route("**/broker/login/marina-bay-morning-v3.mp4", route => route.abort());
      await page.goto(target);
      await expect(page.locator(".portal-login video")).toHaveCount(0);
      await expect(page.locator(".portal-login__plate")).toBeVisible();
      await expect(page.locator(`#${role}-identifier`)).toBeVisible();
      await page.clock.setFixedTime(new Date("2026-10-12T22:00:00+08:00"));
      await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
      await expect(page.locator(".portal-login__scenery")).toHaveAttribute("data-period", "night");
      await expect(page.locator(".portal-login video")).toBeVisible({ timeout: 20_000 });
    });
  });
}
