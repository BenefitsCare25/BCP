import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { PortalTranslator } from "../src/i18n/portal";
import { readFileSync } from "node:fs";
const insuranceCopy = JSON.parse(readFileSync("e2e/fixtures/portal-insurance-copy.json", "utf8")) as { diagnosis: string[] };

const member = { id: "language-member", staff_id: "LANG-001", email: "language@example.test", display_name: "Alex Tan" };
const hr = { user_id: "language-hr", email: member.email, display_name: "Jamie Lim", role: "client_admin", client_id: "language-company", company_name: "Language Review Company", mfa_available: false, mfa_status: "none" };
const policyYear = { id: "language-year", year: 2026, start_date: "2026-01-01", end_date: "2026-12-31" };
const item = (name: string, value: string, properties = {}) => ({ number: "1", name, value, kind: "currency", properties, limits: [], sub_items: [] });
const line = (code: string, route: string, items: ReturnType<typeof item>[]) => ({
  product_code: code, product_name: `Original ${code} Policy`, care_route: route,
  plan_code: "A", plan_display_name: "Original Insurer Plan A", annual_policy_limit: "50000",
  benefit_schedule: { items }, covers_dependants: false, covered_dependants: [], financials: null,
});
const claim = {
  id: "language-claim", employee_id: "language-employee", employee_name: member.display_name,
  claim_kind: "insured", claim_type: "GP", product_code: "GCGP", status: "paid", claim_ref: "LANG-2026-001",
  incurred_date: "2026-09-18", created_at: "2026-09-18T02:00:00Z", submitted_at: "2026-09-18T02:00:00Z",
  amount_claimed: 88.5, amount_approved: 80, amount_paid: 80, currency: "SGD",
  provider_name: "Original Clinic Name", invoice_number: "INV-LANG-001", remarks: "Original employee note",
  documents: [], doc_slots: [], events: [], can_add_evidence: false, can_submit: false,
  submission_channel: "hr", submitted_by_name: hr.display_name,
};

const comparison = [
  { group: "Specialist Care", benefit: "Panel Specialists", qualifier: "on cashless basis · including Specialist Outpatient Clinics in Govt Restructured hospitals - on reimbursement basis", current: "500", elected: "As charged", kind: "currency" },
  { group: "Specialist Care", benefit: "Non Panel Specialists", qualifier: null, current: "500", elected: "3000", kind: "currency" },
  { group: "Diagnostic X-ray & Lab Test", benefit: "Panel", qualifier: null, current: "Refer to 1a", elected: "Refer to 1a", kind: "currency" },
  { group: "Diagnostic X-ray & Lab Test", benefit: "Non Panel", qualifier: null, current: null, elected: "Refer to 1b", kind: "currency" },
];
const tier = (n: number) => ({ key: `specialist::${n}`, tier_category_id: `specialist-${n}`, plan_code: String(n), label: `Plan${n}`, participation: "compulsory", dependant_participation: null, direction: n === 1 ? "same" : "upgrade", is_baseline: n === 1, is_current: n === 1, financials: null, price_tag: null, differences: n === 1 ? [] : comparison, differences_total: n === 1 ? 0 : 4 });
const enrollmentWindow = { id: "language-window", name: "Annual enrolment", status: "open", window_type: "open", opens_at: "2026-01-01T00:00:00Z", closes_at: "2030-12-31T00:00:00Z", allow_leave: false, allow_dependant_changes: false, member_self_service: true, allow_overdraft: false, product_scope: ["GCSP"], default_behavior: "deemed_keep_current" };
const claimProduct = (code: string, category: string, labels: string[]) => ({ product_code: code, product_name: code === "GCSP" ? "Group Clinical Specialist" : code === "GHS" ? "Group Hospital & Surgical" : "Group Clinical General Practitioner", plan_code: "1", plan_display_name: "Plan1", annual_policy_limit: "50000", covers_dependants: false, covered_dependant_ids: [], insurer: "Original Insurer", insurer_member_id: "LANG-001", sub_types: [], requires_referral: false, diagnosis_group: "gp", diagnosis_required: true, category,
  claim_types: labels.map((label, i) => ({ label, sub_type: category === "inpatient" ? label : null, scope_code: `scope-${i}`, scope_key: `${code}-${i}`, benefit_key: null, requires_doctor_name: false, supports_stay_dates: false, anchor_mode: null, doc_slots: [], doc_slots_by_sector: null })) });
