import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Trash2, Users } from "lucide-react";
import { toast } from "sonner";
import {
  type DependantConfig,
  type DependantPricingMode,
  type FamilyRole,
  type FamilyScheme,
  type FlexAgeBand,
  type FlexPriceSource,
  type FlexPricingBag,
  type FlexPricingMode,
  type FlexPricingProduct,
  type FlexPricingProductBlock,
  type FlexPricingTier,
  useFlexPricing,
  useSaveFlexPricing,
} from "@/api/enrollment";
import { formatError } from "@/lib/errors";
import { ALL_AGES_LABEL, planRows, planScalar } from "@/lib/flexTiers";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Segmented } from "@/components/ui/segmented";

/** Sensible starting age bands a broker can tweak (5-/10-year style). */
const DEFAULT_BANDS: FlexAgeBand[] = [
  { label: "<30", min: 0, max: 29 },
  { label: "30–39", min: 30, max: 39 },
  { label: "40–49", min: 40, max: 49 },
  { label: "50–59", min: 50, max: 59 },
  { label: "60+", min: 60, max: 200 },
];

/** The single implicit band a plan-type product is stored under, so the matrix
 *  shape + resolver (age → band) stay unchanged (min/max null matches any age). */
const ALL_AGES: FlexAgeBand = { label: ALL_AGES_LABEL, min: null, max: null };

const EMPTY_BLOCK: FlexPricingProductBlock = { age_bands: [], price_tags: {} };

/** Placeholder for a price input: the slip baseline when present, else "0". */
function slipPlaceholder(value: number | null | undefined): string {
  return value != null ? String(value) : "0";
}

const MODE_OPTIONS: { value: FlexPricingMode; label: string }[] = [
  { value: "age_banded", label: "Age-banded" },
  { value: "plan_type", label: "Per plan" },
];

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

const DIRECTION_VARIANT: Record<string, "good" | "warn" | "outline"> = {
  upgrade: "good",
  downgrade: "warn",
  same: "outline",
};

export type FlexPricingEditor = {
  products: FlexPricingProduct[];
  isLoading: boolean;
  dirty: boolean;
  saving: boolean;
  blockFor: (pid: string) => FlexPricingProductBlock;
  modeFor: (product: FlexPricingProduct) => FlexPricingMode;
  setMode: (pid: string, mode: FlexPricingMode, currentMode: FlexPricingMode) => void;
  setBands: (pid: string, bands: FlexAgeBand[]) => void;
  /** Set an age-banded cell on one or MORE tier keys at once (fan-out, as below). */
  setCell: (pid: string, tierKeys: string[], label: string, value: string) => void;
  /** Set a plan-type price on one or MORE tier keys at once — so a plan priced
   *  identically across job-category cohorts is edited in a single row that fans
   *  out to every cohort key (see `lib/flexTiers`). */
  setPlanPrice: (pid: string, tierKeys: string[], value: string) => void;
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
  onSave: () => void;
};

/**
 * Editing state for the per-policy-year flex price-tag matrix + dependant config.
 * Shared by the inline per-product editors (rendered in the Flex-funding section
 * when a window's price-tag source for that product is the manual matrix).
 */
