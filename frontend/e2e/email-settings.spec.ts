import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";

async function enter(page: Page, request: APIRequestContext) {
  const me = await (await request.get("/api/v1/me")).json();
  const clientId = me.active_client_id;
  const headers = { "X-Inspro-Client": clientId };
  const years = await (await request.get("/api/v1/policy-years", { headers })).json();
  const yearId = years[0].id;
  await page.addInitScript(({ clientId, yearId }) => { if (window !== window.top || !location.protocol.startsWith("http")) return; localStorage.setItem("inspro-session", JSON.stringify({
    state: { activeClientId: clientId, currentPolicyYearId: yearId, policyYearClientId: clientId }, version: 0,
  })); }, { clientId, yearId });
  await page.goto("/settings/company?tab=email");
  await expect(page.getByRole("tab", { name: "Email", exact: true })).toHaveAttribute("data-state", "active");
  await expect(page.getByRole("button", { name: "Employee welcome", exact: true })).toBeVisible();
  return { clientId, yearId, headers };
}

test("custom templates validate fields, publish, preview and guard unsaved navigation", async ({ page, request }, testInfo) => {
  await enter(page, request);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.getByRole("button", { name: "New template", exact: true }).click();
  await expect(page.getByRole("button", { name: "Publish template" })).toBeDisabled();
  const title = `Email review ${testInfo.project.name} ${Date.now()}`;
  await page.getByLabel("Template title", { exact: true }).fill(title);
  await page.getByLabel("Email subject", { exact: true }).fill("Hello {{invalid_field}}");
  await page.getByLabel("Email content", { exact: true }).fill("**Hello** {{recipient_name}}\n\nYour company is {{company_name}}.");
  await expect(page.getByText("Unsupported placeholder: invalid_field.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Publish template" })).toBeDisabled();
  await page.getByLabel("Email subject", { exact: true }).fill("Hello {{company_name}}");
  await page.getByLabel("Button label (optional)").fill("Open");
  await page.getByLabel("Button destination", { exact: true }).fill("javascript:alert(1)");
  await expect(page.getByRole("button", { name: "Publish template" })).toBeDisabled();
  await page.getByLabel("Button destination", { exact: true }).fill("{{portal_url}}");
  await page.getByRole("button", { name: "Publish template" }).click();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("Draft changes are not published", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Edit template", exact: true })).toBeVisible();
  await expect(page.frameLocator('iframe[title="Rendered email preview"]').getByText("Hello", { exact: false }).first()).toBeVisible();
  await page.getByLabel("Email content", { exact: true }).fill("Unsaved work");
  await page.getByRole("button", { name: "Sender & branding", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Leave unsaved work?" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Email content", { exact: true })).toHaveValue("Unsaved work");
  await page.getByLabel("Email content", { exact: true }).fill("**Hello** {{recipient_name}}\n\nYour company is {{company_name}}.");
  await page.screenshot({ path: testInfo.outputPath("email-editor.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  // The email iframe deliberately forbids scripts/origin access. Scan its HTML
  // in a separate test page instead of weakening the production sandbox.
  const accessibility = await new AxeBuilder({ page }).include('main').exclude('iframe').withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(accessibility.violations.filter(v => v.impact === "critical" || v.impact === "serious")).toEqual([]);
  const emailPage = await page.context().newPage();
  await emailPage.setContent((await page.locator('iframe[title="Rendered email preview"]').getAttribute("srcdoc"))!);
  const emailAccessibility = await new AxeBuilder({ page: emailPage }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(emailAccessibility.violations.filter(v => v.impact === "critical" || v.impact === "serious")).toEqual([]);
  await emailPage.close();
  if (testInfo.project.name === "desktop-chromium") {
    for (const width of [768, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await page.getByRole("heading", { name: "Email", exact: true }).scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`email-editor-${width}.png`), fullPage: true });
    }
  }
});

test("real recipient preview and selected preparation work without SMTP", async ({ page, request }, testInfo) => {
  const { yearId, headers } = await enter(page, request);
  const suffix = `${testInfo.project.name}-${Date.now()}`;
  // Additive synthetic roster in Playwright's disposable OS-temp database only.
  const code = "import io,sys; from openpyxl import Workbook; w=Workbook(); s=w.active; s.append(['Staff ID','Employee Name','Email']); s.append([sys.argv[1], 'Email Review Alex',sys.argv[1]+'@example.invalid']); b=io.BytesIO(); w.save(b); sys.stdout.buffer.write(b.getvalue())";
  const python = resolve(process.platform === "win32" ? "../backend/.venv/Scripts/python.exe" : "../backend/.venv/bin/python");
  const workbook = execFileSync(python, ["-c", code, suffix], { windowsHide: true });
  const upload = await request.post("/api/v1/employees/upload", { headers, multipart: {
    policy_year_id: yearId, file: { name: "email-review.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: workbook },
  } });
  expect(upload.ok(), await upload.text()).toBe(true);
  const content = { title: `Manual email ${suffix}`, subject: "Welcome {{recipient_name}}",
    body: "Hello {{recipient_name}} at {{company_name}}. Visit {{portal_url}}.", audience: "employee", purpose: "general",
    button_label: "Open portal", button_url: "{{portal_url}}" };
  const created = await (await request.post("/api/v1/email-templates", { headers, data: { content } })).json();
  const published = await request.post(`/api/v1/email-templates/${created.key}/publish`, { headers, data: { revision: created.revision } });
  expect(published.ok(), await published.text()).toBe(true);
  await page.reload();
  await page.getByRole("button", { name: content.title, exact: true }).click();
  await page.getByLabel("Find a recipient for real-data preview").fill(suffix);
  await expect(page.getByLabel("Preview recipient").locator("option")).toHaveCount(2);
  const value = await page.getByLabel("Preview recipient").locator("option").nth(1).getAttribute("value");
  await page.getByLabel("Preview recipient").selectOption(value!);
  await expect(page.getByText("Real recipient data · preview only", { exact: true })).toBeVisible();
  await expect(page.frameLocator('iframe[title="Rendered email preview"]').getByText("Hello Email Review Alex at", { exact: false })).toBeVisible();
  const portalUrl = await page.frameLocator('iframe[title="Rendered email preview"]').getByRole("link", { name: "Open portal" }).getAttribute("href");
  expect(portalUrl).not.toContain("example.invalid");
  await page.getByRole("button", { name: "Templates", exact: true }).last().click();
  await page.getByRole("row").filter({ has: page.getByRole("button", { name: content.title, exact: true }) }).getByRole("button", { name: "Send email", exact: true }).click();
  await page.getByLabel("Find employees").fill(suffix);
  const recipientRow = page.getByRole("row").filter({ has: page.getByText(suffix, { exact: true }) });
  await expect(recipientRow).toHaveCount(1);
  await recipientRow.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Review 1 selected recipients" }).click();
  await expect(page.getByRole("button", { name: "Send to 1 recipients" })).toBeDisabled();
  await page.getByRole("button", { name: "Save preparation", exact: true }).click();
  await expect(page.getByText("Preparation saved. No email was queued or sent.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Prepared messages", exact: true }).click();
  await expect(page.getByRole("cell", { name: content.title, exact: true })).toBeVisible();
  await page.getByRole("button", { name: content.title, exact: true }).click();
  await expect(page.getByRole("heading", { name: "1 saved recipients" })).toBeVisible();
  await expect(page.frameLocator('iframe[title="Saved email preview"]').getByText("Hello Alex Tan (sample)", { exact: false })).toBeVisible();
  await expect(page.frameLocator('iframe[title="Saved email preview"]').getByRole("link", { name: "Open portal" })).toHaveAttribute("href", portalUrl!);
  const delivery = await request.post("/api/v1/email-templates/send", { headers, data: {} });
  expect(delivery.status()).toBe(503);
  await page.screenshot({ path: testInfo.outputPath("email-prepared.png"), fullPage: true });
});

test("branding validates addresses and viewers cannot edit", async ({ page, request }) => {
  await enter(page, request);
  await page.getByRole("button", { name: "Sender & branding", exact: true }).click();
  await page.getByLabel("Support / Reply-To email").fill("invalid");
  await expect(page.getByText("Enter a valid support email address.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save branding" })).toBeDisabled();
  await page.getByLabel("Support / Reply-To email").fill("helpdesk@inspro.com.sg");
  await page.route("**/api/v1/me", async route => { const response = await route.fetch(); const data = await response.json(); await route.fulfill({ response, json: { ...data, role: "broker_viewer" } }); });
  await page.reload();
  await expect(page.getByRole("button", { name: "New template", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send email", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Employee welcome", exact: true }).click();
  await expect(page.getByLabel("Template title", { exact: true })).toBeDisabled();
});

test("broker publications refresh an already loaded company catalog", async ({ page, request }, testInfo) => {
  const suffix = `${testInfo.project.name}-${Date.now()}`;
  const initialTitle = `Existing broker default ${suffix}`;
  const me = await (await request.get("/api/v1/me")).json();
  const headers = { "X-Inspro-Client": me.active_client_id };
  const created = await request.post("/api/v1/email-templates?scope=firm", { headers, data: {
    content: { title: initialTitle, subject: "Initial broker subject", body: "Hello {{recipient_name}}." },
  } });
  expect(created.ok(), await created.text()).toBe(true);
  const initial = await created.json();
  const published = await request.post(`/api/v1/email-templates/${initial.key}/publish?scope=firm`, {
    headers, data: { revision: initial.revision },
  });
  expect(published.ok(), await published.text()).toBe(true);
  await enter(page, request);
  await expect(page.getByRole("button", { name: initialTitle, exact: true })).toBeVisible();
  const subject = `Updated broker welcome ${suffix}`;
  await page.getByLabel("Configure", { exact: true }).selectOption("firm");
  await page.getByRole("button", { name: initialTitle, exact: true }).click();
  await page.getByLabel("Email subject", { exact: true }).fill(subject);
  await page.getByRole("button", { name: "Publish template" }).click();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("Template published. No email was sent.", { exact: true })).toBeVisible();
  await page.getByLabel("Configure", { exact: true }).selectOption("company");
  await page.getByRole("button", { name: initialTitle, exact: true }).click();
  await expect(page.getByLabel("Email subject", { exact: true })).toHaveValue(subject);

  await page.getByLabel("Configure", { exact: true }).selectOption("firm");
  await page.getByRole("button", { name: "New template", exact: true }).click();
  const title = `New broker default ${suffix}`;
  await page.getByLabel("Template title", { exact: true }).fill(title);
  await page.getByLabel("Email subject", { exact: true }).fill("Broker announcement");
  await page.getByLabel("Email content", { exact: true }).fill("Hello {{recipient_name}}.");
  await page.getByRole("button", { name: "Publish template" }).click();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Publish this template?" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Edit template", exact: true })).toBeVisible();
  await page.getByLabel("Configure", { exact: true }).selectOption("company");
  await expect(page.getByRole("button", { name: title, exact: true })).toBeVisible();
});

