import { expect, test } from "@playwright/test";
import type { EnrollmentOptions, ProductTierSet } from "../src/api/enrollment";
import {
  buildElectionsPayload,
  computeFlex,
  dependantParticipationFor,
  type ProductState,
} from "../src/components/enrollment/electionCore";

const noCoverTierSet: ProductTierSet = {
  product_id: "medical",
  product_code: "MED",
  product_name: "Medical",
  employee_participation: "compulsory",
  // Deliberately different: explicit tier null must not fall back to this.
  dependant_participation: "compulsory",
  baseline_tier_category_id: "base",
  baseline_plan_code: "BASE",
  allow_plan_change: true,
  can_decline: false,
  tiers: [
    {
      key: "base::BASE",
      tier_category_id: "base",
      plan_code: "BASE",
      label: "Base",
      participation: "compulsory",
      dependant_participation: "compulsory",
      direction: "same",
      is_baseline: true,
      is_current: true,
      financials: null,
      price_tag: 80,
      differences: [],
      differences_total: 0,
    },
    {
      key: "no-family::PLUS",
      tier_category_id: "no-family",
      plan_code: "PLUS",
      label: "Plus",
      participation: "voluntary",
      dependant_participation: null,
      direction: "upgrade",
      is_baseline: false,
      is_current: false,
      financials: null,
      price_tag: 100,
      differences: [],
      differences_total: 0,
    },
  ],
  dependant: {
    mode: "per_pax",
    scheme: null,
    by_tier: {
      "no-family::PLUS": {
        mode: "per_pax",
        family: [],
        per_pax_rate: 50,
      },
    },
    option_choices: [
      {
        role: "spouse",
        choices: [
          {
            category_id: "level-1",
            label: "Level 1",
            sum_insured: 10_000,
            amount: 50,
            amounts_by_dependant: { spouse: 50 },
          },
        ],
      },
    ],
  },
};

const state: Record<string, ProductState> = {
  MED: {
    productCode: "MED",
    tierKey: "no-family::PLUS",
    declined: false,
    dependantIds: ["spouse"],
    depOptionIds: { spouse: "level-1" },
  },
};

test("explicit no-cover tiers clear payload dependants and skip wallet pricing", () => {
  expect(dependantParticipationFor(noCoverTierSet, "no-family::PLUS")).toBeNull();

  const payload = buildElectionsPayload(
    state,
    [noCoverTierSet],
    [{ id: "spouse", role: "spouse" }],
    true,
  );
  expect(payload[0]?.covered_dependant_ids).toBeNull();
  expect(payload[0]?.dependant_option_ids).toBeNull();

  const options = {
    flex_wallet: 500,
    flex_proration: null,
    flex_currency: "SGD",
    member_leave_rate: null,
    flex_drawdown_rule: "full",
  } as EnrollmentOptions;
  const flex = computeFlex(
    options,
    [noCoverTierSet],
    state,
    [{ id: "spouse", role: "spouse" }],
    true,
    "none",
    "0",
  );
  expect(flex).toMatchObject({ total: 100, balance: 400, incomplete: false });
});

test("legacy tiers with an omitted field still use the product fallback", () => {
  const legacy = structuredClone(noCoverTierSet) as ProductTierSet;
  delete (legacy.tiers[1] as Partial<ProductTierSet["tiers"][number]>)
    .dependant_participation;
  legacy.dependant_participation = "voluntary";
  expect(dependantParticipationFor(legacy, "no-family::PLUS")).toBe("voluntary");
});