export function useFlexPricingEditor(
  policyYearId: string | undefined,
): FlexPricingEditor {
  const { data } = useFlexPricing(policyYearId);
  const save = useSaveFlexPricing(policyYearId);
  const [bag, setBag] = useState<FlexPricingBag>({});
  const [dirty, setDirty] = useState(false);

  // Per-product stash of each mode's bands + price tags, so toggling
  // Age-banded ↔ Per plan is fully reversible — the shape you leave is restored
  // verbatim when you switch back, so an accidental click never drops values.
  const modeStash = useRef<
    Record<
      string,
      Partial<
        Record<
          FlexPricingMode,
          { age_bands: FlexAgeBand[]; price_tags: Record<string, Record<string, number | null>> }
        >
      >
    >
  >({});

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
    modeStash.current = {};
  }, [data, policyYearId]);

  const blockFor = (pid: string): FlexPricingProductBlock =>
    bag.products?.[pid] ?? EMPTY_BLOCK;

  const setBlock = (pid: string, block: FlexPricingProductBlock) => {
    setBag((b) => ({ ...b, products: { ...b.products, [pid]: block } }));
    setDirty(true);
  };

  const setBands = (pid: string, age_bands: FlexAgeBand[]) =>
    setBlock(pid, { ...blockFor(pid), age_bands });

  const setCell = (pid: string, tierKeys: string[], label: string, value: string) => {
    const block = blockFor(pid);
    const tags = { ...block.price_tags };
    const num = value.trim() === "" ? null : Number(value);
    for (const key of tierKeys) {
      const row = { ...(tags[key] ?? {}) };
      if (num === null) delete row[label];
      else row[label] = num;
      tags[key] = row;
    }
    setBlock(pid, { ...block, price_tags: tags });
  };

  // Plan-type products: one price per plan, stored under the single ALL_AGES band
  // (collapsing any stale multi-band rows) so the matrix shape stays valid. Writes
  // to EVERY passed tier key so a plan folded across cohorts prices all of them at
  // once — blank clears the plan from all of them.
  const setPlanPrice = (pid: string, tierKeys: string[], value: string) => {
    const block = blockFor(pid);
    const tags: Record<string, Record<string, number | null>> = {};
    for (const [k, row] of Object.entries(block.price_tags)) {
      const v = row[ALL_AGES.label];
      if (v != null) tags[k] = { [ALL_AGES.label]: v };
    }
    const num = value.trim() === "" ? null : Number(value);
    for (const key of tierKeys) {
      if (num === null) delete tags[key];
      else tags[key] = { [ALL_AGES.label]: num };
    }
    setBlock(pid, { ...block, age_bands: [ALL_AGES], price_tags: tags });
  };

  // Per-policy-year override of a product's pricing shape. Each mode keeps its own
  // bands + price tags stashed per product, so toggling is reversible: the shape
  // we leave is stashed under `currentMode` and restored verbatim if switched back.
  // Only the FIRST switch to a mode with no stash derives from the current shape —
  // plan-type collapses each tier to the single ALL_AGES band; age-banded seeds the
  // default bands when none exist (keeping existing bands otherwise).
  const setMode = (pid: string, mode: FlexPricingMode, currentMode: FlexPricingMode) => {
    if (mode === currentMode) return;
    const block = blockFor(pid);
    const stash = modeStash.current;
    stash[pid] = {
      ...stash[pid],
      [currentMode]: { age_bands: block.age_bands, price_tags: block.price_tags },
    };

    const saved = stash[pid]?.[mode];
    if (saved) {
      setBlock(pid, { ...block, mode, age_bands: saved.age_bands, price_tags: saved.price_tags });
      return;
    }
    if (mode === "plan_type") {
      const tags: Record<string, Record<string, number | null>> = {};
      for (const [k, row] of Object.entries(block.price_tags)) {
        const vals = Object.values(row).filter((v): v is number => typeof v === "number");
        if (vals.length) tags[k] = { [ALL_AGES.label]: vals[0] };
      }
      setBlock(pid, { mode, age_bands: [ALL_AGES], price_tags: tags });
    } else {
      const keep =
        block.age_bands.length && block.age_bands[0].label !== ALL_AGES.label;
      setBlock(pid, {
        mode,
        age_bands: keep ? block.age_bands : DEFAULT_BANDS,
        price_tags: block.price_tags,
      });
    }
  };

  // Effective config shape: the per-year override if set, else the product default.
  const modeFor = (product: FlexPricingProduct): FlexPricingMode =>
    bag.products?.[product.product_id]?.mode ?? product.pricing_mode;

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

  const onSave = () =>
    save.mutate(bag, {
      onSuccess: () => {
        setDirty(false);
        toast.success("Flex price tags saved");
      },
      onError: (e) => toast.error(formatError(e)),
    });

  return {
    products: data?.products ?? [],
    isLoading: !data,
    dirty,
    saving: save.isPending,
    blockFor,
    modeFor,
    setMode,
    setBands,
    setCell,
    setPlanPrice,
    setDepMode,
    setScheme,
    setFamilyTag,
    setPerPax,
    setDepAgeLimit,
    onSave,
  };
}

/** The price-tag matrix (age-banded or per-plan) + dependant-pricing config for ONE
 *  product, rendered inline in the Flex-funding section. Under the "slip" source the
 *  slip values appear as input placeholders (blank = use the slip, typed = a sparse
 *  override that corrects a wrong extraction); under "manual" the broker defines
 *  everything. Both write to the same matrix — the source only decides the baseline. */
