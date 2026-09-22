import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

interface PolicyYear {
  id: string;
  start_date: string;
  end_date: string;
}

interface SessionContext {
  clientId: string;
  year: PolicyYear;
}

interface ReportVersion {
  id: string;
  report_type: string;
  scope_key: null;
  version_no: number;
  mode: "versioned";
  label: string | null;
  file_name: string;
  size_bytes: number;
  summary: { masked: false; member_count: number };
  generated_by_user_id: string;
  generated_by: string;
  report_label: string;
  created_at: string;
}

async function installSession(
  page: Page,
  request: APIRequestContext,
): Promise<SessionContext> {
  const meResponse = await request.get("/api/v1/me");
  expect(meResponse.ok()).toBe(true);
  const me = await meResponse.json();
  expect(me.active_client_id).toBeTruthy();

  const yearsResponse = await request.get("/api/v1/policy-years", {
    headers: { "X-Inspro-Client": me.active_client_id },
  });
  expect(yearsResponse.ok()).toBe(true);
  const [year] = (await yearsResponse.json()) as PolicyYear[];
  expect(year?.id).toBeTruthy();

  await page.addInitScript(
    ({ clientId, yearId }) => {
      localStorage.setItem(
        "inspro-session",
        JSON.stringify({
          state: {
            activeClientId: clientId,
            currentPolicyYearId: yearId,
            policyYearClientId: clientId,
          },
          version: 0,
        }),
      );
    },
    { clientId: me.active_client_id, yearId: year.id },
  );

  return { clientId: me.active_client_id, year };
}

function versions(count: number): ReportVersion[] {
  return Array.from({ length: count }, (_, index) => {
    const version = count - index;
    return {
      id: `00000000-0000-0000-0000-${String(version).padStart(12, "0")}`,
      report_type: "benefit_selection",
      scope_key: null,
      version_no: version,
      mode: "versioned",
      label:
        version === count
          ? "Final enrollment submission with reconciled dependant elections"
          : null,
      file_name: `benefit-selection-v${version}.xlsx`,
      size_bytes: 4096 + version,
      summary: { masked: false, member_count: 120 + version },
      generated_by_user_id: "00000000-0000-0000-0000-000000000019",
      generated_by: "Synthetic Broker Reviewer",
      report_label: "Benefit Selection & Leave",
      created_at: new Date(Date.UTC(2026, 8, 21 - index, 4, 0)).toISOString(),
    };
  });
}

async function mockReportHistory(
  page: Page,
  listVersions: () => Promise<{ status: number; json?: ReportVersion[] }>,
) {
  const items = versions(25);
  await page.route(
    /\/api\/v1\/policy-years\/[^/]+\/report-versions(?:\/status)?(?:\?.*)?$/,
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/status")) {
        await route.fulfill({
          json: {
            latest: items[0],
            is_stale: false,
            has_movement: false,
          },
        });
        return;
      }
      if (url.pathname.endsWith("/report-versions")) {
        const response = await listVersions();
        await route.fulfill({
          status: response.status,
          json: response.json,
        });
        return;
      }
      await route.fallback();
    },
  );
}

async function syntheticEmployeeRoster(): Promise<Buffer> {
  const outputDirectory = await mkdtemp(join(tmpdir(), "inspro-report-demo-"));
  try {
    const generated = spawnSync(
      "uv",
      [
        "run",
        "python",
        "tests/fixtures/generate_synthetic_roster.py",
        "3",
        outputDirectory,
      ],
      {
        cwd: resolve("../backend"),
        encoding: "utf8",
        shell: process.platform === "win32",
      },
    );
    if (generated.status !== 0) {
      throw new Error(
        generated.stderr || generated.stdout || "Synthetic roster generation failed",
      );
    }
    return await readFile(join(outputDirectory, "synthetic_employees_3.xlsx"));
  } finally {
    await rm(outputDirectory, { recursive: true, force: true });
  }
}

