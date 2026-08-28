import { useEffect, useRef, useState } from "react";
import { Users } from "lucide-react";
import {
  type DependantConfig,
  type DependantPricingMode,
  type FamilyRole,
  type FamilyScheme,
  type FlexPricingBag,
  type FlexPricingProduct,
  type FlexPricingProductBlock,
  type FlexPricingTier,
  type VoluntaryRateBand,
  useFlexPricing,
} from "@/api/enrollment";
import { ALL_AGES_LABEL } from "@/lib/flexTiers";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";

/** The single implicit band a plan-type product is stored under, so the matrix
 *  shape + resolver (age → band) stay unchanged (min/max null matches any age). */
const ALL_AGES = { label: ALL_AGES_LABEL, min: null, max: null };

const EMPTY_BLOCK: FlexPricingProductBlock = { age_bands: [], price_tags: {} };

/** Placeholder for a price input: the slip baseline when present, else "0". */
function slipPlaceholder(value: number | null | undefined): string {
  return value != null ? String(value) : "0";
}

const DEP_MODE_OPTIONS: { value: DependantPricingMode; label: string }[] = [
  { value: "none", label: "None" },
  { value: "family_group", label: "Family group" },
  { value: "per_pax", label: "Per dependant" },
];

const SCHEME_OPTIONS: { value: FamilyScheme; label: string }[] = [
  { value: "ec_es_ef", label: "EO·ES·EC·EF" },
  { value: "so_co_sc", label: "EO·SO·CO·SC" },
];

const FAMILY_ROLES: FamilyRole[] = ["spouse", "child", "both"];

/** Per-scheme labels for the three above-Employee-Only family roles. */
const SCHEME_LABELS: Record<FamilyScheme, Record<FamilyRole, string>> = {
  ec_es_ef: { spouse: "ES · + spouse", child: "EC · + children", both: "EF · family" },
  so_co_sc: { spouse: "SO · spouse only", child: "CO · child only", both: "SC · spouse & children" },
};

export type FlexPricingEditor = {
  dirty: boolean;
  pricing: FlexPricingBag;
  markSaved: () => void;
  blockFor: (pid: string) => FlexPricingProductBlock;
  /** Set one or more explicit cohort/tier overrides. Multi-key writes only occur
   *  after the broker chooses "apply to all categories". */
  setPlanPrice: (pid: string, tierKeys: string[], value: string) => void;
  clearTierPriceOverrides: (pid: string, tierKeys: string[]) => void;
  voluntaryRatesFor: (product: FlexPricingProduct) => VoluntaryRateBand[];
  voluntaryRatesEdited: (product: FlexPricingProduct) => boolean;
  setVoluntaryRates: (pid: string, rates: VoluntaryRateBand[] | null) => void;
  setDepMode: (pid: string, mode: DependantPricingMode) => void;
  setScheme: (pid: string, scheme: FamilyScheme) => void;
  /** Set a family increment on one or more tier keys at once (fan-out, as above). */
  setFamilyTag: (pid: string, tierKeys: string[], role: FamilyRole, value: string) => void;
  /** Set a per-dependant rate on one or more tier keys at once (fan-out, as above). */
  setPerPax: (pid: string, tierKeys: string[], value: string) => void;
  setDepAgeLimit: (
    pid: string,
    role: "spouse" | "child",
    bound: "min" | "max",
    value: string,
  ) => void;
};

/**
 * Editing state for the unified per-policy-year price book. Parsed values stay
 * outside the saved bag as recommendations; the bag contains sparse overrides.
 */
