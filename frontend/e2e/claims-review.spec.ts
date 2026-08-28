import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

function monitorRuntime(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      errors.push(`http ${response.status()}: ${response.url()}`);
    }
  });
  return errors;
}

async function assertAccessible(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(result.violations).toEqual([]);
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({
    path: testInfo.outputPath(`${name}.png`),
    fullPage: true,
    animations: "disabled",
  });
}

async function openClaimsReview(page: Page, tab: string) {
  await page.goto(`/claims/review?tab=${tab}`);
  await page.getByRole("combobox", { name: "Select company" }).waitFor();
  const companyGate = page.getByRole("heading", { name: "Select a company" });
  if (await companyGate.isVisible()) {
    await page.getByRole("button", { name: /\d+ members$/ }).first().click();
  }
}

test("signed-in administrator can use every Claims Review tab", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "queue");

  const queue = page.getByRole("tab", { name: "Queue" });
  const messages = page.getByRole("tab", { name: /Messages/ });
  const reviewRules = page.getByRole("tab", { name: "Review rules" });
  const claimSettings = page.getByRole("tab", { name: "Claim settings" });
  await expect(queue).toHaveAttribute("aria-selected", "true");
  await expect(messages).toBeVisible();
  await expect(reviewRules).toBeVisible();
  await expect(claimSettings).toBeVisible();
  await expect(page.getByText("Claims review queue")).toBeVisible();

  await messages.click();
  await expect(messages).toHaveAttribute("aria-selected", "true");
  const conversationSearch = page.getByRole("searchbox", {
    name: "Search conversations",
  });
  await expect(conversationSearch).toBeVisible();
  await expect(page.getByLabel("Conversation view")).toBeVisible();

  await reviewRules.click();
  await expect(
    page.getByRole("button", { name: "Duplicate from another company" }),
  ).toBeVisible();
  const configuredClaimType = page.getByText("GP (General Practitioner)", {
    exact: true,
  });
  if (await configuredClaimType.isVisible()) {
    await page.getByRole("button", { name: "Customize" }).first().click();
    await expect(page.getByText("Use this setup")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save rules" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
  } else {
    await expect(
      page.getByText("The benefit year covering today is not live."),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Review launch readiness" }),
    ).toBeVisible();
  }
  await screenshot(page, testInfo, "review-rules");
  await assertAccessible(page);

  await claimSettings.click();
  await expect(
    page.getByRole("heading", { name: "Required documents by claim type" }),
  ).toBeVisible();
  if (
    await page
      .getByText("Invoice or receipt", { exact: true })
      .first()
      .isVisible()
  ) {
    await expect(
      page.getByText("Invoice or receipt", { exact: true }).first(),
    ).toBeVisible();
  } else {
    await expect(page.getByText(/No claim types are available/)).toBeVisible();
  }
  await screenshot(page, testInfo, "claim-settings");
  await assertAccessible(page);

  expect(runtimeErrors).toEqual([]);
});

