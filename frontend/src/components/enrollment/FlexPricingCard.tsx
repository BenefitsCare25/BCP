import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Link2,
  Plus,
  RotateCcw,
  Trash2,
  Users,
} from "lucide-react";
import {
  type DependantConfig,
  type DependantParticipationMode,
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { InfoHint } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fmtMoney } from "@/lib/format";

/** The single implicit band a plan-type product is stored under, so the matrix
 *  shape + resolver (age → band) stay unchanged (min/max null matches any age). */
const ALL_AGES = { label: ALL_AGES_LABEL, min: null, max: null };

const EMPTY_BLOCK: FlexPricingProductBlock = { age_bands: [], price_tags: {} };

function slipPlaceholder(value: number | null | undefined): string {
  return value != null ? String(value) : "0";
}

const DEP_MODE_OPTIONS: { value: Exclude<DependantPricingMode, "slip_options">; label: string }[] = [
  { value: "none", label: "Included ($0)" },
  { value: "family_group", label: "Family rate" },
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
  voluntaryRatesFor: (
    product: FlexPricingProduct,
    tierKey?: string,
  ) => VoluntaryRateBand[];
  voluntaryRatesEdited: (
    product: FlexPricingProduct,
    tierKey?: string,
  ) => boolean;
  setVoluntaryRates: (
    pid: string,
    rates: VoluntaryRateBand[] | null,
    tierKey?: string,
  ) => void;
  setDepMode: (
    pid: string,
    tierKeys: string[],
    mode: Exclude<DependantPricingMode, "slip_options">,
  ) => void;
  clearDepMode: (pid: string, tierKeys: string[]) => void;
  setDepParticipation: (
    pid: string,
    tierKey: string,
    participation: Exclude<DependantParticipationMode, "none">,
  ) => void;
  removeDepCoverage: (pid: string, tierKey: string) => void;
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

const SCHEME_CODES: Record<FamilyScheme, Record<FamilyRole, string>> = {
  ec_es_ef: { spouse: "ES", child: "EC", both: "EF" },
  so_co_sc: { spouse: "SO", child: "CO", both: "SC" },
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

  const voluntaryRatesEdited = (
    product: FlexPricingProduct,
    tierKey?: string,
  ): boolean => {
    const block: FlexPricingProductBlock =
      bag.products?.[product.product_id] ?? EMPTY_BLOCK;
    if (tierKey) {
      return Object.prototype.hasOwnProperty.call(
        block.voluntary_rates_by_tier ?? {},
        tierKey,
      );
    }
    return Object.prototype.hasOwnProperty.call(block, "voluntary_rates");
  };

  const voluntaryRatesFor = (
    product: FlexPricingProduct,
    tierKey?: string,
  ): VoluntaryRateBand[] => {
    const block = bag.products?.[product.product_id];
    if (tierKey) {
      const tier = product.tiers.find((candidate) => candidate.key === tierKey);
      return (
        block?.voluntary_rates_by_tier?.[tierKey] ??
        block?.voluntary_rates ??
        tier?.voluntary_rates ??
        product.voluntary_rates ??
        []
      );
    }
    return block?.voluntary_rates ?? product.voluntary_rates ?? [];
  };

  const setVoluntaryRates = (
    pid: string,
    rates: VoluntaryRateBand[] | null,
    tierKey?: string,
  ) => {
    const block = blockFor(pid);
    if (tierKey) {
      const byTier = { ...(block.voluntary_rates_by_tier ?? {}) };
      if (rates === null) delete byTier[tierKey];
      else byTier[tierKey] = rates;
      const next = { ...block };
      if (Object.keys(byTier).length) next.voluntary_rates_by_tier = byTier;
      else delete next.voluntary_rates_by_tier;
      setBlock(pid, next);
      return;
    }
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

  const setDepMode = (
    pid: string,
    tierKeys: string[],
    mode: Exclude<DependantPricingMode, "slip_options">,
  ) => {
    const dep = depFor(pid);
    const modes = { ...(dep.modes ?? {}) };
    for (const key of tierKeys) modes[key] = mode;
    setDep(pid, { ...dep, modes });
  };

  const clearDepMode = (pid: string, tierKeys: string[]) => {
    const dep = depFor(pid);
    const modes = { ...(dep.modes ?? {}) };
    for (const key of tierKeys) delete modes[key];
    const next = { ...dep };
    if (tierKeys.length === 0) delete next.mode;
    if (Object.keys(modes).length) next.modes = modes;
    else delete next.modes;
    setDep(pid, next);
  };

  const setDepParticipation = (
    pid: string,
    tierKey: string,
    participation: Exclude<DependantParticipationMode, "none">,
  ) => {
    const dep = depFor(pid);
    setDep(pid, {
      ...dep,
      participation: { ...(dep.participation ?? {}), [tierKey]: participation },
    });
  };

  const removeDepCoverage = (pid: string, tierKey: string) => {
    const dep = depFor(pid);
    const modes = { ...(dep.modes ?? {}) };
    const familyTags = { ...(dep.family_tags ?? {}) };
    const perPax = { ...(dep.per_pax ?? {}) };
    delete modes[tierKey];
    delete familyTags[tierKey];
    delete perPax[tierKey];
    const next: DependantConfig = {
      ...dep,
      participation: { ...(dep.participation ?? {}), [tierKey]: "none" },
    };
    if (Object.keys(modes).length) next.modes = modes;
    else delete next.modes;
    if (Object.keys(familyTags).length) next.family_tags = familyTags;
    else delete next.family_tags;
    if (Object.keys(perPax).length) next.per_pax = perPax;
    else delete next.per_pax;
    setDep(pid, next);
  };

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
    clearDepMode,
    setDepParticipation,
    removeDepCoverage,
    setScheme,
    setFamilyTag,
    setPerPax,
    setDepAgeLimit,
  };
}

/** Unified editor's dependant section. Parsed dependant rates are always the
 * recommendation and sparse values in the pricing bag are category-specific
 * broker corrections. */
/** @deprecated Kept as a compatibility export for downstream imports during the
 * unified editor rollout. New enrollment screens use ProductDependantEditor. */
export function LegacyProductDependantEditor({
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
      onMode={(mode) =>
        editor.setDepMode(pid, product.tiers.map((tier) => tier.key), mode)
      }
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
  onMode: (m: Exclude<DependantPricingMode, "slip_options">) => void;
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
  const detected = product.dependant_suggested_mode;
  const mode: Exclude<DependantPricingMode, "slip_options"> =
    dep.mode && dep.mode !== "slip_options"
      ? dep.mode
      : detected === "slip_options"
        ? "none"
        : detected;
  const scheme = dep.scheme ?? "ec_es_ef";
  const labels = SCHEME_LABELS[scheme];
  const slip = product.slip_family ?? {};
  const slipPerPax = product.slip_per_pax ?? {};
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
        </div>
      )}
    </div>
  );
}