export function useFlexPricingEditor(
  policyYearId: string | undefined,
): FlexPricingEditor {
  const { data } = useFlexPricing(policyYearId);
  const [bag, setBag] = useState<FlexPricingBag>({});
  const [dirty, setDirty] = useState(false);

  // Re-seed local edits when the SERVER bag changes OR the policy year changes —
  // keying the signature on policyYearId too prevents unsaved edits from leaking
  // across a year switch when two years happen to share identical pricing JSON.
  const serverSig = useRef<string | null>(null);
  useEffect(() => {
    if (!data) return;
    const sig = `${policyYearId}:${JSON.stringify(data.pricing)}`;
    if (sig === serverSig.current) return;
    serverSig.current = sig;
    setBag(data.pricing ?? {});
    setDirty(false);
  }, [data, policyYearId]);

  const blockFor = (pid: string): FlexPricingProductBlock =>
    bag.products?.[pid] ?? EMPTY_BLOCK;

  const setBlock = (pid: string, block: FlexPricingProductBlock) => {
    setBag((b) => ({ ...b, products: { ...b.products, [pid]: block } }));
    setDirty(true);
  };

  // A fixed tier stores one value across every age band already used by the
  // product. Repeating it preserves mixed products: editing a compulsory flat
  // tier must not collapse or discard age-tier override rows. A blank value
  // removes the selected tier rows from every band.
  const setPlanPrice = (pid: string, tierKeys: string[], value: string) => {
    const block = blockFor(pid);
    const ageBands = block.age_bands.length ? block.age_bands : [ALL_AGES];
    const tags = Object.fromEntries(
      Object.entries(block.price_tags).map(([key, row]) => [key, { ...row }]),
    );
    const num = value.trim() === "" ? null : Number(value);
    for (const key of tierKeys) {
      if (num === null) delete tags[key];
      else {
        tags[key] = Object.fromEntries(
          ageBands.map((band) => [band.label, num]),
        );
      }
    }
    setBlock(pid, { ...block, age_bands: ageBands, price_tags: tags });
  };

  const clearTierPriceOverrides = (pid: string, tierKeys: string[]) => {
    const block = blockFor(pid);
    const tags = { ...block.price_tags };
    for (const key of tierKeys) delete tags[key];
    setBlock(pid, { ...block, price_tags: tags });
  };

  const voluntaryRatesEdited = (product: FlexPricingProduct): boolean =>
    Object.prototype.hasOwnProperty.call(
      bag.products?.[product.product_id] ?? {},
      "voluntary_rates",
    );

  const voluntaryRatesFor = (product: FlexPricingProduct): VoluntaryRateBand[] =>
    bag.products?.[product.product_id]?.voluntary_rates ??
    product.voluntary_rates ??
    [];

  const setVoluntaryRates = (pid: string, rates: VoluntaryRateBand[] | null) => {
    const block = blockFor(pid);
    if (rates === null) {
      const { voluntary_rates: _removed, ...rest } = block;
      setBlock(pid, rest);
      return;
    }
    setBlock(pid, { ...block, voluntary_rates: rates });
  };

  // ── Dependant pricing (additive over Employee-Only) ──────────────────────
  const depFor = (pid: string): DependantConfig => blockFor(pid).dependant ?? {};
  const setDep = (pid: string, dependant: DependantConfig) =>
    setBlock(pid, { ...blockFor(pid), dependant });

  const setDepMode = (pid: string, mode: DependantPricingMode) =>
    setDep(pid, { ...depFor(pid), mode });

  const setScheme = (pid: string, scheme: FamilyScheme) =>
    setDep(pid, { ...depFor(pid), scheme });

  // Family/per-pax amounts are keyed per tier (the dependant tag differs per plan),
  // and fan out across every passed key so a plan folded across cohorts sets them
  // all at once.
  const setFamilyTag = (pid: string, tierKeys: string[], role: FamilyRole, value: string) => {
    const dep = depFor(pid);
    const tags = { ...(dep.family_tags ?? {}) };
    const num = value.trim() === "" ? null : Number(value);
    for (const key of tierKeys) {
      const row = { ...(tags[key] ?? {}) };
      if (num === null) delete row[role];
      else row[role] = num;
      if (Object.keys(row).length) tags[key] = row;
      else delete tags[key];
    }
    setDep(pid, { ...dep, family_tags: tags });
  };

  // Per-role dependant eligibility window (life products). Blank clears the bound
  // → the product falls back to the effective default surfaced by the API.
  const setDepAgeLimit = (
    pid: string,
    role: "spouse" | "child",
    bound: "min" | "max",
    value: string,
  ) => {
    const dep = depFor(pid);
    const limits = { ...(dep.age_limits ?? {}) };
    const win = { ...(limits[role] ?? {}) };
    if (value.trim() === "") delete win[bound];
    else win[bound] = Number(value);
    limits[role] = win;
    setDep(pid, { ...dep, age_limits: limits });
  };

  const setPerPax = (pid: string, tierKeys: string[], value: string) => {
    const dep = depFor(pid);
    const pp = { ...(dep.per_pax ?? {}) };
    const num = value.trim() === "" ? null : Number(value);
    for (const key of tierKeys) {
      if (num === null) delete pp[key];
      else pp[key] = { flat: num };
    }
    setDep(pid, { ...dep, per_pax: pp });
  };

  return {
    dirty,
    pricing: bag,
    markSaved: () => setDirty(false),
    blockFor,
    setPlanPrice,
    clearTierPriceOverrides,
    voluntaryRatesFor,
    voluntaryRatesEdited,
    setVoluntaryRates,
    setDepMode,
    setScheme,
    setFamilyTag,
    setPerPax,
    setDepAgeLimit,
  };
}

/** Unified editor's dependant section. Parsed dependant rates are always the
 * recommendation and sparse values in the pricing bag are category-specific
 * broker corrections. */
export function ProductDependantEditor({
  product,
  editor,
}: {
  product: FlexPricingProduct;
  editor: FlexPricingEditor;
}) {
  const pid = product.product_id;
  const dep = editor.blockFor(pid).dependant ?? {};
  return (
    <DependantPricing
      product={product}
      dep={dep}
      onMode={(mode) => editor.setDepMode(pid, mode)}
      onScheme={(scheme) => editor.setScheme(pid, scheme)}
      onFamilyTag={(tierKeys, role, value) =>
        editor.setFamilyTag(pid, tierKeys, role, value)
      }
      onPerPax={(tierKeys, value) => editor.setPerPax(pid, tierKeys, value)}
    />
  );
}

