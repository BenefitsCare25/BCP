import { expect, test } from "@playwright/test";

for (const [surface, url, scenery] of [
  ["broker", "/sign-in", ".broker-login__scenery"],
  ["employee", "/portal/acme/sign-in", ".portal-login__scenery"],
  ["HR", "/hr/sign-in?company=acme", ".portal-login__scenery"],
] as const) {
  test(`${surface}: media menus and dragging are suppressed while forms remain usable`, async ({ page }) => {
    await page.goto(url);
    await expect(page.getByRole("heading", { name: "Welcome back", exact: true })).toBeVisible();
    await page.evaluate(() => {
      document.addEventListener("contextmenu", event => {
        document.documentElement.dataset.mediaMenuPrevented = String(event.defaultPrevented);
      });
    });
    // A real right-click on the scenery must cancel the browser's media menu.
    await page.locator(scenery).click({ button: "right", position: { x: 20, y: 20 } });
    await expect(page.locator("html")).toHaveAttribute("data-media-menu-prevented", "true");
    // The brand logo's alt text is the product name (built-in brand: "Inspro").
    const logo = page.locator("img[alt='Inspro']");
    const allowed = await logo.evaluate(element => ({
      menu: element.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })),
      drag: element.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true })),
    }));
    expect(allowed).toEqual({ menu: false, drag: false });
    expect(await page.getByRole("heading", { name: "Welcome back", exact: true }).evaluate(element =>
      element.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })))).toBe(true);
    const video = page.locator(`${scenery} video`);
    await expect(video).toHaveAttribute("controlsList", "nodownload nofullscreen noremoteplayback");
    expect(await video.evaluate((element: HTMLVideoElement) => ({ controls: element.controls,
      pip: element.disablePictureInPicture, remote: element.disableRemotePlayback }))).toEqual({ controls: false, pip: true, remote: true });
    if (surface !== "broker") {
      await page.locator("input[type='password']").fill("local-only-synthetic-password");
      await expect(page.locator("input[type='password']")).toHaveValue("local-only-synthetic-password");
    }
  });
}