test("removing a broker default refreshes the cached inherited company catalog", async ({ page, request }) => {
  let removed = false;
  const title = "Disposable broker catalog removal";
  // Exercise the system-admin UI with a synthetic catalog; no privileged API
  // mutation or role change is made in the disposable test database.
  await page.route("**/api/v1/me", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), role: "system_admin" } });
  });
  await page.route("**/api/v1/email-templates**", async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === "DELETE" && url.pathname.endsWith("/custom_cache-removal")) {
      removed = true;
      await route.fulfill({ status: 204 });
      return;
    }
    if (route.request().method() !== "GET" || url.pathname !== "/api/v1/email-templates") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const catalog = await response.json();
    if (!removed) {
      const base = catalog.items[0];
      catalog.items.push({ ...base, key: "custom_cache-removal", source: "firm",
        content: { ...base.content, title }, published_content: { ...base.content, title },
        has_local_draft: url.searchParams.get("scope") === "firm", has_changes: false });
    }
    await route.fulfill({ response, json: catalog });
  });
  await enter(page, request);
  await expect(page.getByRole("button", { name: title, exact: true })).toBeVisible();
  await page.getByLabel("Configure", { exact: true }).selectOption("firm");
  await page.getByRole("button", { name: `Remove ${title}`, exact: true }).click();
  await page.getByRole("button", { name: "Remove template", exact: true }).click();
  await expect(page.getByRole("button", { name: title, exact: true })).toHaveCount(0);
  await page.getByLabel("Configure", { exact: true }).selectOption("company");
  await expect(page.getByRole("button", { name: "Employee welcome", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: title, exact: true })).toHaveCount(0);
  await page.unrouteAll({ behavior: "wait" });
});