const populatedOptions = { policy_year_start: "2026-01-01", policy_year_end: "2026-12-31", claimable_from: "2026-01-01", claimable_to: "2026-12-31", policy_currency: "SGD", currencies: ["SGD"], hospitals: [], flex: null, insured: [
  claimProduct("GCGP", "outpatient", ["GP (General Practitioner)", "TCM (Traditional Chinese Medicine)", "Physiotherapy"]),
  claimProduct("GCSP", "outpatient", ["SP (Specialist)"]),
  claimProduct("GHS", "inpatient", ["Follow up Pre-/Post-Hospitalisation", "Hospitalisation/Day Surgery/Other Inpatient Treatment", "Emergency Accidental Outpatient Treatment", "Kidney Dialysis/Cancer Treatment"]),
] };

async function mockPortals(page: Page, authenticated = true, populated = false) {
  const writes: string[] = [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!["GET", "HEAD"].includes(request.method()) && !path.endsWith("/auth/refresh")) writes.push(path);
    if (path.endsWith("/auth/login")) return route.fulfill({ status: 401, json: { detail: "Invalid credentials" } });
    if (path.endsWith("/auth/refresh")) return route.fulfill(authenticated ? { json: path.includes("/hr/")
      ? { status: "authenticated", access_token: "language-review-token", expires_at: "2100-01-01T00:00:00Z", me: hr }
      : { token: "language-review-token", expires_at: "2100-01-01T00:00:00Z", member } } : { status: 401, json: { detail: "No session." } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: hr });
    if (path.endsWith("/auth/security-status")) return route.fulfill({ json: { mfa_available: false, mfa_status: "none" } });
    if (path.endsWith("/portal/me")) return route.fulfill({ json: {
      member, employee: { id: "language-employee", employee_name: member.display_name, staff_id: member.staff_id },
      company: { slug: "language", name: "Language Review", legal_name: "Language Review Company" },
      policy_year: policyYear, access: { state: "active", capabilities: ["record", "claim", "elect", "entitlement"] },
      flex_eligible: false, enrollment_open: false,
    } });
    if (path.endsWith("/benefit-statement")) return route.fulfill({ json: {
      employee: { id: "language-employee", employee_name: member.display_name }, policy_year_id: policyYear.id,
      is_matched: true, attributes: [], dependants: [], flex: null,
      coverage: [line("GCGP", "gp", [item("Panel consultation", "As charged", { per_visit: "As charged", co_payment: "5" })]),
        line("GHS", "hospital", [item("Daily Room & Board", "250"), item("Intensive Care Unit", "500")]),
        ...(populated ? [line("GCSP", "specialist", [item("Specialist Care", "As charged"), item("Diagnostic X-ray & Lab Test", "Refer to 1a"), item("Outpatient Kidney Dialysis / Cancer Treatment (Per Policy Year)", "20000")])] : [])],
    } });
    if (path.endsWith("/conversations")) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20, unread_total: 0 } });
    if (path.endsWith("/enrollment/notices")) return route.fulfill({ json: { items: [], unread: 0 } });
    if (path.endsWith("/enrollment")) return route.fulfill({ json: populated ? { window: enrollmentWindow, enrollment: null, options: { products: [{ product_id: "specialist", product_code: "GCSP", product_name: "Group Clinical Specialist", employee_participation: "compulsory", dependant_participation: null, baseline_tier_category_id: "specialist-1", baseline_plan_code: "1", allow_plan_change: true, can_decline: false, dependant: null, tiers: [tier(1), tier(2)] }], flex_wallet: null, leave: null } } : { window: null, enrollment: null, options: null } });
    if (path.endsWith("/enrollment/form")) return route.fulfill({ json: { company_name: "Language Review Company", title: "Enrolment", closes_at: enrollmentWindow.closes_at, policy_start: "2026-01-01", policy_end: "2026-12-31", window_type: "open", intro_lines: [], eligibility_notes: [], clauses: [], documents: [], rules: [], compulsory: [], contributions: [], plans: [], dependants: [], latest: null, particulars: { name: member.display_name, staff_id: member.staff_id, id_masked: "****", email: member.email, contact_no: "", gender: "Male" } } });
    if (path.endsWith("/enrollment-forms/windows")) return route.fulfill({ json: [] });
    if (path.endsWith("/enrollment-forms")) return route.fulfill({ json: path.includes("/hr/")
      ? { items: [], total: 0, counts: { submitted: 0, acknowledged: 0 }, offset: 0, limit: 25 } : [] });
    if (path.endsWith("/dependants")) return route.fulfill({ json: [] });
    if (path.endsWith("/employees")) return route.fulfill({ json: { items: populated ? [{ id: "language-employee", name: member.display_name, staff_id: member.staff_id, period: "2026", dependants: [] }] : [], total: populated ? 1 : 0 } });
    if (path.endsWith("/claims/language-claim")) return route.fulfill({ json: claim });
    if (path.endsWith("/claims")) return route.fulfill({ json: { items: [claim], total: 1, offset: 0, limit: 50 } });
    if (path.endsWith("/utilization")) return route.fulfill({ json: { insured: [], flex: null } });
    if (path.endsWith("/claim-years")) return route.fulfill({ json: [policyYear] });
    if (path.endsWith("/coverage-options") || path.endsWith("/hr/claims/employees/language-employee/options")) return route.fulfill({ json: populated ? populatedOptions : { insured: [], flex: null, currencies: ["SGD"], hospitals: [] } });
    if (path.endsWith("/claim-diagnoses")) return route.fulfill({ json: { group: "gp", items: insuranceCopy.diagnosis.map(label => ({ label, icd10: "J06" })) } });
    return route.fulfill({ status: 404, json: { detail: "No active coverage." } });
  });
  return writes;
}

