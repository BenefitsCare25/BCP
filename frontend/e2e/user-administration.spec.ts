import { expect, test } from "@playwright/test";

for (const role of ["broker_admin", "broker_viewer", "system_admin"]) {
  test(`${role} has the correct user administration visibility`, async ({ page, request }, testInfo) => {
    const me = await (await request.get("/api/v1/me")).json();
    await page.route("**/api/v1/me", (route) =>
      route.fulfill({ json: { ...me, role } }),
    );
    const userRequests: string[] = [];
    await page.route("**/api/v1/admin/users**", (route) => {
      userRequests.push(route.request().url());
      return route.fulfill({ json: [{
        id: "review-user", email: "review@example.test", display_name: "Review User",
        role: "broker_viewer", status: "active", broker_firm_id: "review-firm", client_ids: [],
      }] });
    });
    await page.route("**/api/v1/admin/invitations**", (route) => {
      userRequests.push(route.request().url());
      return route.fulfill({ json: [] });
    });
    await page.route("**/api/v1/admin/broker-firms", (route) =>
      route.fulfill({ json: [{ id: "review-firm", name: "Review Brokerage", client_count: 1 }] }),
    );
    await page.goto("/firm/access");
    if (role === "system_admin") {
      await expect(page.getByText("Users", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Invite", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Rename", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Disable", exact: true })).toBeVisible();
      await expect(page.getByText("Review User", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Bind Microsoft identity" })).toBeVisible();
      const invite = page.getByRole("button", { name: "Invite", exact: true });
      await page.getByLabel("Email", { exact: true }).fill("new-broker@example.test");
      await expect(invite).toBeDisabled();
      await page.getByLabel("Microsoft object ID", { exact: true }).fill("11111111-1111-4111-8111-111111111111");
      await expect(invite).toBeEnabled();
      await page.screenshot({ path: testInfo.outputPath("broker-identity-invitation.png"), fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      expect(userRequests.length).toBe(2);
    } else {
      await expect(page.getByText(role === "broker_admin" ? "Client companies" : "Access restricted", { exact: true })).toBeVisible();
      await expect(page.getByText("Users", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^(Invite|Rename|Disable|Revoke)$/ })).toHaveCount(0);
      expect(userRequests).toEqual([]);
    }
  });
}

test("system admin binds an unlinked Microsoft identity through the account control", async ({ page, request }) => {
  const me = await (await request.get("/api/v1/me")).json();
  await page.route("**/api/v1/me", route => route.fulfill({ json: { ...me, role: "system_admin" } }));
  const oid = "11111111-1111-4111-8111-111111111111";
  let current = { id: "review-user", email: "review@example.test", display_name: "Review User",
    role: "broker_viewer", status: "active", broker_firm_id: "review-firm", client_ids: [], external_id: null as string | null };
  let bound = false;
  await page.route("**/api/v1/admin/users**", route => {
    if (route.request().method() === "PATCH") {
      expect(route.request().postDataJSON()).toEqual({ external_id: oid });
      current = { ...current, external_id: oid }; bound = true;
      return route.fulfill({ json: current });
    }
    return route.fulfill({ json: [current] });
  });
  await page.route("**/api/v1/admin/invitations**", route => route.fulfill({ json: [] }));
  await page.route("**/api/v1/admin/broker-firms", route => route.fulfill({ json: [
    { id: "review-firm", name: "Review Brokerage", client_count: 1 },
  ] }));
  await page.goto("/firm/access");
  await page.getByRole("button", { name: "Bind Microsoft identity" }).click();
  await page.getByLabel("Microsoft object ID for review@example.test").fill(oid);
  await page.getByRole("button", { name: "Save identity" }).click();
  await expect(page.getByRole("button", { name: "Bind Microsoft identity" })).toHaveCount(0);
  expect(bound).toBe(true);
});

for (const role of ["broker_admin", "broker_viewer", "system_admin"]) {
  test(`${role} has the correct product and roster removal controls`, async ({ page, request }, testInfo) => {
    const me = await (await request.get("/api/v1/me")).json();
    const clientId = me.accessible_clients[0].id;
    const years = await (await request.get("/api/v1/policy-years", {
      headers: { "X-Inspro-Client-ID": clientId },
    })).json();
    await page.addInitScript(({ clientId, yearId }) => {
      localStorage.setItem("inspro-session", JSON.stringify({
        state: { activeClientId: clientId, currentPolicyYearId: yearId }, version: 0,
      }));
    }, { clientId, yearId: years[0].id });
    await page.route("**/api/v1/me", (route) => route.fulfill({ json: { ...me, role } }));
    await page.route("**/api/v1/policy-years/*/setup-products", (route) =>
      route.fulfill({ json: [{
        code: "GCGP", display_name: "Review GP", line: "medical",
        has_template_file: true, has_slip_data: true, is_client_product: true,
        base_code: null, variant_label: null,
      }] }),
    );
    await page.goto("/client-relations/company-benefits");
    await expect(page.getByText("Review GP", { exact: true }).first()).toBeVisible();
    const remove = page.getByRole("button", { name: "Remove", exact: true });
    if (role === "system_admin") {
      await expect(remove).toBeVisible();
      await remove.click();
      const dialog = page.getByRole("dialog", { name: "Remove Review GP?" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Cancel" }).click();
    } else {
      await expect(remove).toHaveCount(0);
    }
    if (role === "broker_admin") {
      await expect(page.getByRole("button", { name: "Edit", exact: true }).first()).toBeVisible();
    }
    await page.goto("/policy-admin/member-listing");
    await expect(page.getByRole("tab", { name: "Employees", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Clear all", exact: true }))
      .toHaveCount(role === "system_admin" ? 1 : 0);
    await page.getByRole("tab", { name: "Dependants", exact: true }).click();
    await expect(page.getByRole("button", { name: "Clear all", exact: true }))
      .toHaveCount(role === "system_admin" ? 1 : 0);
    await page.screenshot({ path: testInfo.outputPath(`${role}-roster.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  });
}