test("HTTPS branding images render inside a sandbox under production CSP", async ({ page, request }) => {
  const me = await request.get("/api/v1/me");
  const csp = me.headers()["content-security-policy"];
  expect(csp).toContain("script-src 'self'");
  const clientId = (await me.json()).active_client_id;
  const response = await request.post("/api/v1/email-templates/preview", {
    headers: { "X-Inspro-Client": clientId },
    data: { content: { title: "CSP preview", subject: "Hello", body: "Hello {{recipient_name}}" } },
  });
  expect(response.ok()).toBe(true);
  // Exercise a configured-logo element under the real response policy without
  // modifying shared test-company branding or making external image requests.
  const html = (await response.json()).html.replace("<main>", '<main><img alt="Company logo" src="https://logo.example.invalid/logo.png">');
  await page.route("https://logo.example.invalid/logo.png", route => route.fulfill({ contentType: "image/png",
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=", "base64") }));
  await page.route("https://preview.example.invalid/", route => route.fulfill({ contentType: "text/html",
    headers: { "Content-Security-Policy": csp }, body: '<!doctype html><html lang="en"><title>Preview shell</title><iframe title="Email" sandbox=""></iframe></html>' }));
  await page.goto("https://preview.example.invalid/");
  await page.locator("iframe").evaluate((frame: HTMLIFrameElement, content) => { frame.srcdoc = content; }, html);
  const logo = page.frameLocator("iframe").getByRole("img", { name: "Company logo" });
  await expect.poll(() => logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  await expect(page.locator("iframe")).toHaveAttribute("sandbox", "");
});