async function chooseLanguage(page: Page, value: "zh-SG" | "en-SG") {
  await page.locator(".portal-language-control select:visible").selectOption(value);
  await expect(page.locator("html")).toHaveAttribute("lang", value === "zh-SG" ? "zh-Hans-SG" : "en");
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("populated employee claims translate every insurance choice and search diagnoses in both languages", async ({ page }, info) => {
  const writes = await mockPortals(page, true, true);
  await page.goto("/portal/language/claims/new");
  await chooseLanguage(page, "zh-SG");
  const select = page.getByRole("combobox", { name: /^(理赔类型|Claim type)/ });
  const options = await select.locator("option").allTextContents();
  expect(options).toEqual(expect.arrayContaining(["全科门诊（GP）", "中医（TCM）", "物理治疗", "专科门诊（SP）", "住院前／出院后复诊", "住院／日间手术／其他住院治疗", "意外受伤紧急门诊治疗", "肾脏透析／癌症治疗"]));
  await select.selectOption("insured:GCGP:0");
  const search = page.getByPlaceholder("输入关键词搜索选项");
  await search.fill("上呼吸道");
  await page.getByRole("button", { name: "上呼吸道感染（URTI）／普通感冒", exact: true }).click();
  await expect(page.getByText("上呼吸道感染（URTI）／普通感冒", { exact: true })).toBeVisible();
  const before = writes.length;
  await chooseLanguage(page, "en-SG");
  await expect(select).toHaveValue("insured:GCGP:0");
  await expect(page.getByText("Upper respiratory tract infection (URTI) / Common cold", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear diagnosis" }).click();
  await page.getByPlaceholder("Start typing to search for options").fill("upper respiratory");
  await expect(page.getByRole("button", { name: "Upper respiratory tract infection (URTI) / Common cold", exact: true })).toBeVisible();
  await chooseLanguage(page, "zh-SG");
  await expect(select).toHaveValue("insured:GCGP:0");
  expect(writes.length).toBe(before);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("populated-claims-zh.png"), fullPage: true });
});

test("populated enrolment translates comparison headings, qualifiers, values and references without changing elections", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const writes = await mockPortals(page, true, true);
  await page.goto("/portal/language/enrollment?p=GCSP");
  await chooseLanguage(page, "zh-SG");
  const current = page.getByRole("radio", { name: /计划 1/ });
  const upgrade = page.getByRole("radio", { name: /计划 2/ });
  await expect(current).toBeChecked();
  await expect(upgrade).toBeVisible();
  await expect(page.getByText("增加保障", { exact: true })).toBeVisible();
  const comparison = page.locator(".enrolment-comparison");
  await expect(comparison.getByText("专科医疗", { exact: true }).first()).toBeVisible();
  await expect(comparison.getByText("指定专科医生", { exact: true })).toBeVisible();
  await expect(comparison.getByText("非指定专科医生", { exact: true })).toBeVisible();
  await expect(comparison.getByText("免现金结算；包括公立重组医院专科门诊（报销制）", { exact: true })).toBeVisible();
  await expect(comparison.getByText("按实际费用赔付", { exact: true })).toBeVisible();
  await expect(comparison.getByText("不受保", { exact: true })).toBeVisible();
  await expect(comparison.getByText("参见 1a", { exact: true })).toHaveCount(2);
  await expect(comparison.getByText("参见 1b", { exact: true })).toHaveCount(1);
  await expect(comparison.getByText("S$3,000", { exact: true })).toBeVisible();
  for (const text of ["Specialist Care", "Diagnostic X-ray & Lab Test", "As charged", "Not covered", "Refer to 1a", "Plan1"]) await expect(comparison.getByText(text, { exact: true })).toHaveCount(0);
  await upgrade.check();
  const before = writes.length;
  await chooseLanguage(page, "en-SG");
  await expect(page.getByRole("radio", { name: /Plan2/ })).toBeChecked();
  await expect(comparison.getByText("Panel Specialists", { exact: true })).toBeVisible();
  await expect(comparison.getByText("Refer to 1b", { exact: true })).toBeVisible();
  await chooseLanguage(page, "zh-SG");
  await expect(upgrade).toBeChecked();
  expect(writes.length).toBe(before);
  for (const width of info.project.name.startsWith("desktop") ? [1440, 1024, 940] : [393, 320]) {
    await page.setViewportSize({ width, height: 960 });
    await noOverflow(page);
  }
  await page.screenshot({ path: info.outputPath("populated-enrolment-comparison-zh.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("populated HR claim choices translate each fragment and retain the original selected value", async ({ page }, info) => {
  await mockPortals(page, true, true);
  await page.goto("/hr/claims/new");
  await chooseLanguage(page, "zh-SG");
  await page.getByText(member.display_name, { exact: true }).click();
  const select = page.getByRole("combobox", { name: /^(理赔类型|Claim type)/ });
  await expect(select.locator("option").filter({ hasText: "住院／日间手术／其他住院治疗" })).toHaveCount(1);
  const gp = select.locator("option").filter({ hasText: "全科门诊（GP）" });
  await expect(gp).toContainText("计划 1");
  await expect(gp).toContainText("团体全科门诊保险");
  await expect(gp).not.toContainText("Group Clinical General Practitioner");
  const value = await gp.getAttribute("value");
  await select.selectOption(value!);
  await chooseLanguage(page, "en-SG");
  await expect(select).toHaveValue(value!);
  await expect(select.locator("option:checked")).toContainText("GP (General Practitioner)");
  await chooseLanguage(page, "zh-SG");
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("populated-hr-claim-zh.png"), fullPage: true });
});

for (const role of ["portal", "hr"] as const) {
  test(`${role}: language switching preserves credentials, errors and preference`, async ({ page }) => {
    await mockPortals(page, false);
    await page.goto(role === "portal" ? "/portal/language/sign-in" : "/hr/sign-in?company=language");
    await page.locator(`#${role}-identifier`).fill(member.email);
    await page.locator(`#${role}-password`).fill("Language!Review123");
    await chooseLanguage(page, "zh-SG");
    await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
    await expect(page.locator(`#${role}-identifier`)).toHaveValue(member.email);
    await expect(page.locator(`#${role}-password`)).toHaveValue("Language!Review123");
    await page.getByRole("button", { name: "显示密码" }).click();
    await expect(page.locator(`#${role}-password`)).toHaveAttribute("type", "text");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("登录资料不正确");
    await chooseLanguage(page, "en-SG");
    await expect(page.getByRole("alert")).toContainText("Those details weren't recognised");
    await expect(page.locator(`#${role}-identifier`)).toHaveValue(member.email);
    await chooseLanguage(page, "zh-SG");
    await page.reload();
    await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
    await noOverflow(page);
  });
}

test("employee: Chinese coverage keeps policy facts and switching restores English", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const writes = await mockPortals(page);
  await page.goto("/portal/language");
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByText("您的福利", { exact: true })).toBeVisible();
  await expect(page.getByText("全科门诊", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("住院与手术", { exact: true })).toBeVisible();
  await page.goto("/portal/language/coverage?tab=benefits&p=gp");
  await expect(page.getByRole("tab", { name: "保障范围" })).toBeVisible();
  await expect(page.getByText("指定诊所", { exact: true })).toBeVisible();
  await expect(page.getByText(/共同支付额 S\$5/)).toBeVisible();
  await expect(page.getByText("Original Insurer Plan A", { exact: true })).toBeVisible();
  await noOverflow(page);
  await chooseLanguage(page, "en-SG");
  await expect(page.getByText("Panel clinic", { exact: true })).toBeVisible();
  await expect(page.getByText(/S\$5 co-pay per visit/)).toBeVisible();
  await expect(page).toHaveURL(/p=gp/);
  await chooseLanguage(page, "zh-SG");
  await page.goto("/portal/language/coverage?tab=benefits&p=hospital");
  await expect(page.getByText("病房与膳食费用", { exact: true })).toBeVisible();
  await expect(page.getByText(/每天 S\$250/)).toBeVisible();
  await page.getByText("完整保障明细", { exact: true }).click();
  await expect(page.getByText("每日病房与膳食费用", { exact: true })).toBeVisible();
  await expect(page.locator("dd").filter({ hasText: "S$50,000" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("chinese-coverage.png"), fullPage: true, animations: "disabled" });
  for (const width of testInfo.project.name.startsWith("desktop") ? [1440, 1180, 1024, 940] : [393, 320]) {
    await page.setViewportSize({ width, height: 960 });
    await noOverflow(page);
    await expect(page.locator(".portal-language-control select:visible")).toBeVisible();
  }
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("employee: Chinese pages, form state and English broker isolation", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const writes = await mockPortals(page);
  await page.goto("/portal/language/coverage?tab=dependants");
  await chooseLanguage(page, "zh-SG");
  await page.getByRole("button", { name: "添加家属", exact: true }).click();
  const name = page.getByRole("textbox", { name: "姓名（必填）", exact: true });
  await name.fill("Original Family Name");
  await chooseLanguage(page, "en-SG");
  await expect(page.getByRole("textbox", { name: /^Full name/ })).toHaveValue("Original Family Name");
  await chooseLanguage(page, "zh-SG");
  await expect(name).toHaveValue("Original Family Name");
  // Leave the unsent form through a fresh navigation; no working data is written.
  for (const [route, text] of [
    ["claims", "Original Clinic Name"], ["claims/language-claim", "申请理赔金额"],
    ["claims/new", "暂无可申请理赔的保障"], ["card", "暂无保障记录"],
    ["clinics", "暂无指定诊所"], ["messages", "暂无消息"],
    ["security", "更改密码"], ["enrollment", "当前暂无可选择项目"],
  ]) {
    await page.goto(`/portal/language/${route}`);
    await expect(page.getByText(text).first()).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`employee-${route.replaceAll("/", "-")}-zh.png`), fullPage: true, animations: "disabled" });
  }
  await page.goto("/sign-in");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator(".portal-language-control")).toHaveCount(0);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("HR: Chinese navigation, claims, register and responsive language control", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const writes = await mockPortals(page);
  await page.goto("/hr/dashboard?company=language");
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByRole("heading", { name: "欢迎， Jamie Lim" })).toBeVisible();
  await expect(page.getByRole("region", { name: "人力资源服务" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("hr-home-zh.png"), fullPage: true, animations: "disabled" });
  for (const [route, text] of [["claims", "Original Clinic Name"], ["claims/language-claim", "申请理赔金额"], ["claims/new", "选择员工"], ["enrollment-forms", "搜索表格"], ["security", "账户安全"]]) {
    await page.goto(`/hr/${route}`);
    await expect(page.getByText(text).first()).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`hr-${route.replaceAll("/", "-")}-zh.png`), fullPage: true, animations: "disabled" });
  }
  await page.goto("/hr/enrollment-forms");
  await page.getByRole("searchbox").fill("Original Search");
  await chooseLanguage(page, "en-SG");
  await expect(page.getByRole("searchbox")).toHaveValue("Original Search");
  await expect(page.getByRole("button", { name: "Excel summary" })).toBeVisible();
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByRole("button", { name: "Excel 汇总" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Excel 汇总" })).toBeDisabled();
  for (const width of testInfo.project.name.startsWith("desktop") ? [1440, 1024, 940] : [393, 320]) {
    await page.setViewportSize({ width, height: 960 });
    await noOverflow(page);
    await expect(page.getByRole("combobox", { name: "语言" })).toBeVisible();
  }
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("language preference works when browser storage is blocked", async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.setItem = () => { throw new DOMException("Blocked", "SecurityError"); };
    Storage.prototype.getItem = () => { throw new DOMException("Blocked", "SecurityError"); };
  });
  await mockPortals(page, false);
  await page.goto("/portal/language/sign-in");
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
  await chooseLanguage(page, "en-SG");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
});

