import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { Check, CheckCheck, Pencil, Sparkles, X } from "lucide-react";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useConfirmCategory,
  useDeleteCategory,
  usePatchCategory,
  useUpdatePlan,
} from "@/api/hooks";
import { confidencePill, sourcePill, statusPill } from "@/lib/badges";
import { formatError } from "@/lib/errors";
import type {
  BasisModel,
  Category,
  PlanAssignment,
  PlanDetail,
  RateModel,
  RateTier,
  TemplateTier,
  VoluntaryRateBand,
} from "@/types";
import { toast } from "sonner";

export type MemberCount = { employees: number; dependants: number };

// Canonical tier vocabulary (mirror of backend dynamic_template._TIER_LABELS /
// product_registry TIER_SCHEMES): composite employee tiers first, dependant-only
// tiers after. Drives the add-tier picker and labels for tiers a slip carries
// that the product template doesn't declare (GD's "2 - EO" / "2 - SO/CO" split).
const TIER_VOCAB: { code: string; label: string }[] = [
  { code: "EO", label: "Employee Only" },
  { code: "ES", label: "Employee + Spouse" },
  { code: "EC", label: "Employee + Children" },
  { code: "EF", label: "Employee + Family" },
  { code: "SO", label: "Spouse Only" },
  { code: "CO", label: "Child(ren) Only" },
  { code: "SC", label: "Spouse & Child(ren)" },
  { code: "FO", label: "Family Only" },
];
const TIER_ORDER = new Map(TIER_VOCAB.map((t, i) => [t.code, i]));
const tierVocabLabel = (code: string) =>
  TIER_VOCAB.find((t) => t.code === code)?.label ?? code;

// A voluntary category prices by age band when it carries a voluntary_rates table
// (or is flagged age_banded). Compulsory / flat-voluntary plans don't.
export function isAgeBanded(c: Category): boolean {
  if (c.participation_model !== "voluntary") return false;
  const pa = (c.plan_assignments ?? {}) as PlanAssignment & {
    rate_basis?: string;
    voluntary_rates?: VoluntaryRateBand[] | null;
  };
  return pa.rate_basis === "age_banded" || !!pa.voluntary_rates;
}

// `basis` can be a plain amount (e.g. 1000000) or a salary-multiple expression
// ("24x basic monthly salary"). Only pure numbers are reformatted.
const NUMERIC_RE = /^-?\d+(\.\d+)?$/;

// Comma-stripped value to store — numbers are float-parsed downstream
// (coverage / fact-find), so the stored form must never contain separators.
function toCleanAmount(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const cleaned = trimmed.replace(/,/g, "");
  return NUMERIC_RE.test(cleaned) ? cleaned : trimmed;
}