export function ProductFlexEditor({
  product,
  editor,
  source,
}: {
  product: FlexPricingProduct;
  editor: FlexPricingEditor;
  source: FlexPriceSource;
}) {
  const pid = product.product_id;
  const block = editor.blockFor(pid);
  const mode = editor.modeFor(product);
  // Guard the mode toggle: if the product already has prices, confirm before
  // reshaping so an accidental click can't change layout. Values are preserved
  // either way (the editor stashes each mode's shape), but the dialog stops the
  // jarring "my edits vanished" surprise.
  const [pendingMode, setPendingMode] = useState<FlexPricingMode | null>(null);
  const hasPriceData = useMemo(
    () =>
      Object.values(block.price_tags ?? {}).some((row) =>
        Object.values(row).some((v) => typeof v === "number"),
      ),
    [block.price_tags],
  );
  const onMode = (m: FlexPricingMode) => {
    if (m === mode) return;
    if (hasPriceData) setPendingMode(m);
    else editor.setMode(pid, m, mode);
  };
  // Under the slip source, each tier's employee tag defaults to the slip premium.
  const slipByTier = useMemo<Record<string, number | null>>(
    () =>
      source === "slip"
        ? Object.fromEntries(product.tiers.map((t) => [t.key, t.slip_premium]))
        : {},
    [source, product.tiers],
  );
  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/10 p-2.5">
      {source === "slip" && (
        <p className="text-[11px] text-muted-foreground">
          Values default to the placement slip (shown as placeholders). Edit any
          field to correct a wrong extraction — typed values override the slip.
        </p>
      )}
      {mode === "age_banded" ? (
        <ProductMatrix
          code={product.product_code}
          mode={mode}
          onMode={onMode}
          tiers={product.tiers}
          bands={block.age_bands}
          priceTags={block.price_tags}
          slipByTier={slipByTier}
          onBands={(bands) => editor.setBands(pid, bands)}
          onCell={(tierKeys, label, v) => editor.setCell(pid, tierKeys, label, v)}
        />
      ) : (
        <PlanPriceList
          code={product.product_code}
          mode={mode}
          onMode={onMode}
          tiers={product.tiers}
          priceTags={block.price_tags}
          slipByTier={slipByTier}
          onPrice={(tierKeys, v) => editor.setPlanPrice(pid, tierKeys, v)}
        />
      )}
      <DependantPricing
        product={product}
        dep={block.dependant ?? {}}
        showSlip={source === "slip"}
        onMode={(m) => editor.setDepMode(pid, m)}
        onScheme={(s) => editor.setScheme(pid, s)}
        onFamilyTag={(tierKeys, role, v) => editor.setFamilyTag(pid, tierKeys, role, v)}
        onPerPax={(tierKeys, v) => editor.setPerPax(pid, tierKeys, v)}
      />
      <AlertDialog
        open={pendingMode !== null}
        onOpenChange={(o) => !o && setPendingMode(null)}
        title={`Switch ${product.product_code} to ${
          pendingMode === "plan_type" ? "Per plan" : "Age-banded"
        }?`}
        description={
          <>
            This reshapes {product.product_code}'s price tags. Your current{" "}
            {mode === "plan_type" ? "per-plan" : "age-banded"} values are kept and
            restored if you switch back.
          </>
        }
        confirmLabel="Switch"
        confirmVariant="default"
        onConfirm={() => {
          if (pendingMode) editor.setMode(pid, pendingMode, mode);
          setPendingMode(null);
        }}
      />
    </div>
  );
}

/** A tier's row label: plan name, its cohort (only when a split row needs
 *  disambiguating), and the up/downgrade badge. Shared by every price table. */
function TierLabel({
  rep,
  cohortLabel,
}: {
  rep: FlexPricingTier;
  cohortLabel: string | null;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-foreground">{rep.label}</span>
      {cohortLabel && (
        <span className="text-[11px] text-muted-foreground">· {cohortLabel}</span>
      )}
      {!rep.is_baseline && (
        <Badge
          variant={DIRECTION_VARIANT[rep.direction] ?? "outline"}
          className="text-[10px]"
        >
          {rep.direction}
        </Badge>
      )}
    </div>
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
        <span className="ml-1 text-[11px] text-muted-foreground">· {cohortLabel}</span>
      )}
    </span>
  );
}