test("enrolment: switching languages preserves contact details and leave choices", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const writes = await mockPortals(page);
  const window = { id: "language-period", name: "Original Annual Enrolment", status: "open", window_type: "open",
    opens_at: "2020-01-01T00:00:00Z", closes_at: "2035-12-31T00:00:00Z",
    allow_leave: true, allow_dependant_changes: false, member_self_service: true,
    allow_overdraft: false, product_scope: null, default_behavior: "deemed_keep_current" };
  await page.route("**/api/v1/portal/enrollment", route => route.fulfill({ json: { window,
    enrollment: { id: "language-draft", window_id: window.id, status: "not_started", latest_event_id: null,
      elections: [], leave: null, baseline_snapshot: null, compulsory_product_codes: [] },
    options: { products: [], flex_wallet: null, member_leave_rate: 100,
      leave: { allow_buy: true, allow_sell: true, min_buy_days: 0, max_buy_days: 5,
        min_sell_days: 0, max_sell_days: 5, increment_days: 1, sell_eligible: true } },
  } }));
  await page.route("**/api/v1/portal/enrollment/state/language-draft", route => route.fulfill({ json: {
    window_status: "open", opens_at: window.opens_at, closes_at: window.closes_at,
    member_self_service: true, latest_event_id: null,
  } }));
  await page.route("**/api/v1/portal/enrollment/form", route => route.fulfill({ json: {
    company_name: "Language Review Company", title: "Enrolment", closes_at: window.closes_at,
    policy_start: policyYear.start_date, policy_end: policyYear.end_date, window_type: "open",
    intro_lines: [], eligibility_notes: ["Spouse: your legal spouse, up to age 65 next birthday, not divorced or legally separated from you."],
    clauses: [{ id: "accurate", applies_to: "all", text: "The information in this form is true and complete. I will tell my HR team or broker if any of it changes." }], documents: [], rules: [], compulsory: [],
    contributions: [], plans: [], dependants: [], latest: null,
    particulars: { name: member.display_name, staff_id: member.staff_id, id_masked: "****",
      email: member.email, contact_no: "" },
  } }));
  await page.goto("/portal/language/enrollment");
  const email = page.getByRole("textbox", { name: "Email", exact: true });
  await email.fill("original-contact@example.test");
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByRole("textbox", { name: "电子邮箱", exact: true })).toHaveValue("original-contact@example.test");
  await noOverflow(page);
  await page.getByRole("tab", { name: "年假", exact: true }).click();
  await page.getByLabel("您希望如何处理？").selectOption("sell");
  await page.getByLabel("天数").fill("2");
  await chooseLanguage(page, "en-SG");
  await expect(page.getByLabel("What would you like to do")).toHaveValue("sell");
  await expect(page.getByLabel("How many days")).toHaveValue("2");
  await chooseLanguage(page, "zh-SG");
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("employee-enrolment-leave-zh.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "您的资料", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "电子邮箱", exact: true })).toHaveValue("original-contact@example.test");
  await page.getByRole("tab", { name: "您的家属", exact: true }).click();
  await expect(page.getByText(/配偶：您的合法配偶/)).toContainText("65");
  await page.getByRole("tab", { name: /^阅读并同意/ }).click();
  const declaration = page.getByRole("checkbox", { name: /此表格中的资料真实且完整/ });
  await declaration.check();
  await page.screenshot({ path: info.outputPath("employee-enrolment-declarations-zh.png"), fullPage: true, animations: "disabled" });
  await expect(page.getByText("The information in this form is true and complete. I will tell my HR team or broker if any of it changes.", { exact: true })).toBeVisible();
  await chooseLanguage(page, "en-SG");
  await expect(page.getByRole("checkbox", { name: /The information in this form is true and complete/ })).toBeChecked();
  await chooseLanguage(page, "zh-SG");
  const audit = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(audit.violations).toEqual([]);
  expect(errors).toEqual([]);
  expect(writes).toEqual([]);
});