// Display form: a pure number renders as 1,000,000.00; text passes through.
function formatAmount(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const cleaned = trimmed.replace(/,/g, "");
  if (!NUMERIC_RE.test(cleaned)) return trimmed;
  return Number(cleaned).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// Numeric-aware equality so an unchanged amount (stored "1000000.0" vs a
// re-cleaned "1000000.00") isn't treated as an edit and re-PATCHed.
function sameAmount(a: string, b: string): boolean {
  const ca = String(a).replace(/,/g, "").trim();
  const cb = String(b).replace(/,/g, "").trim();
  if (NUMERIC_RE.test(ca) && NUMERIC_RE.test(cb)) return Number(ca) === Number(cb);
  return ca === cb;
}

export function CategoryCard({
  category,
  planOptions,
  basisModel,
  rateModel,
  tiers,
  hasDependants,
  count,
  countsError = false,
  insuredEntities = [],
  onEditRule,
}: {
  category: Category;
  planOptions: PlanDetail[];
  basisModel: BasisModel;
  rateModel: RateModel;
  tiers: TemplateTier[];
  hasDependants: boolean;
  count?: MemberCount;
  // True when the member-counts query failed — shows "Count unavailable"
  // instead of a perpetual "Calculating…".
  countsError?: boolean;
  // The legal entities gating this category, already resolved by the parent in
  // the matcher's precedence (product-level field, else the slip's own value).
  // Empty = covers every entity.
  insuredEntities?: string[];
  onEditRule: () => void;
}) {
  const patch = usePatchCategory();
  const confirmCat = useConfirmCategory();
  const deleteCat = useDeleteCategory();
  const updatePlan = useUpdatePlan();
  const [showDelete, setShowDelete] = useState(false);

  const assignments = (category.plan_assignments ?? {}) as PlanAssignment;

  // Local field state so typing is smooth and concurrent field edits compose.
  // The parent remounts this card (its key includes updated_at) whenever the
  // server category changes, so these re-init from fresh props after each save.
  const [name, setName] = useState(category.display_name);
  const [planCode, setPlanCode] = useState(
    assignments.plan_code != null ? String(assignments.plan_code) : "",
  );
  const [basis, setBasis] = useState(formatAmount(assignments.basis ?? ""));
  // Standard (compulsory) per-S$1000 rate — editable here now the separate Rate
  // section is gone. Voluntary tiers price by age band (set in the panel below),
  // so they carry no flat rate.
  const [rate, setRate] = useState(
    assignments.premium_rate != null ? String(assignments.premium_rate) : "",
  );
  // Tiered medical (GHS/GMM): a rate + premium per dependant-tier (EO/ES/EC/EF),
  // edited inline now the standalone Rate section is gone. Init from the parsed /
  // previously-saved rate_tiers; a tiered product with none yet seeds a zero row
  // per template tier so the grid starts populated. The row set then lives in
  // this state alone — adding/removing a tier persists through rate_tiers.
  const [tierRates, setTierRates] = useState<Record<string, RateTier>>(() => {
    const stored = assignments.rate_tiers ?? {};
    if (Object.keys(stored).length > 0) return { ...stored };
    if (rateModel === "tiered") {
      return Object.fromEntries(
        tiers.map((t) => [t.code, { rate: 0, premium: 0 }]),
      );
    }
    return {};
  });
  // Flat annual policy premium (GBT travel): one annual figure for the whole
  // policy, no per-member rate. Stored as annual_premium + rate_basis annual_flat.
  const [annualPremium, setAnnualPremium] = useState(
    assignments.annual_premium != null
      ? formatAmount(String(assignments.annual_premium))
      : "",
  );
  // Earnings-based (WICA statutory): premium = rate × estimated annual earnings.
  const [earnings, setEarnings] = useState(
    assignments.estimated_annual_earnings != null
      ? formatAmount(String(assignments.estimated_annual_earnings))
      : "",
  );
  const [renamingPlan, setRenamingPlan] = useState(false);
  const [planLabel, setPlanLabel] = useState("");

  // Age-banded voluntary tiers (GTL/GCI) price off the shared age-band panel, so
  // they show a note instead of a flat rate. A flat voluntary tier (GPA options)
  // is NOT age-banded — it keeps the editable standard rate like a compulsory plan.
  const ageBanded = isAgeBanded(category);
  const basisNum = Number(toCleanAmount(basis));
  const hasBasis = Number.isFinite(basisNum) && basisNum > 0;
  // Rate ↔ premium are two views of the same figure (premium = amount covered /
  // 1000 × rate). Both are editable; editing one recomputes the other. The
  // canonical persisted value is premium_rate, so a typed premium back-computes
  // the rate (rate = premium × 1000 / amount covered).
  const premiumFor = (r: string): string => {
    const rn = Number(r);
    return hasBasis && Number.isFinite(rn) && rn > 0
      ? formatAmount(String((basisNum / 1000) * rn))
      : "";
  };
  const [premium, setPremium] = useState(() => premiumFor(rate));

  // The persisted plan behind the selected code — only it can be renamed (a
  // pre-materialization slip plan has no Plan record yet).
  const currentPlan = planOptions.find((p) => String(p.code) === planCode);

  // Insurer-facing report label for the selected plan ("4 Bed Restr Hosp /
  // Inpatient Expenses - S$10,000"). Editable even on active years — it's
  // report metadata, not coverage config.
  const [reportLabel, setReportLabel] = useState(
    currentPlan?.report_label ?? "",
  );
  const currentPlanId = currentPlan?.id;
  useEffect(() => {
    setReportLabel(currentPlan?.report_label ?? "");
    // Re-seed only when the selected plan itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPlanId]);
  const saveReportLabel = () => {
    const next = reportLabel.trim();
    if (!currentPlan || next === (currentPlan.report_label ?? "")) return;
    updatePlan.mutate(
      { id: currentPlan.id, patch: { report_label: next || null } },
      {
        onSuccess: () => toast.success("Report label saved"),
        onError: (e) => toast.error(`Report label: ${formatError(e)}`),
      },
    );
  };

  const savePatch = (p: Partial<Category>, label: string) =>
    patch.mutate(
      { id: category.id, patch: p },
      { onError: (e) => toast.error(`${label}: ${formatError(e)}`) },
    );

  const saveName = () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === category.display_name) {
      setName(category.display_name);
      return;
    }
    savePatch({ display_name: trimmed }, "Rename");
  };

  // Always write EVERY in-card editable plan_assignments field (plan_code, and
  // for sum-assured: basis + the flat standard rate) from current local state, so
  // two field edits firing within one refetch window can't clobber each other.
  // Other keys carry over from the mount snapshot (nothing else edits them while
  // mounted). Age-banded voluntary tiers have no flat rate (premium_rate is
  // popped server-side; the rate input isn't shown) — don't resurrect one.
  const writeAssignments = (over: Partial<PlanAssignment>, label: string) => {
    const base: Record<string, unknown> = {
      ...assignments,
      plan_code: planCode || null,
    };
    if (basisModel === "sum_assured") {
      base.basis = toCleanAmount(basis) || null;
      if (!ageBanded) {
        const r = rate.trim();
        const n = Number(r);
        base.premium_rate =
          r === "" || !Number.isFinite(n) ? assignments.premium_rate ?? null : n;
        if (base.premium_rate != null) base.rate_basis = "per_1000_si";
      }
    } else if (rateModel === "tiered") {
      // Medical tiered: persist the per-tier rate grid; the financials view sums
      // the tier premiums for the category's annual total.
      base.rate_tiers = tierRates;
      base.rate_basis = "tiered";
    } else if (rateModel === "flat") {
      // GBT travel: one flat annual policy premium, no per-member rate. Empty /
      // non-numeric carries the prior value rather than clearing it.
      const p = Number(toCleanAmount(annualPremium));
      base.annual_premium =
        annualPremium.trim() === "" || !Number.isFinite(p)
          ? assignments.annual_premium ?? null
          : p;
      base.rate_basis = "annual_flat";
    } else if (rateModel === "earnings_based") {
      // WICA statutory: premium = rate × estimated annual earnings. Persist both
      // inputs plus the derived annual premium — from the same resolved values
      // the card displays, so the saved premium matches what the broker saw.
      base.estimated_annual_earnings = resolvedEarnings;
      base.premium_rate = resolvedRate;
      base.annual_premium = earningsPremium;
      base.rate_basis = "earnings_based";
    } else {
      // Medical per-member (and other flat rate models): a premium rate per
      // employee, plus an optional separate rate for dependants. The annual total
      // is computed downstream from rate × matched headcount.
      const r = rate.trim();
      const n = Number(r);
      base.premium_rate =
        r === "" || !Number.isFinite(n) ? assignments.premium_rate ?? null : n;
      if (base.premium_rate != null) base.rate_basis = "per_member";
    }
    // A non-tiered product can still carry a per-tier rate split (a slip pricing
    // one plan by EO vs SO/CO, or tiers the broker added) — persist the edited
    // grid rather than the mount snapshot.
    if (rateModel !== "tiered" && Object.keys(tierRates).length > 0) {
      base.rate_tiers = tierRates;
    }
    savePatch(
      { plan_assignments: { ...base, ...over } as Category["plan_assignments"] },
      label,
    );
  };

  const savePlan = (code: string) => {
    setPlanCode(code);
    writeAssignments({ plan_code: code || null }, "Plan type");
  };

  const startRenamePlan = () => {
    setPlanLabel(currentPlan?.display_name ?? "");
    setRenamingPlan(true);
  };
  const savePlanName = () => {
    const next = planLabel.trim();
    if (!currentPlan || !next || next === currentPlan.display_name) {
      setRenamingPlan(false);
      return;
    }
    // Renames the shared Plan record, so the new name shows on every category
    // assigned to this plan type.
    updatePlan.mutate(
      { id: currentPlan.id, patch: { display_name: next } },
      {
        onSuccess: () => {
          toast.success("Plan type renamed");
          setRenamingPlan(false);
        },
        onError: (e) => toast.error(`Rename plan: ${formatError(e)}`),
      },
    );
  };

  // The card's Participation describes the EMPLOYEE. Mirror it into both
  // participation_model (the binary used across matching/enrollment) and
  // participation_detail.employee (preserving the dependant/direction split the
  // dependant card edits).
  const saveParticipation = (v: "compulsory" | "voluntary") =>
    savePatch(
      {
        participation_model: v,
        participation_detail: { ...(category.participation_detail ?? {}), employee: v },
      },
      "Participation",
    );

  const saveBasis = () => {
    const stored = toCleanAmount(basis); // commas stripped; text passes through
    setBasis(formatAmount(stored)); // re-display with separators / .00
    // Amount covered changed → the premium (rate × covered) follows; rate is held.
    const bn = Number(stored);
    const rn = Number(rate);
    if (Number.isFinite(bn) && bn > 0 && Number.isFinite(rn) && rn > 0)
      setPremium(formatAmount(String((bn / 1000) * rn)));
    if (sameAmount(stored, String(assignments.basis ?? ""))) return;
    writeAssignments({ basis: stored || null }, "Basis amount");
  };

  const saveRate = () => {
    const cur = assignments.premium_rate ?? null;
    const trimmed = rate.trim();
    const n = Number(trimmed);
    // Empty / non-numeric → revert the field (don't clear a set rate); unchanged
    // → no-op. writeAssignments reads premium_rate from local `rate` state.
    if (trimmed === "" || !Number.isFinite(n)) {
      setRate(cur != null ? String(cur) : "");
      return;
    }
    setPremium(premiumFor(trimmed)); // keep the premium view in sync
    if (n === cur) return;
    writeAssignments({}, "Standard rate");
  };

  // Premium is editable too: a typed premium back-computes the canonical rate
  // (needs a numeric amount covered). Non-numeric basis or blank → revert to the
  // rate-derived premium rather than persist an underivable figure.
  const savePremium = () => {
    const cur = assignments.premium_rate ?? null;
    const p = Number(toCleanAmount(premium));
    if (premium.trim() === "" || !Number.isFinite(p) || !hasBasis) {
      setPremium(premiumFor(rate));
      return;
    }
    const derived = (p * 1000) / basisNum;
    setRate(String(derived));
    setPremium(formatAmount(String(p)));
    if (derived === cur) return;
    // local `rate` is stale this render (setRate is async) — pass the derived rate
    // explicitly so the base's premium_rate (read from stale state) is overridden.
    writeAssignments({ premium_rate: derived, rate_basis: "per_1000_si" }, "Premium");
  };

  // Per-member medical: the annual premium rate per employee. Empty / non-numeric
  // reverts to the stored value rather than clearing it.
  const savePerMemberRate = () => {
    const cur = assignments.premium_rate ?? null;
    const trimmed = rate.trim();
    const n = Number(trimmed);
    if (trimmed === "" || !Number.isFinite(n)) {
      setRate(cur != null ? String(cur) : "");
      return;
    }
    if (n === cur) return;
    writeAssignments({}, "Premium rate per employee");
  };

  // Flat annual policy premium (GBT). Empty / non-numeric reverts to the stored
  // value; otherwise re-displays with separators and persists on change.
  const saveAnnualPremium = () => {
    const cur = assignments.annual_premium ?? null;
    const p = Number(toCleanAmount(annualPremium));
    if (annualPremium.trim() === "" || !Number.isFinite(p)) {
      setAnnualPremium(cur != null ? formatAmount(String(cur)) : "");
      return;
    }
    setAnnualPremium(formatAmount(String(p)));
    if (p === cur) return;
    writeAssignments({}, "Annual premium");
  };

  // Earnings-based (WICA): premium = rate × estimated annual earnings. Resolve
  // each input from the live field, falling back to the stored value when the
  // field is mid-edit/blank — the SAME resolution writeAssignments uses, so the
  // premium shown on the card is exactly the premium persisted (no divergence).
  const earningsNum = Number(toCleanAmount(earnings));
  const rateNum = Number(rate);
  const resolvedEarnings =
    Number.isFinite(earningsNum) && earningsNum > 0
      ? earningsNum
      : assignments.estimated_annual_earnings ?? null;
  const resolvedRate =
    Number.isFinite(rateNum) && rateNum > 0 ? rateNum : assignments.premium_rate ?? null;
  const earningsPremium =
    resolvedEarnings != null && resolvedRate != null
      ? resolvedEarnings * resolvedRate
      : null;
  const saveEarnings = () => {
    const cur = assignments.estimated_annual_earnings ?? null;
    const e = Number(toCleanAmount(earnings));
    if (earnings.trim() === "" || !Number.isFinite(e)) {
      setEarnings(cur != null ? formatAmount(String(cur)) : "");
      return;
    }
    setEarnings(formatAmount(String(e)));
    if (e === cur) return;
    writeAssignments({}, "Estimated annual earnings");
  };
  const saveEarningsRate = () => {
    const cur = assignments.premium_rate ?? null;
    const trimmed = rate.trim();
    const n = Number(trimmed);
    if (trimmed === "" || !Number.isFinite(n)) {
      setRate(cur != null ? String(cur) : "");
      return;
    }
    if (n === cur) return;
    writeAssignments({}, "Rate on earnings");
  };

  // Tiered medical: update one tier's rate or premium, then persist on blur.
  const setTierField = (code: string, field: "rate" | "premium", value: number) =>
    setTierRates((t) => ({
      ...t,
      [code]: { rate: t[code]?.rate ?? 0, premium: t[code]?.premium ?? 0, [field]: value },
    }));
  const saveTiers = () => writeAssignments({}, "Tier rates");

  // The tier rows the card shows = exactly the tiers in state (slip-parsed,
  // template-seeded, or broker-added), in template order first then canonical.
  // Labels prefer the template's, then the slip's own vocabulary
  // (plan_assignments.tier_labels), then the canonical name.
  const slipTierLabels = assignments.tier_labels ?? {};
  const tierLabelFor = (code: string) =>
    tiers.find((t) => t.code === code)?.label ??
    slipTierLabels[code] ??
    tierVocabLabel(code);
  const effectiveTiers: TemplateTier[] = (() => {
    const templateIdx = new Map(tiers.map((t, i) => [t.code, i]));
    const codes = Object.keys(tierRates);
    codes.sort((a, b) => {
      const ta = templateIdx.get(a);
      const tb = templateIdx.get(b);
      if (ta != null || tb != null) return (ta ?? 99) - (tb ?? 99);
      return (TIER_ORDER.get(a) ?? 99) - (TIER_ORDER.get(b) ?? 99);
    });
    return codes.map((code) => ({ code, label: tierLabelFor(code) }));
  })();
  const addableTiers = TIER_VOCAB.filter(
    (t) => !effectiveTiers.some((e) => e.code === t.code),
  );
  const addTier = (code: string) => {
    setTierRates((t) => ({ ...t, [code]: { rate: 0, premium: 0 } }));
    // setTierRates is async — persist the next state explicitly.
    writeAssignments(
      { rate_tiers: { ...tierRates, [code]: { rate: 0, premium: 0 } } },
      "Add tier",
    );
  };
  const removeTier = (code: string) => {
    const { [code]: _drop, ...rest } = tierRates;
    setTierRates(rest);
    writeAssignments(
      { rate_tiers: Object.keys(rest).length ? rest : null },
      "Remove tier",
    );
  };
  // Broker opened the (initially empty) tier grid on a non-tiered product to
  // add a per-tier rate split by hand.
  const [tierGridOpened, setTierGridOpened] = useState(false);
  const showTierGrid =
    rateModel === "tiered" || Object.keys(tierRates).length > 0 || tierGridOpened;
  const tierAnnual = effectiveTiers.reduce(
    (sum, t) => sum + (tierRates[t.code]?.premium ?? 0),
    0,
  );

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {sourcePill(category.source)}
          {statusPill(category.status)}
          {confidencePill(category.confidence)}
          {category.human_modified && (
            <Badge variant="outline" className="gap-1">
              <Sparkles className="size-3" /> Edited
            </Badge>
          )}
          {/* Dependant-scope slip row: standalone dependant cover (a GPA/GTL
              option level or a dependants sheet), auto-detected at parse — it
              feeds dependant pricing in enrollment, never an employee tier. */}
          {(category.plan_assignments as { member_scope?: string } | null)
            ?.member_scope === "dependant" && (
            <Badge variant="outline" className="text-[10px]">
              Dependant option
            </Badge>
          )}
          {/* Multi-entity products (WICA per-subsidiary blocks): which legal
              entities this category covers. Click to edit — this is a real
              matching gate, so it has to agree with the roster's Entity value. */}
          {/* Read-only: entities are chosen ONCE per product on the setup
              header ("Entities covered") and gate every category. Shown here so
              an active restriction is visible where the categories are. */}
          {insuredEntities.length > 0 && (
            <Badge
              variant="outline"
              className="max-w-64 truncate text-[10px]"
              title={`Only employees of ${insuredEntities.join(", ")} match this category`}
            >
              {insuredEntities.join(", ")}
            </Badge>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {category.status !== "confirmed" && (
            <Button
              size="sm"
              variant="outline"
              disabled={confirmCat.isPending}
              onClick={() =>
                confirmCat.mutateAsync(category.id).then(
                  () => toast.success(`Confirmed ${category.display_name}`),
                  (e) => toast.error(formatError(e)),
                )
              }
            >
              <CheckCheck className="size-3.5" /> Confirm
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={onEditRule}>
            <Pencil className="size-3.5" /> Edit rule
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Delete category"
            className="text-error hover:text-error"
            onClick={() => setShowDelete(true)}
          >
            <X className="size-4" />
          </Button>
        </div>
      </div>

      <div
        className={`grid gap-3 items-end ${
          basisModel === "sum_assured"
            ? "grid-cols-[1.6fr_1fr_1fr_1.4fr]"
            : "grid-cols-[2fr_1fr_1fr]"
        }`}
      >
        <Field label="Employee Category">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={saveName}
            className="h-8 text-sm"
          />
        </Field>
        <Field label="Plan Type">
          {renamingPlan ? (
            <div className="flex items-center gap-1">
              <Input
                autoFocus
                value={planLabel}
                placeholder="Plan type name"
                onChange={(e) => setPlanLabel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") savePlanName();
                  if (e.key === "Escape") setRenamingPlan(false);
                }}
                className="h-8 min-w-0 flex-1 text-sm"
              />
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8 shrink-0"
                aria-label="Save plan type name"
                onClick={savePlanName}
              >
                <Check className="size-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8 shrink-0"
                aria-label="Cancel rename"
                onClick={() => setRenamingPlan(false)}
              >
                <X className="size-4" />
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-1">
              <Select value={planCode} onValueChange={savePlan}>
                <SelectTrigger className="h-8 min-w-0 flex-1 text-sm">
                  <SelectValue placeholder="Plan type" />
                </SelectTrigger>
                <SelectContent>
                  {/* Fall back to the category's own plan code when the product's
                      plans aren't materialized yet, so the current value renders. */}
                  {planCode &&
                    !planOptions.some((p) => String(p.code) === planCode) && (
                      <SelectItem value={planCode}>{planCode}</SelectItem>
                    )}
                  {planOptions.map((p) => (
                    <SelectItem key={p.code} value={String(p.code)}>
                      {p.display_name || p.code}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {currentPlan && (
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 shrink-0"
                  aria-label="Rename plan type"
                  title="Rename this plan type"
                  onClick={startRenamePlan}
                >
                  <Pencil className="size-3.5" />
                </Button>
              )}
            </div>
          )}
          {currentPlan && (
            <Input
              value={reportLabel}
              onChange={(e) => setReportLabel(e.target.value)}
              onBlur={saveReportLabel}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveReportLabel();
              }}
              placeholder="Insurer report label (e.g. 4 Bed Restr Hosp / S$60,000)"
              title="Shown as this plan's Plan/Basis of Cover on insurer report columns"
              className="mt-1 h-7 text-xs"
            />
          )}
        </Field>
        <Field label="Participation">
          <Select
            value={category.participation_model ?? ""}
            onValueChange={(v) =>
              saveParticipation(v as "compulsory" | "voluntary")
            }
          >
            <SelectTrigger className="h-8 text-sm">
              <SelectValue placeholder="Select…" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="compulsory">Compulsory</SelectItem>
              <SelectItem value="voluntary">Voluntary</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        {basisModel === "sum_assured" && (
          <Field label="Basis Amount (Amount Covered Per Employee)">
            <Input
              list="category-basis-bases"
              value={basis}
              onChange={(e) => setBasis(e.target.value)}
              onBlur={saveBasis}
              placeholder="e.g. 24x monthly salary or 1,000,000.00"
              className="h-8 text-sm"
            />
            <datalist id="category-basis-bases">
              {[
                "Flat sum",
                "12x basic monthly salary",
                "24x basic monthly salary",
                "36x basic monthly salary",
                "% of Group Term Life",
              ].map((b) => (
                <option key={b} value={b} />
              ))}
            </datalist>
          </Field>
        )}
      </div>

      {basisModel === "sum_assured" ? (
        <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
          {ageBanded ? (
            <p className="text-[11px] text-muted-foreground">
              Voluntary — priced by age band. Premium per employee = amount
              covered ÷ 1,000 × the rate for the member's age band (set in{" "}
              <span className="font-medium text-foreground">
                Voluntary Age-Band Rates
              </span>{" "}
              below).
            </p>
          ) : (
            <>
              <Field label="Standard Rate (per S$1,000 SI)">
                <Input
                  type="number"
                  value={rate}
                  onChange={(e) => setRate(e.target.value)}
                  onBlur={saveRate}
                  placeholder="e.g. 1.62"
                  className="h-8 w-36 text-sm"
                />
              </Field>
              <Field label="Premium per employee">
                <Input
                  value={premium}
                  onChange={(e) => setPremium(e.target.value)}
                  onBlur={savePremium}
                  placeholder="e.g. 3,060.00"
                  className="h-8 w-36 text-sm"
                />
              </Field>
            </>
          )}
        </div>
      ) : rateModel === "tiered" ? (
        <TierRateGrid
          tiers={effectiveTiers}
          tierRates={tierRates}
          annual={tierAnnual}
          addable={addableTiers}
          onField={setTierField}
          onCommit={saveTiers}
          onAdd={addTier}
          onRemove={removeTier}
        />
      ) : rateModel === "flat" ? (
        <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
          <Field label="Annual Premium (whole policy)">
            <Input
              value={annualPremium}
              onChange={(e) => setAnnualPremium(e.target.value)}
              onBlur={saveAnnualPremium}
              placeholder="e.g. 3,169.80"
              className="h-8 w-44 text-sm"
            />
          </Field>
          <p className="text-[11px] text-muted-foreground">
            One flat annual premium for the whole policy — not a per-member rate.
          </p>
        </div>
      ) : rateModel === "earnings_based" ? (
        <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
          <Field label="Estimated Annual Earnings">
            <Input
              value={earnings}
              onChange={(e) => setEarnings(e.target.value)}
              onBlur={saveEarnings}
              placeholder="e.g. 71,960,473"
              className="h-8 w-44 text-sm"
            />
          </Field>
          <Field label="Rate on Earnings">
            <Input
              type="number"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              onBlur={saveEarningsRate}
              placeholder="e.g. 0.00033"
              className="h-8 w-32 text-sm"
            />
          </Field>
          <div className="flex flex-col gap-1">
            <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Annual Premium
            </Label>
            <div className="flex h-8 items-center text-sm text-foreground">
              {earningsPremium != null
                ? earningsPremium.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })
                : "—"}
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-end gap-4 border-t border-border pt-3">
          <Field label="Premium Rate Per Employee">
            <Input
              type="number"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              onBlur={savePerMemberRate}
              placeholder="e.g. 378"
              className="h-8 w-44 text-sm"
            />
          </Field>
          {/* Dependant rate moved to the Dependant Category & Plan Type section. */}
          {!showTierGrid && (
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => setTierGridOpened(true)}
            >
              + Add tier rates (EO / ES / …)
            </Button>
          )}
        </div>
      )}

      {/* A per-member/flat product whose slip split this plan's rate by member
          tier ("2 - EO" / "2 - SO/CO") — show and edit the split alongside the
          model's own rate field. */}
      {basisModel !== "sum_assured" && rateModel !== "tiered" && showTierGrid && (
        <TierRateGrid
          tiers={effectiveTiers}
          tierRates={tierRates}
          annual={tierAnnual}
          addable={addableTiers}
          onField={setTierField}
          onCommit={saveTiers}
          onAdd={addTier}
          onRemove={removeTier}
        />
      )}

      {category.rule_human_readable && (
        <div className="mt-3 font-mono text-xs text-muted-foreground">
          {category.rule_human_readable}
        </div>
      )}

      <div className="mt-2 text-[11px] text-muted-foreground">
        {count ? (
          <>
            <span className="font-medium text-foreground">{count.employees}</span>{" "}
            employee{count.employees === 1 ? "" : "s"}
            {hasDependants && (
              <>
                {" · "}
                <span className="font-medium text-foreground">
                  {count.dependants}
                </span>{" "}
                dependant{count.dependants === 1 ? "" : "s"}
              </>
            )}{" "}
            <span className="text-muted-foreground/70">matched from roster</span>
          </>
        ) : countsError ? (
          "Count unavailable"
        ) : (
          "Calculating from roster…"
        )}
      </div>

      <AlertDialog
        open={showDelete}
        onOpenChange={setShowDelete}
        title="Delete this category?"
        description={
          <>
            Permanently removes <strong>{category.display_name}</strong>. The
            deletion is logged in the audit trail. This cannot be undone.
          </>
        }
        loading={deleteCat.isPending}
        onConfirm={async () => {
          await deleteCat.mutateAsync(category.id);
          toast.success("Category deleted");
          setShowDelete(false);
        }}
      />
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}

// Tiered medical rate editor (EO/ES/EC/EF): a rate + premium per dependant-tier,
// with the annual total summed across tiers. Tiers can be added (from the
// canonical vocabulary) and removed per category, so a plan priced for tiers
// the template doesn't declare — or an extra tier a client negotiates — is
// configurable without re-uploading the slip.
function TierRateGrid({
  tiers,
  tierRates,
  annual,
  addable,
  onField,
  onCommit,
  onAdd,
  onRemove,
}: {
  tiers: TemplateTier[];
  tierRates: Record<string, RateTier>;
  annual: number;
  addable: { code: string; label: string }[];
  onField: (code: string, field: "rate" | "premium", value: number) => void;
  onCommit: () => void;
  onAdd: (code: string) => void;
  onRemove: (code: string) => void;
}) {
  const safe = (v: string) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="grid grid-cols-[1.4fr_1fr_1fr_auto] items-center gap-2">
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Tier
        </Label>
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Rate
        </Label>
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Premium
        </Label>
        <span />
        {tiers.map((t) => {
          const cell = tierRates[t.code] ?? { rate: 0, premium: 0 };
          return (
            <div key={t.code} className="contents">
              <div className="flex items-baseline gap-1 text-sm text-foreground">
                {t.code}
                <span className="text-[11px] text-muted-foreground">{t.label}</span>
              </div>
              <Input
                type="number"
                value={cell.rate || ""}
                onChange={(e) => onField(t.code, "rate", safe(e.target.value))}
                onBlur={onCommit}
                className="h-8 text-sm"
              />
              <Input
                type="number"
                value={cell.premium || ""}
                onChange={(e) => onField(t.code, "premium", safe(e.target.value))}
                onBlur={onCommit}
                className="h-8 text-sm"
              />
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={`Remove tier ${t.code}`}
                title={`Remove tier ${t.code}`}
                className="text-error hover:text-error"
                onClick={() => onRemove(t.code)}
              >
                <X className="size-3.5" />
              </Button>
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="text-[11px] text-muted-foreground">
          Annual premium (sum of tiers):{" "}
          <span className="font-medium text-foreground">
            {annual.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        </div>
        {addable.length > 0 && (
          <Select value="" onValueChange={onAdd}>
            <SelectTrigger className="h-7 w-48 text-[11px] text-muted-foreground">
              <SelectValue placeholder="+ Add tier" />
            </SelectTrigger>
            <SelectContent>
              {addable.map((t) => (
                <SelectItem key={t.code} value={t.code}>
                  {t.code} — {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  );
}