test("message workbench supports search and empty inboxes", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "messages");

  const search = page.getByRole("searchbox", { name: "Search conversations" });
  await expect(search).toBeVisible();
  await search.fill("no-such-conversation");
  await expect(page.getByText("No matching conversations")).toBeVisible();
  await page.getByRole("button", { name: "Clear search field" }).click();

  await page.getByRole("button", { name: "All", exact: true }).click();
  const conversationList = page.getByRole("list", { name: "Conversations" });
  const hasConversations = await conversationList.isVisible();
  if (hasConversations) {
    await expect(conversationList).toBeVisible();
  } else {
    await expect(page.getByText("No conversations yet")).toBeVisible();
  }
  const scrollOwners = await page.evaluate(() => {
    const main = document.querySelector("main");
    const inbox = document.querySelector('[aria-label="Conversation inbox"]');
    if (!main || !inbox) throw new Error("Message workbench shell is missing");
    const nested = Array.from(inbox.querySelectorAll<HTMLElement>("*")).filter(
      (element) => {
        const overflow = getComputedStyle(element).overflowY;
        return (
          (overflow === "auto" || overflow === "scroll") &&
          element.scrollHeight > element.clientHeight
        );
      },
    );
    return {
      mainRange: main.scrollHeight - main.clientHeight,
      nestedCount: nested.length,
    };
  });
  if (hasConversations) expect(scrollOwners.mainRange).toBeGreaterThan(0);
  expect(scrollOwners.nestedCount).toBe(0);
  await screenshot(page, testInfo, "message-inbox");
  await assertAccessible(page);

  const firstConversation = conversationList.getByRole("button").first();
  if (hasConversations && (await firstConversation.count()) > 0) {
    await firstConversation.click();
    await expect(page.getByLabel("Selected conversation")).toBeVisible();
    await screenshot(page, testInfo, "message-thread");
    const back = page.getByRole("button", { name: "Back to message inbox" });
    if (await back.isVisible()) await back.click();
  }

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  expect(runtimeErrors).toEqual([]);
});

test("message workbench recovers when the final pagination item is replied to", async ({
  page,
}, testInfo) => {
  let total = 26;
  const conversations = Array.from({ length: 26 }, (_, index) => ({
    subject: {
      kind: "claim",
      id: `mock-${index + 1}`,
      claim_kind: "standard",
      claim_type: "Outpatient treatment",
      product_code: "GHS",
      incurred_date: "2026-08-01",
      amount_claimed: 100 + index,
      currency: "SGD",
      status: "submitted",
    },
    last_message: {
      id: `message-${index + 1}`,
      claim_id: `mock-${index + 1}`,
      enquiry_id: null,
      author_type: "member",
      author_name: `Member ${index + 1}`,
      subject: "Question about my claim",
      body: `Could you check claim ${index + 1}?`,
      event: null,
      created_at: new Date(Date.now() - (26 - index) * 60_000).toISOString(),
      mine: false,
      unread: false,
    },
    message_count: 1,
    unread: 0,
    employee: {
      id: `employee-${index + 1}`,
      staff_id: `STAFF-${String(index + 1).padStart(3, "0")}`,
      employee_name: `Member ${index + 1}`,
    },
  }));

  await page.route(/\/api\/v1\/conversations\?/, async (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 25);
    const visible = conversations.slice(0, total);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total,
        offset,
        limit,
        unread_total: 0,
        items: visible.slice(offset, offset + limit),
      }),
    });
  });

  await page.route(
    /\/api\/v1\/claims\/mock-\d+\/messages(?:\/read)?$/,
    async (route) => {
      const url = route.request().url();
      if (url.endsWith("/read")) {
        await route.fulfill({ status: 200, json: { marked: 0 } });
        return;
      }
      const claimId = url.match(/claims\/(mock-\d+)\//)?.[1] ?? "mock-26";
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          json: [
            {
              id: "message-last",
              claim_id: claimId,
              enquiry_id: null,
              author_type: "member",
              author_name: "Member 26",
              subject: "Question about my claim",
              body: "Could you check this claim?",
              event: null,
              created_at: new Date().toISOString(),
              mine: false,
              unread: false,
            },
          ],
        });
        return;
      }
      total = 25;
      await route.fulfill({
        status: 201,
        json: {
          id: "message-reply",
          claim_id: claimId,
          enquiry_id: null,
          author_type: "broker",
          author_name: "Demo Broker Admin",
          subject: "A message about your claim",
          body: "We are checking this now.",
          event: null,
          created_at: new Date().toISOString(),
          mine: true,
          unread: false,
        },
      });
    },
  );

  await openClaimsReview(page, "messages");
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("26–26 of 26")).toBeVisible();

  const lastConversation = page
    .getByRole("list", { name: "Conversations" })
    .getByRole("button")
    .first();
  if (await lastConversation.isVisible()) await lastConversation.click();

  const reply = page.getByRole("textbox", {
    name: /Write to the member/,
  });
  await screenshot(page, testInfo, "message-last-page-thread");
  await reply.fill("We are checking this now.");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByText("1–25 of 25")).toBeVisible();
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled();
  await expect(page.getByText("Sent to the member")).toBeHidden({ timeout: 10_000 });
  await assertAccessible(page);
});