test("keyboard language selection synchronizes employee and HR tabs", async ({ page, context }) => {
  await mockPortals(page);
  await page.goto("/portal/language");
  const hrPage = await context.newPage();
  await mockPortals(hrPage);
  await hrPage.goto("/hr/dashboard?company=language");
  await expect(hrPage.locator("html")).toHaveAttribute("lang", "en");
  const control = page.locator(".portal-language-control select:visible");
  await control.focus();
  await expect(control).toBeFocused();
  await control.press("End");
  await control.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-Hans-SG");
  await expect(hrPage.locator("html")).toHaveAttribute("lang", "zh-Hans-SG");
  await expect(hrPage.getByRole("heading", { name: "欢迎， Jamie Lim" })).toBeVisible();
  await chooseLanguage(hrPage, "en-SG");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByText("Your benefits", { exact: true })).toBeVisible();
});

test("populated clinics translate derived labels and retain clinic details", async ({ page }, info) => {
  await mockPortals(page);
  await page.clock.setFixedTime(new Date("2026-10-09T04:00:00Z"));
  await page.route("**/api/v1/portal/clinics?**", route => route.fulfill({ json: {
    items: [{ id: "language-clinic", name: "Original Family Clinic", code: "ORIG", area: "Ang Mo Kio",
      address: "123 Original Street", phone: "61234567", specialty: null, doctor: null,
      clinic_type: "gp", country: "SG", type_label: "GP", panel_label: "Original Insurer",
      distance_km: 0.42, google_map_url: "https://maps.google.com/?q=original",
      hours: { mon_fri: "9am - 5pm", sat: "9am - 1pm", sun: "Closed", public_holiday: "Closed" } }],
    total: 1, offset: 0, limit: 50, located: true,
    filters: { clinic_types: [{ clinic_type: "gp", country: "SG", label: "GP", count: 1 }], areas: ["Ang Mo Kio"] },
  } }));
  await page.goto("/portal/language/clinics");
  await expect(page.getByRole("heading", { name: "Original Family Clinic" })).toBeVisible();
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByText("420 米", { exact: true })).toBeVisible();
  await expect(page.getByText("正在营业", { exact: true })).toBeVisible();
  await expect(page.getByText(/营业至/)).toBeVisible();
  await page.getByRole("button", { name: /Original Family Clinic/ }).click();
  await expect(page.getByText("周一至周五", { exact: true })).toBeVisible();
  await expect(page.getByText("123 Original Street", { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("employee-populated-clinic-zh.png"), fullPage: true, animations: "disabled" });
  await chooseLanguage(page, "en-SG");
  await expect(page.getByText("420 m", { exact: true })).toBeVisible();
  await expect(page.getByText("Until 5:00 pm", { exact: true })).toBeVisible();
});

