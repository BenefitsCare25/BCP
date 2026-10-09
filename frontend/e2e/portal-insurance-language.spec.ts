import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
const reviewed = JSON.parse(readFileSync("e2e/fixtures/portal-insurance-copy.json", "utf8")) as Record<string, string[]>;

test("all reviewed policy content, current templates and diagnosis entries translate with English originals intact", async ({ page }) => {
  const phrases = new Set(Object.entries(reviewed).filter(([kind]) => kind !== "property_key").flatMap(([, values]) => values));
  for (const name of readdirSync("../backend/app/templates").filter(name => name.endsWith(".json"))) {
    const template = JSON.parse(readFileSync(`../backend/app/templates/${name}`, "utf8"));
    for (const item of template.benefit_items ?? []) {
      for (const source of [item.name, item.note, ...(item.sub_items ?? []).map((s: { name: string }) => s.name)]) if (source) phrases.add(source);
    }
  }
  const diagnoses = readFileSync("../backend/app/services/sg_diagnoses.py", "utf8");
  for (const match of diagnoses.matchAll(/_d\("([^"]+)"/g)) phrases.add(match[1]);
  const products = readFileSync("../backend/app/services/product_registry.py", "utf8");
  for (const match of products.matchAll(/name="([^"]+)"/g)) phrases.add(match[1]);
  await page.goto("/portal/language/sign-in");
  const failures = await page.evaluate(async ({ sources, propertyKeys, bases }) => {
    const modulePath = "/src/i18n/portal.ts";
    const { translatePortalText } = await import(/* @vite-ignore */ modulePath);
    const schedulePath = "/src/lib/sob.ts";
    const { propertyLabel } = await import(/* @vite-ignore */ schedulePath);
    const basisPath = "/src/lib/basis.ts";
    const { basisWording, coverWording } = await import(/* @vite-ignore */ basisPath);
    sources.push(...propertyKeys.map((key: string) => propertyLabel(key)));
    for (const basis of bases) {
      for (const source of [basisWording(basis), coverWording(basis, 1600000)]) if (source) sources.push(source);
    }
    return sources.flatMap(source => {
      const chinese = translatePortalText(source, "zh-SG");
      const english = translatePortalText(source, "en-SG");
      if (english !== source) return [`English changed: ${source}`];
      if (/[a-z]{2}/i.test(source) && chinese === source) return [`Missing Chinese: ${source}`];
      return [];
    });
  }, { sources: [...phrases], propertyKeys: reviewed.property_key, bases: reviewed.basis });
  expect(failures).toEqual([]);
  expect(reviewed.diagnosis).toHaveLength(245);
  expect(reviewed.claim_label).toContain("Hospitalisation/Day Surgery/Other Inpatient Treatment");
});

test("composed insurance wording preserves figures, references and unfamiliar authored content", async ({ page }) => {
  await page.goto("/portal/language/sign-in");
  const translated = await page.evaluate(async () => {
    const modulePath = "/src/i18n/portal.ts";
    const { translatePortalText } = await import(/* @vite-ignore */ modulePath);
    return [
      "48 × basic monthly salary, up to S$1,600,000",
      "50% of GTL",
      "Refer to 1a/1b",
      "Plan D01",
      "Acme Custom Care (private arrangement)",
      "We could not get an exchange rate for USD on 2026-10-09. Your claim can still be sent — it will be converted by hand when it is reviewed.",
      "USD 1,000.00 is SGD 1,280.50, using the rate published on 2026-10-08 — no rate is published for 2026-10-09.",
      "USD 1,000.00 is SGD 1,280.50 at the 2026-10-09 rate.",
      "S$1,234.00 confirmed balance",
    ].map(source => translatePortalText(source, "zh-SG"));
  });
  expect(translated).toEqual([
    "基本月薪的 48 倍，最高 S$1,600,000",
    "GTL保额的 50%",
    "参见 1a/1b",
    "计划 D01",
    "Acme Custom Care (private arrangement)",
    "无法获取 2026-10-09 的 USD 汇率。您仍可提交理赔，审核时将由工作人员手动换算。",
    "USD 1,000.00 按 2026-10-08 公布的汇率换算为 SGD 1,280.50；2026-10-09 未公布汇率。",
    "USD 1,000.00 按 2026-10-09 的汇率换算为 SGD 1,280.50。",
    "已确认剩余额度 S$1,234.00",
  ]);
});
