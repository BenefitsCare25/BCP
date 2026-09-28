const { chromium, expect } = require("@playwright/test");
const { default: AxeBuilder } = require("@axe-core/playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.env.AI_REVIEW_URL || "http://127.0.0.1:5173";
const captures = path.resolve(__dirname, "../../.impeccable/review");
const results = [];
fs.mkdirSync(captures, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1536, height: 1024 },
    });
    const meResponse = await context.request.get(`${base}/api/v1/me`);
    assert.equal(meResponse.status(), 200);
    const me = await meResponse.json();
    const client = me.accessible_clients[0];
    assert.ok(client, "Use a seeded local demo API.");
    await context.addInitScript(
      (clientId) =>
        localStorage.setItem(
          "inspro-session",
          JSON.stringify({
            state: {
              activeClientId: clientId,
              currentPolicyYearId: null,
              policyYearClientId: null,
            },
            version: 0,
          }),
        ),
      client.id,
    );
    const page = await context.newPage();
    const errors = [];
    const mutations = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (
        request.url().includes("/api/") &&
        !["GET", "HEAD", "OPTIONS"].includes(request.method())
      )
        mutations.push(`${request.method()} ${request.url()}`);
    });
    const goto = async (url) => {
      await page.goto(`${base}${url}`);
      await expect(
        page.getByText("Local UI review · Sample data.", { exact: false }),
      ).toBeVisible();
    };
    const tabs = () =>
      page.getByRole("tablist", { name: "AI oversight sections" });

    await goto("/firm/ai-oversight");
    await expect(
      page.getByRole("heading", { name: "AI oversight", exact: true }),
    ).toBeVisible();
    await expect(
      page
        .locator("[data-top-bar]")
        .getByRole("link", { name: "AI oversight", exact: true }),
    ).toBeVisible();
    await expect(page.locator("[data-context-bar=company]")).toHaveCount(0);
    await page
      .getByRole("textbox", { name: "Find an AI use" })
      .fill("no such use");
    await expect(
      page.getByRole("heading", { name: "No matching AI uses" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(
      page
        .getByRole("list", { name: "Registered AI uses" })
        .getByRole("listitem"),
    ).toHaveCount(3);
    await page
      .getByRole("button", { name: "Register an AI use", exact: true })
      .click();
    await page
      .getByLabel("AI use name", { exact: true })
      .fill("Sample eligibility assistance");
    await page
      .getByLabel("Intended purpose", { exact: true })
      .fill("Suggest draft eligibility rules for a broker to review.");
    await page.getByLabel("Accountable owner").selectOption("Product lead");
    await page.getByRole("button", { name: "Save use", exact: true }).click();
    await expect(
      page.getByRole("link", { name: "Sample eligibility assistance" }),
    ).toBeVisible();
    await page
      .getByRole("link", { name: "Sample eligibility assistance" })
      .click();
    await expect(
      page.getByRole("region", { name: "Selected AI use" }),
    ).toContainText("Manual handling");
    await page.getByRole("button", { name: "Edit owner and purpose" }).click();
    await page.getByLabel("Accountable owner").selectOption("AI lead");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Selected AI use" }),
    ).toContainText("Product lead");
    await page.getByRole("button", { name: "Back to AI use register" }).click();
    await expect(
      page.getByRole("region", { name: "Selected AI use" }),
    ).toHaveCount(0);
    await tabs().getByRole("tab", { name: "Actions" }).click();
    await page
      .getByRole("button", { name: "Mark complete", exact: true })
      .first()
      .click();
    await expect(page.getByRole("button", { name: "Reopen" })).toHaveCount(1);
    await tabs().getByRole("tab", { name: "Evidence", exact: true }).click();
    await page.getByRole("button", { name: /AI policy/ }).click();
    await expect(
      page.getByRole("button", { name: "Request approval", exact: true }),
    ).toBeDisabled();
    await page
      .getByLabel("Reviewer", { exact: true })
      .selectOption("Independent reviewer");
    await page
      .getByRole("button", { name: "Request approval", exact: true })
      .click();
    await expect(page.getByRole("button", { name: /AI policy/ })).toContainText(
      "Awaiting approval",
    );
    results.push(
      "Firm register search, add, cancel, owner, close, actions and evidence gating",
    );

    await page.getByRole("link", { name: "Platform releases" }).click();
    await expect(page.locator("[data-context-bar=platform]")).toContainText(
      "All firms",
    );
    await expect(page.locator("[data-context-bar=company]")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Approve release", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "Add evaluation evidence" }).click();
    await page
      .getByRole("button", { name: "Attach sample evaluation" })
      .click();
    await expect(
      page.getByRole("button", { name: "Approve release", exact: true }),
    ).toBeDisabled();
    await page
      .getByLabel("Independent approver")
      .selectOption("Independent claims reviewer");
    await page
      .getByLabel("Approval reason")
      .fill("Sample evidence and limited coverage reviewed.");
    await page
      .getByRole("button", { name: "Approve release", exact: true })
      .click();
    await expect(
      page.getByText("Existing sample release v2 remains active."),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Preview activation", exact: true })
      .click();
    await expect(
      page.getByText("Sample release v3 is active in this preview."),
    ).toBeVisible();
    await page.getByRole("tab", { name: "AI services", exact: true }).click();
    await expect(
      page.getByText("Shared service · Current sample release v3", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText("Shared service · Current sample release v2", {
        exact: true,
      }),
    ).toHaveCount(2);
    await page.screenshot({
      path: path.join(captures, "services-active.png"),
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("tab", { name: "Releases", exact: true }).click();
    await page
      .getByRole("button", { name: "Preview rollback to v2", exact: true })
      .click();
    await expect(
      page.getByText("Existing sample release v2 remains active."),
    ).toBeVisible();
    await page.getByRole("tab", { name: "AI services", exact: true }).click();
    await expect(
      page.getByText("Shared service · Current sample release v3", {
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Shared service · Current sample release v2", {
        exact: true,
      }),
    ).toHaveCount(3);
    await page.screenshot({
      path: path.join(captures, "services-rollback.png"),
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("tab", { name: "Data & suppliers" }).click();
    await page.getByRole("button", { name: "Add evidence reference" }).click();
    await page
      .getByLabel("Document reference", { exact: true })
      .fill("Sample processing agreement v1");
    await page
      .getByRole("button", { name: "Save reference", exact: true })
      .click();
    await expect(
      page.getByText("Reference added · Needs review"),
    ).toBeVisible();
    results.push(
      "Separate platform context, blocked release, approval versus activation, rollback and supplier evidence",
    );

    await page
      .getByRole("link", { name: "AI oversight", exact: true })
      .last()
      .click();
    await page
      .getByRole("link", { name: "Claim decision", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Claim DEMO-1042" }),
    ).toBeVisible();
    await page
      .getByLabel("Explanation to member (required)", { exact: true })
      .fill("   ");
    await page
      .getByRole("button", { name: "Record approval", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText(
      "Write the explanation",
    );
    await page
      .getByLabel("Explanation to member (required)", { exact: true })
      .fill(
        "The policy covers the S$120 consultation. The S$30 item is excluded.",
      );
    await page
      .getByLabel("Internal assessment note (optional)", { exact: true })
      .fill("PRIVATE-NOTE-REGRESSION");
    await expect(
      page.getByRole("region", { name: "Member-visible decision preview" }),
    ).not.toContainText("PRIVATE-NOTE-REGRESSION");
    await page
      .getByRole("button", { name: "Record approval", exact: true })
      .click();
    await expect(
      page.getByText("Sample decision recorded. No notification was sent."),
    ).toBeVisible();
    await page.getByRole("link", { name: "View member journey" }).click();
    await page.getByRole("tab", { name: "Claim outcome", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Claim details", exact: true }),
    ).toBeVisible();
    await expect(page.locator("main")).toContainText(
      "The policy covers the S$120 consultation.",
    );
    await expect(page.locator("main")).not.toContainText(
      "PRIVATE-NOTE-REGRESSION",
    );
    await page.getByRole("button", { name: "Request another review" }).click();
    await page.getByRole("button", { name: "Submit review request" }).click();
    await expect(page.getByRole("alert")).toContainText("Tell us");
    await page
      .getByLabel("What would you like us to reconsider?")
      .fill("Please check the policy reference for the excluded item.");
    await page.getByRole("button", { name: "Submit review request" }).click();
    await expect(
      page.getByText("Review requested", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("S$120.00", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Submit review request" }),
    ).toHaveCount(0);
    await page.getByRole("tab", { name: "New claim", exact: true }).click();
    await page.getByRole("button", { name: "Enter details myself" }).click();
    await expect(
      page.getByText("Manual entry selected. Add the details below."),
    ).toBeVisible();
    await expect(
      page.getByText("Manual entry skips autofill.", { exact: false }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Try sample autofill" }).click();
    await expect(page.getByLabel("Clinic or provider")).toHaveValue(
      "GP Clinic",
    );
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Sample claim is ready for review" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Reset samples" }).click();
    await expect(page.getByLabel("Clinic or provider")).toHaveValue("");
    results.push(
      "Claim reason validation, private-note isolation, member reconsideration, manual entry and reset",
    );

    const scenes = [
      ["firm", "/firm/ai-oversight"],
      ["platform", "/platform/ai-oversight"],
      ["claim", "/claims/review?preview=ai-governance"],
      ["member", "/firm/ai-oversight/member-journey"],
    ];
    const axeViolations = [];
    for (const width of [1536, 390]) {
      await page.setViewportSize({
        width,
        height: width === 1536 ? 1024 : 844,
      });
      for (const [name, route] of scenes) {
        await goto(route);
        await page.evaluate(() => document.fonts.ready);
        const main = page.locator("main");
        assert.equal(
          await main.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
          true,
          `${name}: horizontal overflow at ${width}`,
        );
        await page.screenshot({
          path: path.join(
            captures,
            name === "firm"
              ? width === 1536
                ? "desktop.png"
                : "mobile.png"
              : `${name}-${width}.png`,
          ),
          fullPage: true,
          animations: "disabled",
        });
        const axe = await new AxeBuilder({ page })
          .include("main")
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze();
        axeViolations.push(
          ...axe.violations.map((v) => ({
            scene: name,
            width,
            id: v.id,
            impact: v.impact,
            nodes: v.nodes.map((n) => n.target),
          })),
        );
        if (name === "claim" || name === "member") {
          if (name === "member") {
            await page.getByRole("tab", { name: "Claim outcome" }).click();
          }
          await main.evaluate((el) => el.scrollTo(0, el.scrollHeight));
          await page.screenshot({
            path: path.join(captures, `${name}-detail-${width}.png`),
            fullPage: true,
            animations: "disabled",
          });
        }
      }
    }
    fs.writeFileSync(
      path.join(captures, "ai-governance-qa.json"),
      JSON.stringify({ results, axeViolations, errors, mutations }, null, 2),
    );
    await page.setViewportSize({ width: 320, height: 844 });
    for (const [name, route] of scenes) {
      await goto(route);
      assert.equal(
        await page
          .locator("main")
          .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
        true,
        `${name}: overflow at 320px`,
      );
    }
    await goto("/firm/ai-oversight");
    await page
      .getByRole("list", { name: "Registered AI uses" })
      .getByRole("link", { name: "Claim review", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "Selected AI use" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Back to AI use register" }).click();
    await expect(
      page.getByRole("list", { name: "Registered AI uses" }),
    ).toBeVisible();
    results.push(
      "Responsive screens at 1536, 390 and 320px, and mobile detail return",
    );

    await page.route("**/api/v1/me", (route) =>
      route.fulfill({ json: { ...me, role: "broker_viewer" } }),
    );
    await page.goto(`${base}/firm/ai-oversight`);
    await expect(
      page.getByRole("heading", { name: "Access restricted" }),
    ).toBeVisible();
    await page.unroute("**/api/v1/me");
    await page.route("**/api/v1/me", (route) =>
      route.fulfill({ json: { ...me, role: "broker_admin" } }),
    );
    await page.goto(`${base}/platform/ai-oversight`);
    await expect(
      page.getByRole("heading", { name: "Access restricted" }),
    ).toBeVisible();
    await page.goto(`${base}/firm/ai-oversight`);
    await expect(
      page.getByRole("heading", { name: "AI oversight", exact: true }),
    ).toBeVisible();
    results.push(
      "Broker viewer restricted; firm admin cannot enter platform scope",
    );
    assert.deepEqual(errors, [], "Browser JavaScript errors");
    assert.deepEqual(mutations, [], "Review must not send API mutations");
    fs.writeFileSync(
      path.join(captures, "ai-governance-qa.json"),
      JSON.stringify({ results, axeViolations, errors, mutations }, null, 2),
    );
    console.log(
      JSON.stringify({ results, axeViolations, errors, mutations }, null, 2),
    );
    assert.deepEqual(axeViolations, [], "Accessibility violations");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