/** Resolver-backed dependant editor. Coverage participation and wallet funding
 * are deliberately separate: compulsory means automatic coverage, voluntary
 * means employee-elected coverage, and every configured charge is paid from the
 * employee's flex wallet. */
export function ProductDependantEditor({
  product,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const pid = product.product_id;
  const dep = editor.blockFor(pid).dependant ?? {};
  const scheme = dep.scheme ?? "ec_es_ef";
  const labels = SCHEME_LABELS[scheme];
  const slip = product.slip_family ?? {};
  const slipPerPax = product.slip_per_pax ?? {};
  const [expanded, setExpanded] = useState(false);
  const cohorts = dependantCohortsFor(product);
  const hasFamilyRows = product.tiers.some(
    (tier) => effectiveDependantMode(dep, tier) === "family_group",
  );

  return (
    <section className="border-t border-border" aria-labelledby={`${pid}-dependants`}>
      <div className="flex flex-wrap items-center justify-between gap-3 bg-muted/20 px-4 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h4
            id={`${pid}-dependants`}
            className="flex items-center gap-1.5 text-sm font-medium text-foreground"
          >
            <Users className="size-4 text-muted-foreground" aria-hidden="true" />
            Dependants
          </h4>
          <DependantProductStatus product={product} />
        </div>
        {!product.has_dependants ? (
          <Button asChild variant="outline" size="sm">
            <Link
              to="/client-relations/company-benefits"
              search={{
                tab: product.line,
              }}
              aria-label={`Add dependant cover for ${product.product_code}`}
            >
              <Plus className="size-3.5" aria-hidden="true" />
              Add dependant cover
            </Link>
          </Button>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {expanded && hasFamilyRows && (
              <>
                <span className="text-2xs font-medium text-muted-foreground">
                  Family labels
                </span>
                <Segmented
                  value={scheme}
                  onChange={(value) => editor.setScheme(pid, value)}
                  options={SCHEME_OPTIONS}
                  disabled={!editable}
                />
              </>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              aria-controls={`${pid}-dependant-pricing`}
              aria-label={`${expanded ? "Hide" : editable ? "Configure" : "View"} dependants for ${product.product_code}`}
            >
              {expanded ? (
                <ChevronDown className="size-3.5" aria-hidden="true" />
              ) : (
                <ChevronRight className="size-3.5" aria-hidden="true" />
              )}
              {expanded ? "Hide" : editable ? "Configure" : "View"}
            </Button>
          </div>
        )}
      </div>

      {product.has_dependants && expanded && dep.mode && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-info/25 bg-info-soft/30 px-4 py-2 text-xs">
          <span className="font-medium text-foreground">Legacy pricing method</span>
          {editable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => editor.clearDepMode(pid, [])}
            >
              <RotateCcw className="size-3.5" aria-hidden="true" />
              Use detected tier methods
            </Button>
          )}
        </div>
      )}

      {product.has_dependants && expanded && (
        <div id={`${pid}-dependant-pricing`}>
          <div
            className="overflow-x-auto"
            role="region"
            aria-label={`${product.product_code} dependant pricing by employee tier`}
            tabIndex={0}
          >
            <table className="w-full min-w-[860px] table-fixed text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="w-[24%] px-4 py-2 text-left font-medium">Employee category</th>
                  <th className="w-[14%] px-3 py-2 text-left font-medium">Plan or option</th>
                  <th className="w-[14%] px-3 py-2 text-left font-medium">Participation</th>
                  <th className="w-[20%] px-3 py-2 text-left font-medium">Pricing method</th>
                  <th className="w-[28%] px-3 py-2 text-left font-medium">Dependant charge</th>
                </tr>
              </thead>
              {cohorts.map((cohort) => (
                <tbody key={cohort.id} className="border-b border-border last:border-b-0">
                {cohort.tiers.map((tier, index) => {
                  const mode = effectiveDependantMode(dep, tier);
                  const modeEdited = Object.prototype.hasOwnProperty.call(
                    dep.modes ?? {},
                    tier.key,
                  );
                  return (
                    <tr
                      key={tier.key}
                      className="align-top hover:bg-muted/20"
                    >
                      {index === 0 && (
                        <th
                          scope="rowgroup"
                          rowSpan={cohort.tiers.length}
                          className="px-4 py-2 text-left font-medium leading-snug text-foreground"
                        >
                          {cohort.label}
                        </th>
                      )}
                      <td className="px-3 py-2 font-medium text-foreground">{tier.label}</td>
                      <td className="px-3 py-2">
                        <DependantParticipation value={tier.dependant_participation} />
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-1">
                          <Select
                            value={mode}
                            disabled={!editable}
                            onValueChange={(value) => {
                              if (value === "slip_options") return;
                              editor.setDepMode(
                                pid,
                                [tier.key],
                                value as Exclude<DependantPricingMode, "slip_options">,
                              );
                            }}
                          >
                            <SelectTrigger
                              className="h-8 w-44 text-xs"
                              aria-label={`${tier.label} dependant pricing method`}
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">No additional charge</SelectItem>
                              <SelectItem value="family_group">Family tier</SelectItem>
                              <SelectItem value="per_pax">Per dependant</SelectItem>
                              <SelectItem value="slip_options" disabled>
                                Linked options (detected)
                              </SelectItem>
                            </SelectContent>
                          </Select>
                          {editable && modeEdited && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => editor.clearDepMode(pid, [tier.key])}
                              aria-label={`Reset ${tier.label} dependant pricing method`}
                              title="Use detected pricing method"
                            >
                              <RotateCcw className="size-3.5" aria-hidden="true" />
                            </Button>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2.5">
                        {mode === "family_group" ? (
                          <div className="grid min-w-60 gap-1.5">
                            {FAMILY_ROLES.map((role) => {
                              const recommendation = slip[tier.key]?.[role];
                              const override = dep.family_tags?.[tier.key]?.[role];
                              return (
                                <label
                                  key={role}
                                  className="grid grid-cols-[minmax(0,1fr)_7rem] items-center gap-2"
                                >
                                  <span className="text-2xs leading-tight text-muted-foreground">
                                    {labels[role]}
                                  </span>
                                  <Input
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    disabled={!editable}
                                    value={override ?? recommendation ?? ""}
                                    onChange={(event) =>
                                      editor.setFamilyTag(
                                        pid,
                                        [tier.key],
                                        role,
                                        event.target.value,
                                      )
                                    }
                                    className="h-8 w-28 tabular-nums"
                                    placeholder="Required"
                                    aria-label={`${tier.label} ${labels[role]} dependant charge`}
                                  />
                                </label>
                              );
                            })}
                          </div>
                        ) : mode === "per_pax" ? (
                          <label className="block w-36">
                            <span className="mb-1 block text-2xs text-muted-foreground">
                              Per covered dependant
                            </span>
                            <Input
                              type="number"
                              min="0"
                              step="0.01"
                              disabled={!editable}
                              value={
                                dep.per_pax?.[tier.key]?.flat ??
                                slipPerPax[tier.key] ??
                                ""
                              }
                              onChange={(event) =>
                                editor.setPerPax(pid, [tier.key], event.target.value)
                              }
                              className="h-8 tabular-nums"
                              placeholder="Required"
                              aria-label={`${tier.label} charge per covered dependant`}
                            />
                          </label>
                        ) : mode === "slip_options" ? (
                          <LinkedOptionSummary tier={tier} />
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                </tbody>
              ))}
            </table>
          </div>

          <DependantEligibility
            product={product}
            dep={dep}
            editable={editable}
            onChange={(role, bound, value) =>
              editor.setDepAgeLimit(pid, role, bound, value)
            }
          />
        </div>
      )}
    </section>
  );
}

function dependantCohortsFor(product: FlexPricingProduct) {
  const groups = new Map<
    string,
    { id: string; label: string; tiers: FlexPricingTier[] }
  >();
  for (const tier of product.tiers) {
    const current = groups.get(tier.cohort_id);
    if (current) current.tiers.push(tier);
    else {
      groups.set(tier.cohort_id, {
        id: tier.cohort_id,
        label: tier.cohort_label || "All eligible employees",
        tiers: [tier],
      });
    }
  }
  return [...groups.values()];
}

function effectiveDependantMode(
  dep: DependantConfig,
  tier: FlexPricingTier,
): DependantPricingMode {
  return dep.modes?.[tier.key] ?? dep.mode ?? tier.dependant_pricing?.mode ?? "none";
}

function DependantProductStatus({ product }: { product: FlexPricingProduct }) {
  if (!product.has_dependants) return <Badge variant="outline">Not offered</Badge>;
  const states = new Set(
    product.tiers.map((tier) => tier.dependant_participation).filter(Boolean),
  );
  if (states.size > 1) return <Badge variant="info">Mixed participation</Badge>;
  const state = [...states][0];
  if (state === "compulsory") return <Badge variant="good">Compulsory</Badge>;
  if (state === "voluntary") return <Badge variant="info">Voluntary</Badge>;
  return <Badge variant="warn">Participation not classified</Badge>;
}

function DependantParticipation({
  value,
}: {
  value: FlexPricingTier["dependant_participation"];
}) {
  if (value === "compulsory") {
    return <Badge variant="good">Compulsory</Badge>;
  }
  if (value === "voluntary") {
    return <Badge variant="info">Voluntary</Badge>;
  }
  return (
    <span className="flex items-start gap-1 text-xs text-warn">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      Not classified
    </span>
  );
}

function LinkedOptionSummary({ tier }: { tier: FlexPricingTier }) {
  const breakdown = tier.dependant_pricing;
  const parts: string[] = [];
  for (const row of breakdown.family ?? []) {
    const role = row.role === "child" ? "Child" : "Spouse";
    parts.push(
      row.amount == null ? `${role}: age-banded` : `${role}: ${fmtMoney(row.amount)}`,
    );
  }
  for (const role of ["spouse", "child"] as const) {
    const choices = breakdown.choices?.[role] ?? [];
    if (choices.length) {
      parts.push(
        `${role === "child" ? "Child" : "Spouse"}: ${choices.length} selectable level${choices.length === 1 ? "" : "s"}`,
      );
    }
  }
  return (
    <div className="flex items-center gap-2 text-xs">
      <Link2 className="mt-0.5 size-4 shrink-0 text-info" aria-hidden="true" />
      <span className="text-foreground">
        {parts.length ? parts.join(" · ") : "Linked options"}
      </span>
    </div>
  );
}

function editedAgeBound(
  dep: DependantConfig,
  role: "spouse" | "child",
  bound: "min" | "max",
): string {
  const value = dep.age_limits?.[role]?.[bound];
  return value == null ? "" : String(value);
}

function effectiveAgeBound(
  product: FlexPricingProduct,
  role: "spouse" | "child",
  bound: "min" | "max",
): string {
  const value = product.dependant_age_limits?.[role]?.[bound];
  return value == null ? `No ${bound === "min" ? "minimum" : "maximum"}` : String(value);
}

function DependantEligibility({
  product,
  dep,
  editable,
  onChange,
}: {
  product: FlexPricingProduct;
  dep: DependantConfig;
  editable: boolean;
  onChange: (
    role: "spouse" | "child",
    bound: "min" | "max",
    value: string,
  ) => void;
}) {
  return (
    <div className="border-t border-border bg-muted/15 px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
        Eligible dependant ages
        <InfoHint>
          Dependants outside these ages are not covered and create no charge.
          A covered dependant&apos;s own age selects any applicable age-banded rate.
        </InfoHint>
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        {(["spouse", "child"] as const).map((role) => (
          <fieldset key={role} className="flex items-center gap-2">
            <legend className="sr-only">{role} age eligibility</legend>
            <span className="w-14 text-xs font-medium capitalize">{role}</span>
            <Input
              type="number"
              min="0"
              value={editedAgeBound(dep, role, "min")}
              onChange={(event) => onChange(role, "min", event.target.value)}
              disabled={!editable}
              className="h-8 w-28 tabular-nums"
              placeholder={effectiveAgeBound(product, role, "min")}
              aria-label={`${role} minimum eligible age`}
            />
            <span className="text-xs text-muted-foreground">to</span>
            <Input
              type="number"
              min="0"
              value={editedAgeBound(dep, role, "max")}
              onChange={(event) => onChange(role, "max", event.target.value)}
              disabled={!editable}
              className="h-8 w-28 tabular-nums"
              placeholder={effectiveAgeBound(product, role, "max")}
              aria-label={`${role} maximum eligible age`}
            />
          </fieldset>
        ))}
      </div>
    </div>
  );
}

function effectiveDependantParticipation(
  dep: DependantConfig,
  tier: FlexPricingTier,
): "compulsory" | "voluntary" | null {
  const configured = dep.participation?.[tier.key];
  if (configured === "none") return null;
  if (configured === "compulsory" || configured === "voluntary") {
    return configured;
  }
  return tier.dependant_participation;
}

export function UnifiedDependantEnrollment({
  product,
  tier,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  tier: FlexPricingTier;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const dep = editor.blockFor(product.product_id).dependant ?? {};
  const participation = effectiveDependantParticipation(dep, tier);
  const label = `${product.product_code} ${tier.cohort_label ?? "employees"} ${tier.label}`;

  if (!participation) {
    if (!editable) return <span className="text-xs text-muted-foreground">Not offered</span>;
    return (
      <Select
        value={undefined}
        onValueChange={(value) =>
          editor.setDepParticipation(
            product.product_id,
            tier.key,
            value as "compulsory" | "voluntary",
          )
        }
      >
        <SelectTrigger
          className="h-8 w-24 border-dashed text-xs"
          aria-label={`Add dependant cover to ${label}`}
        >
          <Plus className="size-3.5" aria-hidden="true" />
          <SelectValue placeholder="Add" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="compulsory">Compulsory</SelectItem>
          <SelectItem value="voluntary">Voluntary</SelectItem>
        </SelectContent>
      </Select>
    );
  }

  return (
    <div className="flex items-center gap-1">
      <Select
        value={participation}
        disabled={!editable}
        onValueChange={(value) =>
          editor.setDepParticipation(
            product.product_id,
            tier.key,
            value as "compulsory" | "voluntary",
          )
        }
      >
        <SelectTrigger
          className="h-8 w-32 text-xs"
          aria-label={`${label} dependant enrolment`}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="compulsory">Compulsory</SelectItem>
          <SelectItem value="voluntary">Voluntary</SelectItem>
        </SelectContent>
      </Select>
      {editable && (
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => editor.removeDepCoverage(product.product_id, tier.key)}
          aria-label={`Remove dependant cover from ${label}`}
          title="Remove dependant cover"
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}

export function UnifiedDependantPricing({
  product,
  tier,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  tier: FlexPricingTier;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const pid = product.product_id;
  const dep = editor.blockFor(pid).dependant ?? {};
  if (!effectiveDependantParticipation(dep, tier)) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const tierOverride = dep.modes?.[tier.key];
  const legacyOverride = dep.mode && dep.mode !== "slip_options" ? dep.mode : undefined;
  const recommended = tier.dependant_pricing?.mode ?? "none";
  const selected = tierOverride ?? legacyOverride ?? (
    recommended === "none" ? undefined : recommended
  );
  const mode = selected ?? "none";
  const scheme = dep.scheme ?? "ec_es_ef";
  const codes = SCHEME_CODES[scheme];
  const label = `${product.product_code} ${tier.cohort_label ?? "employees"} ${tier.label}`;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1">
        <Select
          value={selected}
          disabled={!editable}
          onValueChange={(value) => {
            if (value === "slip_options") return;
            editor.setDepMode(
              pid,
              [tier.key],
              value as Exclude<DependantPricingMode, "slip_options">,
            );
          }}
        >
          <SelectTrigger
            className="h-8 w-36 text-xs"
            aria-label={`${label} dependant pricing`}
          >
            <SelectValue placeholder="Required" />
          </SelectTrigger>
          <SelectContent>
            {DEP_MODE_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
            <SelectItem value="slip_options" disabled>
              Option / age-band
            </SelectItem>
          </SelectContent>
        </Select>
        {editable && tierOverride != null && (
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => editor.clearDepMode(pid, [tier.key])}
            aria-label={`Reset ${label} dependant pricing`}
            title="Reset to recommendation"
          >
            <RotateCcw className="size-3.5" aria-hidden="true" />
          </Button>
        )}
      </div>

      {mode === "family_group" && (
        <div className="grid grid-cols-3 gap-1">
          {FAMILY_ROLES.map((role) => {
            const recommendation = product.slip_family?.[tier.key]?.[role];
            const override = dep.family_tags?.[tier.key]?.[role];
            return (
              <label key={role} className="space-y-0.5">
                <span className="block text-2xs font-medium text-muted-foreground">
                  {codes[role]}
                </span>
                <Input
                  type="number"
                  min="0"
                  step="0.01"
                  disabled={!editable}
                  value={override ?? recommendation ?? ""}
                  onChange={(event) =>
                    editor.setFamilyTag(pid, [tier.key], role, event.target.value)
                  }
                  className="h-8 min-w-0 px-2 tabular-nums"
                  placeholder="Required"
                  aria-label={`${label} ${SCHEME_LABELS[scheme][role]} dependant charge`}
                />
              </label>
            );
          })}
        </div>
      )}

      {mode === "per_pax" && (
        <Input
          type="number"
          min="0"
          step="0.01"
          disabled={!editable}
          value={
            dep.per_pax?.[tier.key]?.flat ??
            product.slip_per_pax?.[tier.key] ??
            ""
          }
          onChange={(event) => editor.setPerPax(pid, [tier.key], event.target.value)}
          className="h-8 w-28 tabular-nums"
          placeholder="Required"
          aria-label={`${label} charge per covered dependant`}
        />
      )}

      {mode === "slip_options" && <LinkedOptionSummary tier={tier} />}
    </div>
  );
}

export function UnifiedDependantSettings({
  product,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const pid = product.product_id;
  const dep = editor.blockFor(pid).dependant ?? {};
  const [expanded, setExpanded] = useState(false);
  const coveredTiers = product.tiers.filter((tier) =>
    effectiveDependantParticipation(dep, tier),
  );
  if (!coveredTiers.length) return null;
  const scheme = dep.scheme ?? "ec_es_ef";
  const hasFamilyRates = coveredTiers.some(
    (tier) => effectiveDependantMode(dep, tier) === "family_group",
  );

  return (
    <section className="border-t border-border bg-muted/10">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs font-medium outline-none hover:bg-muted/25 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
        aria-expanded={expanded}
        aria-controls={`${pid}-dependant-settings`}
      >
        {expanded ? (
          <ChevronDown className="size-3.5" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-3.5" aria-hidden="true" />
        )}
        <Users className="size-3.5 text-muted-foreground" aria-hidden="true" />
        Dependant settings
      </button>
      {expanded && (
        <div
          id={`${pid}-dependant-settings`}
          className="flex flex-wrap items-end gap-x-6 gap-y-3 border-t border-border px-4 py-3"
        >
          {hasFamilyRates && (
            <div className="space-y-1">
              <span className="block text-2xs font-medium text-muted-foreground">
                Family labels
              </span>
              <Segmented
                value={scheme}
                onChange={(value) => editor.setScheme(pid, value)}
                options={SCHEME_OPTIONS}
                disabled={!editable}
              />
            </div>
          )}
          {(["spouse", "child"] as const).map((role) => (
            <fieldset key={role} className="flex items-center gap-2">
              <legend className="sr-only">{role} eligible ages</legend>
              <span className="w-12 text-xs font-medium capitalize">{role}</span>
              <Input
                type="number"
                min="0"
                value={editedAgeBound(dep, role, "min")}
                onChange={(event) =>
                  editor.setDepAgeLimit(pid, role, "min", event.target.value)
                }
                disabled={!editable}
                className="h-8 w-20 tabular-nums"
                placeholder={effectiveAgeBound(product, role, "min")}
                aria-label={`${role} minimum eligible age`}
              />
              <span className="text-xs text-muted-foreground">to</span>
              <Input
                type="number"
                min="0"
                value={editedAgeBound(dep, role, "max")}
                onChange={(event) =>
                  editor.setDepAgeLimit(pid, role, "max", event.target.value)
                }
                disabled={!editable}
                className="h-8 w-20 tabular-nums"
                placeholder={effectiveAgeBound(product, role, "max")}
                aria-label={`${role} maximum eligible age`}
              />
            </fieldset>
          ))}
        </div>
      )}
    </section>
  );
}