test("Claims Review stays within the viewport", async ({ page }, testInfo) => {
  const runtimeErrors = monitorRuntime(page);
  await openClaimsReview(page, "ai-extraction");
  await expect(
    page.getByRole("button", { name: "Duplicate from another company" }),
  ).toBeVisible();
  await expect(
    page.getByText("The benefit year covering today is not live."),
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await screenshot(page, testInfo, "review-rules-mobile");
  await assertAccessible(page);
  expect(runtimeErrors).toEqual([]);
});

test("claim workspace keeps form details readable and documents in context", async ({
  page,
}, testInfo) => {
  if (testInfo.project.name === "desktop-chromium") {
    await page.setViewportSize({ width: 1920, height: 1080 });
  }
  const runtimeErrors = monitorRuntime(page);
  const document = {
    id: "mock-document",
    file_name: "hospital-invoice.png",
    doc_type: "finalised_tax_invoice",
    mime_type: "image/png",
    size_bytes: 68,
    sha256: "mock-sha",
    created_at: "2026-06-27T08:00:00",
  };
  const secondDocument = {
    ...document,
    id: "mock-document-2",
    file_name: "hospital-summary.png",
    doc_type: "discharge_summary",
    sha256: "mock-sha-2",
  };
  const pdfDocument = {
    ...document,
    id: "mock-document-3",
    file_name: "hospital-bill.pdf",
    doc_type: "itemised_tax_invoice",
    mime_type: "application/pdf",
    sha256: "mock-sha-3",
  };
  const baseClaim = {
    client_id: "00000000-0000-0000-0000-000000000011",
    policy_year_id: "mock-policy-year",
    employee_id: "mock-employee",
    staff_id: "100009",
    employee_name: "Raymond Chow",
    case_type: "claim",
    origin: "portal",
    received_via: null,
    received_on: null,
    requested_by: null,
    benefit_key: null,
    referral_document_id: null,
    referral_document: null,
    referral_not_applicable: false,
    related_claim_id: null,
    related_claim: null,
    invoice_number: "INV-2026-001",
    doctor_name: null,
    remarks: null,
    currency: "SGD",
    amount_converted: null,
    fx_state: "not_required",
    fx_rate: null,
    fx_rate_date: null,
    fx_source: null,
    fx_stale: false,
    fx_acknowledged_at: null,
    policy_currency: "SGD",
    amount_approved: null,
    status: "submitted",
    dependant_id: null,
    dependant_name: null,
    submitted_at: "2026-06-27T08:00:00",
    decided_at: null,
    decision_notes: null,
    created_at: "2026-06-27T08:00:00",
    ai_review: null,
    remaining_limit: 20000,
    unread_member_messages: 0,
    allowed_actions: ["approve", "assessment", "amend", "rerun_review"],
    reference_no: "CLM-2026-0009",
    sent_to_insurer_at: null,
    insurer_deadline_on: null,
    paid_on: null,
    payment_amount: null,
    hospital_type: null,
    hospital_type_derived: "government",
    admin_remarks: null,
    servicer_days: null,
    insurer_days: null,
    days_over_deadline: null,
    revision: 1,
    amended_at: null,
    amended_by: null,
    member_editable: true,
    member_edit_block: null,
  };
  let insuredClaim = {
    ...baseClaim,
    id: "mock-insured",
    claim_kind: "insured",
    product_code: "GHS",
    flex_category_name: null,
    claim_type: "Hospital treatment",
    sub_type: "Hospitalisation/Day Surgery/Other Inpatient Treatment",
    incurred_date: "2026-06-26",
    provider_name: "Aptus Surgery Centre",
    diagnosis: "Appendicitis",
    amount_claimed: 14126.13,
    documents: [document, secondDocument, pdfDocument],
    admission_date: "2026-06-26",
    discharge_date: "2026-06-29",
    taxable: true,
    cpf_claimable: true,
    is_inpatient: true,
  };
  const flexClaim = {
    ...baseClaim,
    id: "mock-flex",
    claim_kind: "flex",
    product_code: null,
    flex_category_name: "Dental",
    claim_type: "Dental",
    sub_type: null,
    incurred_date: "2026-07-12",
    provider_name: "Demo Dental Surgery",
    diagnosis: "Dental treatment",
    amount_claimed: 180,
    documents: [],
    admission_date: null,
    discharge_date: null,
    taxable: true,
    cpf_claimable: false,
    is_inpatient: false,
  };

  await page.route(/\/api\/v1\/claims\?.*/, async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        total: 2,
        offset: 0,
        limit: 10,
        items: [insuredClaim, flexClaim],
      },
    });
  });
  await page.route(/\/api\/v1\/claims\/(mock-insured|mock-flex)$/, async (route) => {
    const claim = route.request().url().endsWith("mock-flex")
      ? flexClaim
      : insuredClaim;
    await route.fulfill({ status: 200, json: claim });
  });
  await page.route(/\/api\/v1\/claims\/(mock-insured|mock-flex)\/messages$/, async (route) => {
    await route.fulfill({ status: 200, json: [] });
  });
  await page.route(/\/api\/v1\/claims\/(mock-insured|mock-flex)\/review$/, async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        id: "mock-review",
        status: "complete",
        verdict: "clean",
        confidence: 0.98,
        summary: "The submitted details match the document.",
        stage: "persist",
        progress_current: 5,
        progress_total: 5,
        attempt: 1,
        started_at: "2026-06-27T08:01:00",
        heartbeat_at: "2026-06-27T08:02:00",
        completed_at: "2026-06-27T08:02:00",
        error_code: null,
        deterministic_short_circuit: false,
        review_config_label: "Demo review",
        review_config_fingerprint: "mock",
        created_at: "2026-06-27T08:01:00",
        extractions: [],
        amount_breakdown: {
          status: "match",
          claimed_amount: 14126.13,
          claimed_currency: "SGD",
          totals: [{ currency: "SGD", amount: 14126.13 }],
          difference: 0,
          note: "The document total matches the amount claimed.",
          lines: [
            {
              document_id: "mock-document",
              file_name: "hospital-invoice.png",
              document_type: "finalised tax invoice",
              invoice_number: "INV-2026-001",
              amount: 14126.13,
              currency: "SGD",
              confidence: 0.98,
              included_in_total: true,
              resolution: "included",
              note: "Included in the document total.",
            },
            {
              document_id: "mock-document-2",
              file_name: "hospital-summary.png",
              document_type: "discharge summary",
              invoice_number: null,
              amount: null,
              currency: null,
              confidence: 0,
              included_in_total: false,
              resolution: "no_amount",
              note: "No positive invoice or receipt total was read from this document.",
            },
            {
              document_id: "mock-document-3",
              file_name: "hospital-bill.pdf",
              document_type: "itemised tax invoice",
              invoice_number: "INV-2026-001",
              amount: 14126.13,
              currency: "SGD",
              confidence: 0.93,
              included_in_total: false,
              resolution: "duplicate",
              note: "Not added again because another document carries the same invoice number.",
            },
          ],
        },
        field_comparisons: [],
        rule_results: [],
        vision_checks: [],
        model: null,
        input_tokens: null,
        output_tokens: null,
        cost_estimate_usd: null,
        error_detail: null,
        superseded: false,
      },
    });
  });
  await page.route(
    /\/api\/v1\/claims\/mock-insured\/documents\/mock-document(?:-2)?\/download$/,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
          "base64",
        ),
      });
    },
  );
  await page.route(
    /\/api\/v1\/claims\/mock-insured\/documents\/mock-document-3\/download$/,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/pdf",
        body: Buffer.from("%PDF-1.4\n%%EOF", "utf8"),
      });
    },
  );
  await page.route(/\/api\/v1\/claims\/mock-insured\/assessment$/, async (route) => {
    const body = route.request().postDataJSON() as { admin_remarks?: string };
    insuredClaim = { ...insuredClaim, admin_remarks: body.admin_remarks ?? null };
    await route.fulfill({ status: 200, json: insuredClaim });
  });

  await openClaimsReview(page, "queue");
  await page.getByText("Hospital treatment", { exact: true }).first().click();

  const workspace = page.getByRole("dialog", { name: "Hospital treatment" });
  await expect(workspace.getByRole("heading", { name: "Form details" })).toBeVisible();
  await expect(
    workspace.getByText("The submitted claim and the assessor details recorded against it."),
  ).toHaveCount(0);
  await expect(
    workspace.getByText("Select a file to review it without leaving the claim."),
  ).toHaveCount(0);
  if (testInfo.project.name === "desktop-chromium") {
    const headerBox = await workspace.getByTestId("claim-workspace-header").boundingBox();
    expect(headerBox?.height).toBeLessThan(120);
    const workspaceBox = await workspace.boundingBox();
    const documentPaneBox = await workspace.getByTestId("claim-document-pane").boundingBox();
    if (!workspaceBox || !documentPaneBox) {
      throw new Error("Claim workspace panes have no layout box");
    }
    expect(documentPaneBox.x + documentPaneBox.width).toBeLessThanOrEqual(
      workspaceBox.x + workspaceBox.width + 1,
    );
  }
  await expect(
    workspace.getByText("Admission date", { exact: true }).locator(".."),
  ).toContainText("2026-06-26");
  await expect(workspace.getByText("2026-06-29", { exact: true })).toBeVisible();
  await expect(
    workspace.getByRole("heading", { name: "Document amount reconciliation" }),
  ).toBeVisible();
  await expect(workspace.getByText("Document total matches", { exact: true })).toBeVisible();
  await expect(workspace.getByText("SGD 14,126.13", { exact: true }).first()).toBeVisible();
  await expect(workspace.getByText("Taxable", { exact: true })).toHaveCount(0);
  await expect(workspace.getByText("CPF claimable", { exact: true })).toHaveCount(0);
  const invoicePreview = workspace.getByRole("img", {
    name: "Preview of hospital-invoice.png",
  });
  await expect(invoicePreview).toBeVisible();
  await expect(invoicePreview).toHaveCSS("object-fit", "contain");

  const openInvoice = workspace.getByRole("button", {
    name: "Open hospital-invoice.png full screen",
  });
  await openInvoice.click();
  const imageViewer = page.getByRole("dialog", {
    name: "Full-screen preview of hospital-invoice.png",
  });
  await expect(imageViewer).toBeVisible();
  await expect(
    imageViewer.getByText("Scroll to zoom · Drag to pan · Double-click to reset"),
  ).toBeVisible();

  const interactivePreview = imageViewer.getByRole("region", {
    name: "Interactive preview of hospital-invoice.png",
  });
  await interactivePreview.hover();
  await page.mouse.wheel(0, -300);
  await expect(interactivePreview).not.toHaveAttribute("data-zoom", "1.00");

  const viewerImage = imageViewer.getByRole("img", {
    name: "Preview of hospital-invoice.png",
  });
  const transformBeforePan = await viewerImage.evaluate((element) => element.style.transform);
  const previewBox = await interactivePreview.boundingBox();
  if (!previewBox) throw new Error("Full-screen document preview has no layout box");
  await page.mouse.move(
    previewBox.x + previewBox.width / 2,
    previewBox.y + previewBox.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    previewBox.x + previewBox.width / 2 + 60,
    previewBox.y + previewBox.height / 2 - 60,
    { steps: 4 },
  );
  await page.mouse.up();
  await expect
    .poll(() => viewerImage.evaluate((element) => element.style.transform))
    .not.toBe(transformBeforePan);
  await screenshot(page, testInfo, "claim-document-fullscreen-zoom");
  await assertAccessible(page);

  await interactivePreview.dblclick();
  await expect(interactivePreview).toHaveAttribute("data-zoom", "1.00");
  await imageViewer.getByRole("button", { name: "Close full-screen preview" }).click();
  await expect(imageViewer).toBeHidden();
  await expect(openInvoice).toBeFocused();

  await workspace.getByRole("tab", { name: "hospital-summary.png" }).click();
  await expect(
    workspace.getByRole("img", { name: "Preview of hospital-summary.png" }),
  ).toBeVisible();
  await workspace.getByRole("tab", { name: "hospital-bill.pdf" }).click();
  const inlinePdf = workspace.getByTitle("Preview of hospital-bill.pdf");
  await expect(inlinePdf).toBeVisible();
  await expect(inlinePdf).toHaveAttribute("src", /#view=Fit&toolbar=1&navpanes=0$/);
  await workspace
    .getByRole("button", { name: "Open hospital-bill.pdf full screen" })
    .click();
  const pdfViewer = page.getByRole("dialog", {
    name: "Full-screen preview of hospital-bill.pdf",
  });
  await expect(pdfViewer).toBeVisible();
  await expect(pdfViewer.getByTitle("Full-screen preview of hospital-bill.pdf")).toHaveAttribute(
    "src",
    /#view=Fit&toolbar=1&navpanes=0$/,
  );
  await pdfViewer.getByRole("button", { name: "Close full-screen preview" }).click();
  await workspace.getByRole("tab", { name: "hospital-invoice.png" }).click();
  await expect(workspace.getByText("Correct the claim", { exact: true })).toHaveCount(0);
  await expect(workspace.getByText("Assessment", { exact: true })).toHaveCount(0);

  await workspace.getByRole("button", { name: "Edit", exact: true }).click();
  const formPane = workspace.getByTestId("claim-form-pane");
  await expect
    .poll(() =>
      formPane.evaluate((element) => element.scrollWidth - element.clientWidth),
    )
    .toBeLessThanOrEqual(1);
  await screenshot(page, testInfo, "claim-review-workspace-edit");
  await expect(workspace.getByLabel("Admission date")).toHaveValue("2026-06-26");
  await expect(workspace.getByLabel("Discharge date")).toHaveValue("2026-06-29");
  await workspace.getByLabel("Admin remark").fill("Checked against invoice");
  await workspace.getByRole("button", { name: "Save changes" }).click();
  await expect(workspace.getByText("Checked against invoice", { exact: true })).toBeVisible();
  const savedToast = page.getByText("Form details updated", { exact: true });
  await expect(savedToast).toBeVisible();
  await expect(savedToast).toBeHidden({
    timeout: 10_000,
  });
  await expect(workspace.getByRole("heading", { name: "Form details" })).toBeInViewport();
  await screenshot(page, testInfo, "claim-review-workspace-insured");
  await assertAccessible(page);

  await workspace.getByRole("button", { name: "Close" }).click();
  await page.getByText("Dental", { exact: true }).first().click();
  const flexWorkspace = page.getByRole("dialog", { name: "Dental" });
  await expect(flexWorkspace.getByText("Taxable", { exact: true })).toBeVisible();
  await expect(flexWorkspace.getByText("CPF claimable", { exact: true })).toBeVisible();
  await expect(flexWorkspace.getByText("Admission date", { exact: true })).toHaveCount(0);
  await expect(flexWorkspace.getByText("Discharge date", { exact: true })).toHaveCount(0);
  await screenshot(page, testInfo, "claim-review-workspace-flex");
  await assertAccessible(page);

  expect(runtimeErrors).toEqual([]);
});