/** Plan label for the dependant tables: plan name + cohort (only on a split row).
 *  No direction badge — the dependant tables never carried one. */
function DepPlanLabel({
  rep,
  cohortLabel,
}: {
  rep: FlexPricingTier;
  cohortLabel: string | null;
}) {
  return (
    <span className="text-foreground">
      {rep.label}
      {cohortLabel && (
        <span className="ml-1 text-2xs text-muted-foreground">· {cohortLabel}</span>
      )}
    </span>
  );
}

interface DependantProps {
  product: FlexPricingProduct;
  dep: DependantConfig;
  onMode: (m: DependantPricingMode) => void;
  onScheme: (s: FamilyScheme) => void;
  onFamilyTag: (tierKeys: string[], role: FamilyRole, value: string) => void;
  onPerPax: (tierKeys: string[], value: string) => void;
}

/** Dependant pricing config for one product, priced PER plan/tier (the same rows
 *  as the price-tag table): a family-composition table (EO/ES/EC/EF or
 *  EO/SO/CO/SC) or a flat per-dependant rate. Each amount is the incremental flex
 *  drawn over Employee-Only, added on top of the member's own plan price tag. */
function DependantPricing({
  product,
  dep,
  onMode,
  onScheme,
  onFamilyTag,
  onPerPax,
}: DependantProps) {
  const mode = dep.mode ?? product.dependant_suggested_mode;
  const scheme = dep.scheme ?? "ec_es_ef";
  const labels = SCHEME_LABELS[scheme];
  const slip = product.slip_family ?? {};
  const slipPerPax = product.slip_per_pax ?? {};
  const slipHint =
    " Leave a cell blank to use the recommendation; type to override it.";

  // Never fold category rows merely because their current values match. A broker
  // must be able to create a category-specific correction from an equal starting
  // point; silent fan-out made that impossible in the previous editor.
  const categoryRows = () =>
    product.tiers.map((tier) => ({
      keys: [tier.key],
      rep: tier,
      cohortLabel: tier.cohort_label ?? null,
    }));

  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
          <Users className="size-3.5 text-muted-foreground" /> Dependant pricing
        </span>
        <Segmented value={mode} onChange={onMode} options={DEP_MODE_OPTIONS} />
      </div>

      {mode === "family_group" && (
        <div className="mt-2.5 space-y-2">
          <Segmented value={scheme} onChange={onScheme} options={SCHEME_OPTIONS} />
          <div
            className="overflow-x-auto"
            role="region"
            aria-label={`${product.product_code} family price tags`}
            tabIndex={0}
          >
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-2xs text-muted-foreground">
                  <th className="px-2 py-1.5 text-left font-medium">Plan</th>
                  {FAMILY_ROLES.map((role) => (
                    <th key={role} className="px-2 py-1.5 text-left font-medium">
                      {labels[role]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {categoryRows().map(({ keys, rep, cohortLabel }) => (
                  <tr key={rep.key} className="border-b border-border last:border-0">
                    <td className="px-2 py-1.5">
                      <DepPlanLabel rep={rep} cohortLabel={cohortLabel} />
                    </td>
                    {FAMILY_ROLES.map((role) => (
                      <td key={role} className="px-2 py-1.5">
                        <Input
                          type="number"
                          value={dep.family_tags?.[rep.key]?.[role] ?? ""}
                          onChange={(e) => onFamilyTag(keys, role, e.target.value)}
                          className="h-8 w-24"
                          placeholder={slipPlaceholder(slip[rep.key]?.[role])}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-2xs text-muted-foreground">
            Incremental flex over Employee-Only, per plan.{slipHint}
          </p>
        </div>
      )}

      {mode === "per_pax" && (
        <div className="mt-2.5 space-y-2">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-2xs text-muted-foreground">
                <th className="px-2 py-1.5 text-left font-medium">Plan</th>
                <th className="px-2 py-1.5 text-left font-medium">Per dependant</th>
              </tr>
            </thead>
            <tbody>
              {categoryRows().map(({ keys, rep, cohortLabel }) => (
                <tr key={rep.key} className="border-b border-border last:border-0">
                  <td className="px-2 py-1.5">
                    <DepPlanLabel rep={rep} cohortLabel={cohortLabel} />
                  </td>
                  <td className="px-2 py-1.5">
                    <Input
                      type="number"
                      value={dep.per_pax?.[rep.key]?.flat ?? ""}
                      onChange={(e) => onPerPax(keys, e.target.value)}
                      className="h-8 w-28"
                      placeholder={slipPlaceholder(slipPerPax[rep.key])}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-2xs text-muted-foreground">
            Rate per covered dependant, drawn per dependant on top of the employee
            plan tag.{slipHint}
          </p>
        </div>
      )}
    </div>
  );
}