interface MatrixProps {
  code: string;
  mode: FlexPricingMode;
  onMode: (m: FlexPricingMode) => void;
  tiers: FlexPricingTier[];
  bands: FlexAgeBand[];
  priceTags: Record<string, Record<string, number | null>>;
  /** Slip baseline per tier shown as the input placeholder (blank = use it). */
  slipByTier: Record<string, number | null>;
  onBands: (bands: FlexAgeBand[]) => void;
  /** Fan the cell out to EVERY key in the row (a plan folded across cohorts). */
  onCell: (tierKeys: string[], label: string, value: string) => void;
}

function ProductMatrix({
  code,
  mode,
  onMode,
  tiers,
  bands,
  priceTags,
  slipByTier,
  onBands,
  onCell,
}: MatrixProps) {
  const addBand = () =>
    onBands([...bands, { label: `Band ${bands.length + 1}`, min: null, max: null }]);
  const setBand = (i: number, patch: Partial<FlexAgeBand>) =>
    onBands(bands.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const removeBand = (i: number) => onBands(bands.filter((_, j) => j !== i));

  // Fold cohort tiers that share a plan into one row when they price identically
  // across ALL bands (whole-row compare); a plan whose cohorts differ stays split,
  // each row labelled by its cohort. Editing a folded cell fans out to every key.
  const rows = planRows(tiers, (t) => [priceTags[t.key] ?? null, t.slip_premium]);

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/30 px-3 py-2">
        <span className="text-sm font-medium text-foreground">{code}</span>
        <div className="flex items-center gap-1.5">
          {bands.length === 0 && (
            <Button variant="ghost" size="sm" onClick={() => onBands(DEFAULT_BANDS)}>
              Use default bands
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={addBand}>
            <Plus className="size-3.5" /> Age band
          </Button>
          <Segmented value={mode} onChange={onMode} options={MODE_OPTIONS} />
        </div>
      </div>

      {/* Age-band editor row */}
      {bands.length > 0 && (
        <div className="flex flex-wrap gap-2 border-b border-border px-3 py-2">
          {bands.map((band, i) => (
            <div
              key={i}
              className="flex items-center gap-1 rounded-md border border-border bg-card px-1.5 py-1"
            >
              <Input
                value={band.label}
                onChange={(e) => setBand(i, { label: e.target.value })}
                className="h-7 w-20 text-xs"
                placeholder="label"
              />
              <Input
                type="number"
                value={band.min ?? ""}
                onChange={(e) =>
                  setBand(i, { min: e.target.value === "" ? null : Number(e.target.value) })
                }
                className="h-7 w-14 text-xs"
                placeholder="min"
              />
              <span className="text-muted-foreground">–</span>
              <Input
                type="number"
                value={band.max ?? ""}
                onChange={(e) =>
                  setBand(i, { max: e.target.value === "" ? null : Number(e.target.value) })
                }
                className="h-7 w-14 text-xs"
                placeholder="max"
              />
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => removeBand(i)}
                aria-label="Remove band"
              >
                <Trash2 className="size-3.5 text-muted-foreground" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Price matrix: tiers (rows) × age bands (columns) */}
      {bands.length === 0 ? (
        <p className="px-3 py-3 text-xs text-muted-foreground">
          Add an age band to start pricing this product's tiers.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted-foreground">
                <th className="px-3 py-2 text-left font-medium">Tier</th>
                {bands.map((b, i) => (
                  <th key={i} className="px-2 py-2 text-left font-medium">
                    {b.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ keys, rep, cohortLabel }) => (
                <tr key={rep.key} className="border-b border-border last:border-0">
                  <td className="px-3 py-1.5">
                    <TierLabel rep={rep} cohortLabel={cohortLabel} />
                  </td>
                  {bands.map((band, i) => (
                    <td key={i} className="px-2 py-1.5">
                      <Input
                        type="number"
                        value={priceTags[rep.key]?.[band.label] ?? ""}
                        onChange={(e) => onCell(keys, band.label, e.target.value)}
                        className="h-8 w-24"
                        placeholder={slipPlaceholder(slipByTier[rep.key])}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface PlanListProps {
  code: string;
  mode: FlexPricingMode;
  onMode: (m: FlexPricingMode) => void;
  tiers: FlexPricingTier[];
  priceTags: Record<string, Record<string, number | null>>;
  /** Slip baseline per tier shown as the input placeholder (blank = use it). */
  slipByTier: Record<string, number | null>;
  /** Fan the price out to EVERY key in the row (a plan folded across cohorts). */
  onPrice: (tierKeys: string[], value: string) => void;
}

/** Plan-type pricing: a single price tag per plan (no age bands). Used for every
 *  product not configured as age-banded. Cohort tiers that share a plan and price
 *  identically fold into one row (see `lib/flexTiers`); a plan whose cohorts differ
 *  stays split so no price is silently merged. */
function PlanPriceList({
  code,
  mode,
  onMode,
  tiers,
  priceTags,
  slipByTier,
  onPrice,
}: PlanListProps) {
  // One row per plan when its cohort tiers agree on slip + stored price; otherwise
  // split back to per-cohort rows (labelled) so a genuine divergence is never hidden.
  const rows = planRows(tiers, (t) => [t.slip_premium, planScalar(priceTags, t.key)]);

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/30 px-3 py-2">
        <span className="text-sm font-medium text-foreground">{code}</span>
        <Segmented value={mode} onChange={onMode} options={MODE_OPTIONS} />
      </div>
      <table className="w-full text-sm">
        <tbody>
          {rows.map(({ keys, rep, cohortLabel }) => (
            <tr key={rep.key} className="border-b border-border last:border-0">
              <td className="px-3 py-1.5">
                <TierLabel rep={rep} cohortLabel={cohortLabel} />
              </td>
              <td className="px-3 py-1.5 text-right">
                <Input
                  type="number"
                  value={planScalar(priceTags, rep.key) ?? ""}
                  onChange={(e) => onPrice(keys, e.target.value)}
                  className="ml-auto h-8 w-28"
                  placeholder={slipPlaceholder(slipByTier[rep.key])}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface DependantProps {
  product: FlexPricingProduct;
  dep: DependantConfig;
  /** When the source is "slip", show the slip rates as placeholders (blank = use
   *  the slip); under "manual" the inputs start blank. */
  showSlip: boolean;
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
  showSlip,
  onMode,
  onScheme,
  onFamilyTag,
  onPerPax,
}: DependantProps) {
  const mode = dep.mode ?? product.dependant_suggested_mode;
  const scheme = dep.scheme ?? "ec_es_ef";
  const labels = SCHEME_LABELS[scheme];
  const slip = showSlip ? (product.slip_family ?? {}) : {};
  const slipPerPax = showSlip ? (product.slip_per_pax ?? {}) : {};
  const slipHint = showSlip
    ? " Leave a cell blank to use the slip rate; type to override it."
    : "";

  // Fold cohort tiers sharing a plan into one row (matching the price-tag table),
  // split only when THIS table's values differ across the cohorts. Each table's
  // rows are built lazily inside its own branch so the hidden table costs nothing.
  const familyRows = () =>
    planRows(product.tiers, (t) => [
      dep.family_tags?.[t.key] ?? null,
      product.slip_family?.[t.key] ?? null,
    ]);
  const perPaxRows = () =>
    planRows(product.tiers, (t) => [
      dep.per_pax?.[t.key]?.flat ?? null,
      product.slip_per_pax?.[t.key] ?? null,
    ]);

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
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-[11px] text-muted-foreground">
                  <th className="px-2 py-1.5 text-left font-medium">Plan</th>
                  {FAMILY_ROLES.map((role) => (
                    <th key={role} className="px-2 py-1.5 text-left font-medium">
                      {labels[role]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {familyRows().map(({ keys, rep, cohortLabel }) => (
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
          <p className="text-[11px] text-muted-foreground">
            Incremental flex over Employee-Only, per plan.{slipHint}
          </p>
        </div>
      )}

      {mode === "per_pax" && (
        <div className="mt-2.5 space-y-2">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[11px] text-muted-foreground">
                <th className="px-2 py-1.5 text-left font-medium">Plan</th>
                <th className="px-2 py-1.5 text-left font-medium">Per dependant</th>
              </tr>
            </thead>
            <tbody>
              {perPaxRows().map(({ keys, rep, cohortLabel }) => (
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
          <p className="text-[11px] text-muted-foreground">
            Rate per covered dependant, drawn per dependant on top of the employee
            plan tag.{slipHint}
          </p>
        </div>
      )}
    </div>
  );
}