test("a real demo insurer report is retained and appears in scoped history", async ({
  page,
  request,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "desktop-chromium",
    "Generate the shared demo workbook once; responsive history states are covered separately.",
  );
  const { clientId, year: selectedYear } = await installSession(page, request);
  const headers = { "X-Inspro-Client": clientId };
  const insurer = "AIA Singapore";
  const existingSetup = await request.get(
    `/api/v1/policy-years/${selectedYear.id}/product-setups/GHS`,
    { headers },
  );
  expect([200, 404]).toContain(existingSetup.status());
  const savedSetup = existingSetup.ok()
    ? ((await existingSetup.json()) as {
        template_version: number;
        updated_at: string;
      })
    : null;
  const setup = await request.post(
    `/api/v1/policy-years/${selectedYear.id}/product-setups/GHS/confirm`,
    {
      headers,
      data: {
        answers: {
          header: { insurer },
          plans: [{ code: "CORE", label: "Core Medical", selected: true }],
          categories: [
            {
              category: "All Employees",
              participation: "Compulsory",
              plan_code: "CORE",
            },
          ],
        },
        template_version: savedSetup?.template_version ?? 1,
        expected_updated_at: savedSetup?.updated_at,
      },
    },
  );
  expect(setup.ok(), await setup.text()).toBe(true);

  const roster = await syntheticEmployeeRoster();
  const upload = await request.post("/api/v1/employees/upload", {
    headers,
    multipart: {
      policy_year_id: selectedYear.id,
      file: {
        name: "synthetic-employees.xlsx",
        mimeType:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        buffer: roster,
      },
    },
  });
  expect(upload.ok(), await upload.text()).toBe(true);
  const uploadResult = (await upload.json()) as {
    inserted: number;
    skipped: number;
    errors: string[];
  };
  expect(uploadResult.errors).toEqual([]);
  expect(uploadResult.inserted + uploadResult.skipped).toBe(3);

  const catalogResponse = await request.get(
    `/api/v1/policy-years/${selectedYear.id}/reports/workbooks`,
    { headers },
  );
  expect(catalogResponse.ok(), await catalogResponse.text()).toBe(true);
  const catalog = (await catalogResponse.json()) as Array<{
    key: string;
    insurers: string[];
  }>;
  const insurerSubmission = catalog.find(
    (workbook) => workbook.key === "insurer-submission",
  );
  expect(insurerSubmission?.insurers).toContain(insurer);

  const query = new URLSearchParams({
    insurer,
    masked: "false",
  });
  const report = await request.get(
    `/api/v1/policy-years/${selectedYear.id}/reports/workbooks/insurer-submission?${query}`,
    { headers },
  );
  expect(report.ok(), await report.text()).toBe(true);
  expect(report.headers()["content-type"]).toContain(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
  const workbook = await report.body();
  expect(workbook.subarray(0, 2).toString("ascii")).toBe("PK");
  expect(workbook.length).toBeGreaterThan(1_000);
  await writeFile(testInfo.outputPath("demo-insurer-submission.xlsx"), workbook);

  await page.goto("/claims/reports?tab=pa");
  await page.getByRole("combobox", { name: "Insurer" }).click();
  await page.getByRole("option", { name: insurer }).click();
  const submissionRecord = page.getByText(/^Last sent/);
  await expect(submissionRecord).toBeVisible();
  await expect(submissionRecord).toContainText(/v\d+/);
  await page.getByRole("button", { name: "Open submission history" }).click();

  const dialog = page.getByRole("dialog", {
    name: new RegExp(`Submission history — ${insurer}`),
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-report-version-row]")).toHaveCount(1);
  await expect(dialog).toContainText("unmasked");
  await page.screenshot({
    path: testInfo.outputPath("demo-report-history.png"),
  });
});

test("report history shows the latest 10 or 20 copies with year context", async ({
  page,
  request,
}, testInfo) => {
  const { year } = await installSession(page, request);
  const items = versions(25);
  await mockReportHistory(page, async () => ({ status: 200, json: items }));

  await page.goto("/claims/reports?tab=flex");
  const opener = page.getByRole("button", { name: "Open submission history" });
  await opener.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Submission history" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(year.start_date.slice(0, 4));
  await expect(dialog).toContainText(year.end_date.slice(0, 4));
  await expect(dialog).toContainText(
    "This setting changes only how many copies are shown",
  );

  const displayCount = dialog.getByLabel("Report history display count");
  await expect(displayCount.locator("option")).toHaveText([
    "Latest 10",
    "Latest 20",
  ]);
  const rows = dialog.locator("[data-report-version-row]");
  await expect(rows).toHaveCount(10);
  await expect(rows.first()).toContainText("v25");
  await expect(dialog).toContainText("Showing 10 of 25 retained copies");

  await displayCount.selectOption("20");
  await expect(rows).toHaveCount(20);
  await expect(rows.last()).toContainText("v6");
  await expect(dialog).toContainText("Showing 20 of 25 retained copies");
  await expect(dialog).toContainText(
    "Older copies remain stored and are not affected by this display limit",
  );

  await expect
    .poll(() =>
      dialog.evaluate((element) => element.scrollWidth <= element.clientWidth),
    )
    .toBe(true);
  const accessibility = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);

  await page.screenshot({
    path: testInfo.outputPath("report-history-populated.png"),
  });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("report history recovers from an error and explains an empty period", async ({
  page,
  request,
}, testInfo) => {
  await installSession(page, request);
  let serveEmpty = false;
  await mockReportHistory(page, async () =>
    serveEmpty ? { status: 200, json: [] } : { status: 503 },
  );

  await page.goto("/claims/reports?tab=flex");
  await page.getByRole("button", { name: "Open submission history" }).click();
  const dialog = page.getByRole("dialog", { name: "Submission history" });
  const error = dialog.getByRole("alert");
  await expect(error).toContainText("Report history could not be loaded");
  await expect(error).toContainText("Check your connection, then try again");

  serveEmpty = true;
  await error.getByRole("button", { name: "Retry" }).click();
  await expect(dialog.getByText("Nothing sent yet", { exact: true })).toBeVisible();
  await expect(dialog).toContainText(
    "Downloading the report files the first retained copy",
  );
  await page.screenshot({
    path: testInfo.outputPath("report-history-empty.png"),
  });
});

test("report details stay behind an accessible contents preview", async ({
  page,
  request,
}, testInfo) => {
  await installSession(page, request);
  await page.goto("/claims/reports?tab=pa");

  const report = page.locator('[data-report-workbook="member-register"]');
  await expect(report).toBeVisible();
  await expect(report).not.toContainText("The full roster across every insurer");
  await expect(report).not.toContainText("Sheets:");

  const contents = report.getByRole("button", {
    name: "View Member Register contents",
  });
  if (testInfo.project.name === "mobile-chromium") {
    const triggerBox = await contents.boundingBox();
    if (!triggerBox) throw new Error("Contents trigger has no touch target");
    await page.touchscreen.tap(
      triggerBox.x + triggerBox.width / 2,
      triggerBox.y + triggerBox.height / 2,
    );
  } else {
    await contents.hover();
  }

  const preview = page.locator('[data-report-contents="member-register"]:visible');
  await expect(preview.getByText("Key columns:", { exact: true }).first()).toBeVisible();
  if (testInfo.project.name === "mobile-chromium") {
    await page.waitForTimeout(250);
    await expect(preview).toBeVisible();
  } else {
    await preview.hover();
    await expect(preview).toBeVisible();
  }
  await expect(preview).toContainText("Employee Coverage");
  await expect(preview).toContainText("Staff ID · Employee Name · Masked ID");
  await expect(contents).toHaveAttribute("aria-expanded", "true");
  const bounds = await preview.evaluate((element) => {
    const box = element.getBoundingClientRect();
    return {
      left: box.left,
      right: box.right,
      top: box.top,
      bottom: box.bottom,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(-1);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth + 1);
  expect(bounds.top).toBeGreaterThanOrEqual(-1);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewportHeight + 1);

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);

  await page.screenshot({
    path: testInfo.outputPath(`reports-clean-${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });
});
