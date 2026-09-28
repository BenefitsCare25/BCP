import { expect, test, type Page } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  const me = await (await request.get("/api/v1/me")).json();
  await page.addInitScript((client) => {
    localStorage.setItem(
      "inspro-session",
      JSON.stringify({
        state: {
          activeClientId: client,
          currentPolicyYearId: null,
          policyYearClientId: null,
        },
        version: 0,
      }),
    );
  }, me.accessible_clients[0].id);
});

async function returnToDecision(page: Page) {
  await page
    .getByRole("link", { name: "AI oversight", exact: true })
    .last()
    .click();
  await page.getByRole("link", { name: "Claim decision", exact: true }).click();
}

test("recorded assessor evidence survives navigation but stays out of member content", async ({
  page,
}) => {
  await page.goto("/claims/review?preview=ai-governance");
  await page
    .getByLabel("Amount approved (SGD)", { exact: true })
    .fill("150.00");
  const rationale =
    "PRIVATE override: reviewed clarification covers this item.";
  const reference = "Reviewed policy clarification";
  await page
    .getByLabel("Reason for overriding the concern (required)")
    .fill(rationale);
  await page
    .getByLabel("Supporting reference (required)")
    .selectOption(reference);
  await page
    .getByLabel("Explanation to member", { exact: true })
    .fill("The reviewed policy covers the full claim.");
  await page
    .getByRole("button", { name: "Record approval", exact: true })
    .click();
  await expect(
    page.getByText("Sample decision recorded. No notification was sent."),
  ).toBeVisible();
  await page.getByRole("link", { name: "View member journey" }).click();
  await page.getByRole("tab", { name: "Claim outcome", exact: true }).click();
  await expect(
    page.getByText("S$150.00", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.locator("main")).not.toContainText(rationale);
  await expect(page.locator("main")).not.toContainText(reference);
  await returnToDecision(page);
  await expect(
    page.getByLabel("Reason for overriding the concern (required)"),
  ).toHaveValue(rationale);
  await expect(page.getByLabel("Supporting reference (required)")).toHaveValue(
    reference,
  );
  await expect(
    page.getByLabel("Amount approved (SGD)", { exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Reset samples" }).click();
  await expect(
    page.getByLabel("Internal assessment note (optional)"),
  ).toHaveValue("");
  await expect(page.getByLabel("Supporting reference (required)")).toHaveValue(
    "Policy schedule · Excluded expenses",
  );
});

test("owner next actions open the matching use editor from register and detail", async ({
  page,
}) => {
  await page.goto("/firm/ai-oversight");
  await page
    .getByRole("button", { name: "Next: Assign an owner", exact: true })
    .click();
  const editor = page.getByRole("dialog", { name: "Edit AI use" });
  await expect(editor).toBeVisible();
  await expect(editor.getByLabel("AI use name", { exact: true })).toHaveValue(
    "Slip extraction",
  );
  await editor.getByLabel("Accountable owner").selectOption("AI lead");
  await editor.getByRole("button", { name: "Save use", exact: true }).click();
  const list = page.getByRole("list", { name: "Registered AI uses" });
  await expect(
    list.getByRole("listitem").filter({ hasText: "Slip extraction" }),
  ).toContainText("AI lead");
  await list
    .getByRole("link", { name: "Slip extraction", exact: true })
    .click();
  await page.getByRole("button", { name: "Open action", exact: true }).click();
  await expect(editor.getByLabel("AI use name", { exact: true })).toHaveValue(
    "Slip extraction",
  );
  await expect(editor.getByLabel("Accountable owner")).toHaveValue("AI lead");
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Back to AI use register" }).click();
  await page
    .getByRole("button", {
      name: "Next: Validate the review dataset",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Validate the review dataset",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Review member disclosure",
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Show all actions", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Review member disclosure",
      exact: true,
    }),
  ).toBeVisible();
});

test("a registered use has a corresponding assessment task", async ({
  page,
}) => {
  await page.goto("/firm/ai-oversight");
  await page
    .getByRole("button", { name: "Register an AI use", exact: true })
    .click();
  await page.getByLabel("AI use name", { exact: true }).fill("New sample use");
  await page
    .getByLabel("Intended purpose", { exact: true })
    .fill("Test assessment routing");
  await page.getByLabel("Accountable owner").selectOption("Claims lead");
  await page.getByRole("button", { name: "Save use", exact: true }).click();
  await page
    .getByRole("button", {
      name: "Next: Assess purpose and risks before use",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Assess purpose and risks before use",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Owner for Assess purpose and risks before use"),
  ).toHaveValue("Claims lead");
  await page
    .getByRole("button", { name: "Mark complete", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Reopen", exact: true }),
  ).toBeVisible();
});
