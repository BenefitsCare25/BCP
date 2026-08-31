import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const API = "/api/v1";

function runtimeMonitor(page: Page) {
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

async function installSession(page: Page, clientId: string, policyYearId: string) {
  await page.addInitScript(
    ({ client, year }) => {
      localStorage.setItem(
        "inspro-session",
        JSON.stringify({
          state: {
            activeClientId: client,
            currentPolicyYearId: year,
            policyYearClientId: client,
          },
          version: 0,
        }),
      );
    },
    { client: clientId, year: policyYearId },
  );
}

function preview(mapped: boolean) {
  return {
    additions: [
      {
        row: 2,
        record_type: "employee",
        name: "Mapped Member",
        staff_id: "MAP-1",
        nric_masked: null,
        target_id: null,
        effective: null,
        field_diffs: [],
      },
    ],
    changes: [],
    deletions: [],
    missing: [],
    issues: [],
    warnings: [],
    counts: {
      additions: 1,
      changes: 0,
      deletions: 0,
      missing: 0,
      unchanged: 0,
      issues: 0,
      dropped_rows: 0,
      roster_total: 0,
    },
    missing_digest: "missing-digest",
    roster_mapping: {
      sheet_name: "Employees",
      fingerprint: "employee-template-fingerprint",
      digest: mapped ? "reviewed-mapping-digest" : "unresolved-mapping-digest",
      reused_profile: false,
      unresolved: !mapped,
      required_missing: [],
      columns: [
        {
          index: 0,
          source_column: "Staff ID",
          attribute_id: "staff_id",
          display_name: "Staff ID",
          status: "mapped",
          source: "known_header",
          non_empty_count: 1,
        },
        {
          index: 1,
          source_column: "Business Unit",
          attribute_id: mapped ? "division" : null,
          display_name: mapped ? "Division" : null,
          status: mapped ? "mapped" : "unresolved",
          source: mapped ? "manual" : "unresolved",
          non_empty_count: 1,
        },
      ],
      available_attributes: [
        {
          attribute_id: "staff_id",
          display_name: "Staff ID",
          is_pii: false,
          allow_matching: false,
          derived: false,
        },
        {
          attribute_id: "division",
          display_name: "Division",
          is_pii: false,
          allow_matching: true,
          derived: false,
        },
      ],
    },
  };
}

test("unknown employee columns require mapping and a recalculated preview", async ({
  page,
  request,
}, testInfo: TestInfo) => {
  const me = await request.get(`${API}/me`);
  expect(me.ok(), await me.text()).toBeTruthy();
  const client = (await me.json()).accessible_clients[0];
  const yearsResponse = await request.get(`${API}/policy-years`, {
    headers: { "X-Inspro-Client": client.id },
  });
  expect(yearsResponse.ok(), await yearsResponse.text()).toBeTruthy();
  const year = (await yearsResponse.json())[0];
  await installSession(page, client.id, year.id);

  const runtimeErrors = runtimeMonitor(page);
  let previewCount = 0;
  await page.route("**/api/v1/policy-years/*/adc/preview", async (route) => {
    previewCount += 1;
    await route.fulfill({ status: 200, json: preview(previewCount > 1) });
  });
  await page.route("**/api/v1/policy-years/*/adc/apply", async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        added: 1,
        changed: 0,
        deleted: 0,
        missing_terminated: 0,
        unchanged: 0,
        rematched: 1,
        issues: [],
        flex_errors: [],
        mapping_profile_saved: true,
      },
    });
  });

  await page.goto("/policy-admin/member-listing?tab=employees");
  await page.getByLabel("Upload listing").setInputFiles({
    name: "listing.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("browser-mocked-workbook"),
  });

  await expect(
    page.getByRole("heading", { name: "Review listing changes" }),
  ).toBeVisible();
  await expect(page.getByText("Needs mapping", { exact: true })).toBeVisible();
  await expect(page.getByText("Business Unit", { exact: true })).toBeVisible();
  const apply = page.getByRole("button", { name: "Apply 1 change" });
  await expect(apply).toBeDisabled();

  await page.getByRole("combobox", { name: "Map Business Unit" }).click();
  await page.getByRole("option", { name: /Division.*eligibility/ }).click();
  await expect(page.getByText("Recheck required", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Recheck changes" }).click();

  await expect(page.getByText("Ready", { exact: true })).toBeVisible();
  await expect(apply).toBeEnabled();
  await page.screenshot({
    path: testInfo.outputPath(`roster-mapping-${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);

  await apply.click();
  await expect(page.getByText("Applied — 1 added")).toBeVisible();
  expect(previewCount).toBe(2);
  expect(runtimeErrors).toEqual([]);
});