test("claim validation switches dates and document prompts without changing entered values", async ({ page }) => {
  const writes = await mockPortals(page);
  await page.route("**/api/v1/portal/coverage-options", route => route.fulfill({ json: {
    policy_year_start: policyYear.start_date, policy_year_end: policyYear.end_date,
    claimable_from: policyYear.start_date, claimable_to: policyYear.end_date, currencies: ["SGD"], hospitals: [], flex: null,
    insured: [{ product_code: "GCGP", product_name: "Original GP Policy", plan_code: "A", annual_policy_limit: "50000",
      covers_dependants: false, covered_dependant_ids: [], sub_types: [], requires_referral: false,
      diagnosis_required: false, diagnosis_group: null, category: "outpatient",
      claim_types: [{ label: "GP", sub_type: null, scope_code: "GP", scope_key: "GP", benefit_key: null,
        requires_doctor_name: false, supports_stay_dates: false, anchor_mode: null,
        doc_slots: [{ key: "tax_invoice", label: "Tax invoice" }], doc_slots_by_sector: null }] }],
  } }));
  await page.goto("/portal/language/claims/new");
  await page.getByRole("combobox", { name: "Claim type", exact: false }).selectOption("insured:GCGP:0");
  const visitDate = page.getByLabel("Visit date", { exact: false });
  await visitDate.fill("2026-09-18");
  await page.getByRole("textbox", { name: "Provider / clinic", exact: false }).fill("Original Clinic");
  await page.getByLabel("Invoice number", { exact: false }).fill("ORIGINAL-INV");
  await page.getByRole("spinbutton", { name: "Incurred amount", exact: false }).fill("88.50");
  await page.getByRole("button", { name: "Submit claim", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Attach the tax invoice." })).toBeVisible();
  await chooseLanguage(page, "zh-SG");
  await expect(page.getByRole("alert").filter({ hasText: "请上传" })).toContainText("税务发票");
  await expect(page.getByRole("listitem", { name: "文件: 尚未完成", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "理赔类型", exact: false })).toHaveValue("insured:GCGP:0");
  const entered = await page.locator("input").evaluateAll(inputs => inputs.map(input => input.value));
  expect(entered).toContain("Original Clinic");
  expect(entered).toContain("ORIGINAL-INV");
  const localizedDate = page.locator('input[type="date"]').first();
  await localizedDate.fill("2025-12-31");
  await localizedDate.evaluate(input => input.reportValidity());
  expect(await localizedDate.evaluate(input => input.validationMessage)).toContain("2026年1月1日");
  await chooseLanguage(page, "en-SG");
  await localizedDate.evaluate(input => input.reportValidity());
  expect(await localizedDate.evaluate(input => input.validationMessage)).toContain("Choose a date on or after");
  await localizedDate.fill("2026-09-18");
  expect(await localizedDate.evaluate(input => input.validity.valid)).toBe(true);
  await expect(page.getByRole("alert").filter({ hasText: "Attach the tax invoice." })).toBeVisible();
  expect(writes.filter(path => path.endsWith("/claims"))).toEqual([]);
  await noOverflow(page);
});

test("system claim translations retain snapshot amounts, notes and authored messages", async ({ page }) => {
  await mockPortals(page, false);
  await page.goto("/portal/language/sign-in");
  const fixtures = [
    ["submitted", "Your GP claim for US$88.50 (S$120) on 18 Sep 2026 is with us. You don't need to send it again — if we need anything else, it will appear here.", "US$88.50 (S$120)"],
    ["approved", "Your GP claim for 18 Sep 2026 has been approved for S$0.", "S$0"],
    ["rejected", "We weren't able to approve your GP claim for 18 Sep 2026.", "未获批准"],
    ["paid", "Your GP claim for 18 Sep 2026 has been paid — S$0 on 22 Sep 2026.", "S$0"],
    ["amended", "Your GP claim for 18 Sep 2026 has been updated.", "已更新"],
    ["needs_info", "Before we can finish your GP claim for 18 Sep 2026, we need a little more from you.", "补充资料"],
  ];
  const results = await page.evaluate(async cases => {
    const portalModule = "/src/i18n/portal.ts";
    const messagesModule = "/src/i18n/systemMessages.ts";
    const { translatePortalText } = await import(portalModule);
    const { systemClaimBody } = await import(messagesModule);
    const zh = ((source: string, values?: readonly unknown[]) => translatePortalText(source, "zh-SG", values)) as PortalTranslator;
    return cases.map(([event, body]) => {
      const full = `${body}\n\nOriginal broker note {0}.`;
      return {
        translated: systemClaimBody({ author_type: "system", event, body: full }, zh),
        member: systemClaimBody({ author_type: "member", event, body: full }, zh),
        broker: systemClaimBody({ author_type: "broker", event, body: full }, zh),
        literal: translatePortalText("Remove {0}", "zh-SG", ["original-{0}.pdf"]),
        original: translatePortalText("Original insurer contract wording", "zh-SG"),
        age: translatePortalText("Over the age limit for spouse (65 next birthday).", "zh-SG"),
        http: translatePortalText("Request failed (HTTP 503)", "zh-SG"),
        upload: translatePortalText("That file is too large — the limit is 15 MB. Choose a smaller file and try again.", "zh-SG"),
      };
    });
  }, fixtures);
  for (const [index, [event, body, expected]] of fixtures.entries()) {
    const full = `${body}\n\nOriginal broker note {0}.`;
    const { translated, member, broker, literal, original } = results[index];
    expect(translated).toContain(expected);
    expect(translated).toContain("2026年9月18日");
    expect(translated).toContain("Original broker note {0}.");
    expect(translated).not.toContain("Your GP claim");
    expect(member, event).toBe(full);
    expect(broker, event).toBe(full);
    expect(literal).toContain("original-{0}.pdf");
    expect(original).toBe("Original insurer contract wording");
    expect(results[index].age).toContain("配偶");
    expect(results[index].age).toContain("65");
    expect(results[index].http).toContain("请求失败（HTTP 503）");
    expect(results[index].upload).toContain("15 MB");
  }
});
