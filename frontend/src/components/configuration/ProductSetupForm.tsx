import { useEffect, useMemo, useRef, useState } from "react";
import { CircleCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { cn } from "@/lib/cn";
import {
  useConfirmSetup,
  useFieldSuggestions,
  useMemberCounts,
  usePlans,
} from "@/api/hooks";
import type {
  BasisOfCoverRow,
  Category,
  CategoryGroup,
  EndorsementAnswer,
  PlanAnswer,
  ProductSetup,
  ProductTemplate,
  SetupAnswers,
  SobSchedule,
  TemplateField,
} from "@/types";
import { formatError } from "@/lib/errors";
import { insuredNames } from "@/lib/insured";
import { InsuredPicker } from "./InsuredPicker";
import { buildSobFromPlans, reconcileColumns } from "@/lib/sob";
import { FieldControl } from "./setup/SetupPrimitives";
import { EmployeeCategoryPlanTab } from "./EmployeeCategoryPlanTab";
import { uniqueEmployeeCategoryCount } from "./employeeCategoryGroups";
import { ScheduleOfBenefitsSection } from "./setup/ScheduleOfBenefitsSection";
import { EndorsementsSection } from "./setup/EndorsementsSection";
import {
  hasSelectedDependants,
  inferMemberCoverFromAnswers,
  inferMemberCoverFromCategories,
  normalizeMemberCover,
  prepareEligibilityField,
  selectedMemberCover,
} from "./setup/memberEligibility";

interface Props {
  policyYearId: string;
  template: ProductTemplate;
  draft: ProductSetup | null;
  // Persisted categories for this product (the cards edit these directly).
  group?: CategoryGroup;
  // Opens the slim rule editor for a category.
  onEditRule: (c: Category) => void;
  onConfirmed?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}

// Single-row tab labels per setup section.
const SECTION_LABELS: Record<string, string> = {
  header: "Header & Policy",
  eligibility: "Eligibility",
  basis_of_cover: "Employee Category & Plan Type",
  schedule_of_benefits: "SOB",
  endorsements: "Endorsements",
};

// A legacy draft (pre-`sob`) carries the SOB grid replicated into each plan's
// `benefit_items`. Normalize it enough that buildSobFromPlans can de-dupe it
// into the decoupled column model. Fresh templates feed the same shape.
function normalizeLegacyPlans(plans: PlanAnswer[]): PlanAnswer[] {
  return plans.map((p) => ({
    code: p.code,
    label: p.label,
    selected: p.selected,
    benefit_items: (p.benefit_items ?? []).map((b) => ({
      uid: b.uid ?? crypto.randomUUID(),
      number: b.number,
      name: b.name,
      kind: b.kind ?? "amount",
      value: b.value ?? "",
      default_value: b.default_value ?? b.value ?? "",
      note: b.note ?? null,
      limits: b.limits ?? [],
      properties: b.properties ?? {},
      sub_items: (b.sub_items ?? []).map((s) => ({
        uid: s.uid ?? crypto.randomUUID(),
        key: s.key ?? "",
        name: s.name ?? "",
        value: s.value ?? null,
        note: s.note ?? null,
        limits: s.limits ?? [],
        kind: s.kind,
      })),
    })),
  }));
}

// Strip a plan to the fields the form still needs once the SOB grid lives in
// `answers.sob` (selection + label for Rate/columns; benefit_items dropped).
const planStub = (p: PlanAnswer): PlanAnswer => ({
  code: p.code,
  label: p.label,
  selected: p.selected,
  // The slip's own SOB column header. It is shown once at parse time and never
  // re-derivable (the composite header is fanned out server-side), so dropping
  // it here would let the first autosave permanently replace the broker's
  // wording with a synthetic "Plan 1 +3" on any later rebuild of `sob`.
  source_label: p.source_label ?? null,
});

// Always return a reconciled, non-empty schedule for the given plan codes.
function ensureSob(
  sob: SobSchedule | null,
  planCodes: string[],
): SobSchedule {
  return reconcileColumns(sob ?? { columns: [], items: [] }, planCodes);
}

// With the plan-selection toggles removed from the UI, a plan can only become
// active by being selected here. Guarantee at least one plan is selected when
// any exist, so a draft saved with everything deselected can't permanently
// block confirm or hide the Rate + Schedule of Benefits sections.
function ensurePlanSelected(plans: PlanAnswer[]): PlanAnswer[] {
  if (!plans.length || plans.some((p) => p.selected)) return plans;
  return plans.map((p, index) => ({ ...p, selected: index === 0 }));
}

function withNormalizedMemberCover(answers: SetupAnswers): SetupAnswers {
  const current = answers.eligibility.member_cover_eligibility;
  return {
    ...answers,
    eligibility: {
      ...answers.eligibility,
      member_cover_eligibility: normalizeMemberCover(
        current,
        String(current ?? "").trim() ? [] : inferMemberCoverFromAnswers(answers),
      ),
    },
  };
}

function normalizeEndorsements(items: EndorsementAnswer[] | undefined): EndorsementAnswer[] {
  return (items ?? []).map((item) => ({
    source_cell: item.source_cell ?? null,
    source_row: item.source_row ?? null,
    item_no: item.item_no ?? null,
    year: String(item.year ?? ""),
    label: String(item.label ?? ""),
    name: String(item.name ?? ""),
    content: String(item.content ?? ""),
    comment: item.comment ?? "",
    author: item.author ?? null,
  }));
}

function buildAnswers(tpl: ProductTemplate, draft: ProductSetup | null): SetupAnswers {
  // The template is a structural skeleton — no values. Fresh fields start blank;
  // real values arrive via slip pre-fill, broker input, or dynamic suggestions.
  const fieldDefaults = (fields: TemplateField[]) =>
    Object.fromEntries(fields.map((f) => [f.id, ""]));
  const arrangementDefaults = () =>
    Object.fromEntries(
      tpl.additional_arrangements.map((x) => [x.id, x.default_enabled]),
    );
  const blankCategory = (): BasisOfCoverRow => ({
    id: crypto.randomUUID(),
    insured: [],
    category: "",
    participation: "",
    plan_code: tpl.plans.find((p) => p.default_selected)?.code ?? "",
    tiers: Object.fromEntries(tpl.tiers.map((t) => [t.code, 0])),
    num_employees: 0,
    sum_insured: null,
    basis: "",
  });

  if (draft?.answers?.plans?.length) {
    const a = draft.answers;
    const plans = ensurePlanSelected(a.plans.map(planStub));
    const codes = plans.map((p) => p.code);
    // Prefer the saved decoupled schedule; migrate a pre-`sob` draft by de-
    // duping its replicated per-plan grid into columns.
    const sob = a.sob
      ? ensureSob(a.sob, codes)
      : ensureSob(buildSobFromPlans(normalizeLegacyPlans(a.plans)), codes);
    return withNormalizedMemberCover({
      ...a,
      header: a.header ?? fieldDefaults(tpl.header_fields),
      eligibility: a.eligibility ?? fieldDefaults(tpl.eligibility_fields),
      participation: a.participation ?? "",
      cover_description: a.cover_description ?? "",
      plans,
      sob,
      rate_table: a.rate_table ?? {},
      // A saved draft (or a slip-derived one) may still carry `insured` as a
      // comma-joined string — normalize to tokens on the way in so the picker
      // and the submit payload only ever see the list form.
      categories: (a.categories ?? []).map((c) => ({
        ...c,
        insured: insuredNames(c.insured),
      })),
      endorsements: normalizeEndorsements(a.endorsements),
      arrangements: a.arrangements ?? arrangementDefaults(),
    });
  }

  // Fresh template: build the per-plan grid from the skeleton (every plan shares
  // the template default, so de-duping yields a single "All plans" column), then
  // store plans as stubs + the decoupled schedule.
  const itemUids = tpl.benefit_items.map(() => crypto.randomUUID());
  const subUids = tpl.benefit_items.map((b) =>
    b.sub_items.map(() => crypto.randomUUID()),
  );
  const fullPlans = ensurePlanSelected(
    tpl.plans.map((p) => ({
      code: p.code,
      label: p.label,
      selected: p.default_selected,
      // A hand-authored template may carry suggested default values (carrier-
      // standard schedules like GBT the slip references but doesn't reproduce);
      // they pre-fill every plan and stay the baseline.
      benefit_items: tpl.benefit_items.map((b, i) => ({
        uid: itemUids[i],
        number: b.number,
        name: b.name,
        kind: b.kind,
        value: b.value ?? "",
        default_value: b.value ?? "",
        note: b.note ?? null,
        limits: [],
        properties: {},
        sub_items: b.sub_items.map((s, j) => ({
          uid: subUids[i][j],
          key: s.key,
          name: s.name,
          value: s.value ?? null,
          note: s.note ?? null,
          limits: [],
          kind: s.kind,
        })),
      })),
    })),
  );
  const codes = fullPlans.map((p) => p.code);
  return withNormalizedMemberCover({
    header: fieldDefaults(tpl.header_fields),
    eligibility: fieldDefaults(tpl.eligibility_fields),
    participation: "",
    cover_description: "",
    plans: fullPlans.map(planStub),
    sob: ensureSob(buildSobFromPlans(fullPlans), codes),
    rate_table: {},
    categories: [blankCategory()],
    endorsements: [],
    arrangements: arrangementDefaults(),
  });
}

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

export function ProductSetupForm({
  policyYearId,
  template,
  draft,
  group,
  onEditRule,
  onConfirmed,
  onDirtyChange,
}: Props) {
  const [answers, setAnswers] = useState<SetupAnswers>(() =>
    buildAnswers(template, draft),
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  // Which section tab is open. Local: the form no longer remounts on the first
  // save (parent keys on `code`), so this survives naturally.
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const confirm = useConfirmSetup(policyYearId);
  const { data: suggestions } = useFieldSuggestions(policyYearId, template.code);
  const confirmInFlight = useRef(false);

  // Serialized snapshot of what's saved on the server, so we only auto-save on
  // tab-switch when the form is actually dirty (avoids materializing a draft for
  // a product the user merely clicked through, and redundant concurrent saves).
  const savedSnapshot = useRef<string>(JSON.stringify(answers));
  const answersRef = useRef(answers);
  useEffect(() => {
    answersRef.current = answers;
  }, [answers]);

  // Rebuild when the saved draft changes. A slip re-upload intentionally
  // replaces unconfirmed setup answers, so the mounted form must follow the
  // refreshed server snapshot instead of holding stale local values.
  const builtFromId = useRef<string | null>(draft?.id ?? null);
  useEffect(() => {
    const currentId = draft?.id ?? null;
    const rebuilt = buildAnswers(template, draft);
    const nextSnapshot = JSON.stringify(rebuilt);
    const formIsDirty = JSON.stringify(answersRef.current) !== savedSnapshot.current;
    const idChanged = currentId && builtFromId.current && currentId !== builtFromId.current;
    const serverChanged = currentId && nextSnapshot !== savedSnapshot.current;
    if (serverChanged || (idChanged && !formIsDirty)) {
      setAnswers(rebuilt);
      savedSnapshot.current = nextSnapshot;
    }
    builtFromId.current = currentId;
  }, [draft, template]);
  // Canonical plan names. `Plan.display_name` is the source of truth for a plan's
  // label (the category cards rename it); the draft's `answers.plans[].label` is
  // only a cached copy. Sync it below so Rate / Schedule of Benefits show the
  // current name and a later confirm doesn't write the stale label back.
  const { data: livePlans } = usePlans(policyYearId, group?.product_id ?? undefined);

  const selectedPlans = useMemo(
    () => answers.plans.filter((p) => p.selected),
    [answers.plans],
  );

  // Pull renamed plan labels from the live Plan records into the draft so the
  // rename propagates everywhere (Rate, SOB) and survives the next confirm.
  useEffect(() => {
    const live = livePlans?.items;
    if (!live?.length) return;
    const nameByCode: Record<string, string> = {};
    for (const p of live) {
      if (p.display_name) nameByCode[String(p.code)] = p.display_name;
    }
    setAnswers((a) => {
      let changed = false;
      const plans = a.plans.map((p) => {
        const name = nameByCode[String(p.code)];
        if (name && name !== p.label) {
          changed = true;
          return { ...p, label: name };
        }
        return p;
      });
      return changed ? { ...a, plans } : a;
    });
  }, [livePlans]);

  // Live "members matched" preview. Debounce the category descriptions so we
  // don't fire a request per keystroke; the result auto-prefills headcounts.
  const countCategories = useMemo(
    () =>
      answers.categories.map((c) => ({
        key: c.id,
        description: c.category,
        insured: c.insured.length ? c.insured : null,
      })),
    [answers.categories],
  );
  const debouncedCategories = useDebounced(countCategories, 400);
  const hasDependantsSelected = hasSelectedDependants(
    answers.eligibility.member_cover_eligibility,
  );
  const { data: memberCounts } = useMemberCounts(
    policyYearId,
    template.code,
    hasDependantsSelected,
    debouncedCategories,
  );
  const countsByKey = useMemo(() => {
    const map: Record<string, { employees: number; dependants: number }> = {};
    for (const c of memberCounts?.counts ?? []) {
      map[c.key] = { employees: c.employees, dependants: c.dependants };
    }
    return map;
  }, [memberCounts]);

  // Tiered products carry headcount in per-tier columns that a single match
  // count can't be split across, so they get the informational badges only —
  // no auto-prefill.
  const tieredBasis =
    template.basis_model === "tiered" && template.tiers.length > 0;

  // Untiered products have no manual 'No. of members' field — the matched
  // roster count IS the headcount. Keep num_employees in sync with it so the
  // premium preview and confirm-time materialization use the live count.
  useEffect(() => {
    if (!memberCounts || tieredBasis) return;
    setAnswers((a) => {
      let changed = false;
      const categories = a.categories.map((c) => {
        const mc = countsByKey[c.id];
        if (mc && c.num_employees !== mc.employees) {
          changed = true;
          return { ...c, num_employees: mc.employees };
        }
        return c;
      });
      return changed ? { ...a, categories } : a;
    });
  }, [memberCounts, countsByKey, tieredBasis]);

  const setHeader = (id: string, v: string | string[]) =>
    setAnswers((a) => ({ ...a, header: { ...a.header, [id]: v } }));
  // `entities` is the one header value that is a token list, not free text.
  const headerEntities = insuredNames(answers.header.entities);
  const setElig = (id: string, v: string) =>
    setAnswers((a) => ({
      ...a,
      eligibility: {
        ...a.eligibility,
        [id]:
          id === "member_cover_eligibility" ? normalizeMemberCover(v) : v,
      },
    }));
  // Single entry point for every Schedule-of-Benefits edit. The section is a
  // controlled component over `answers.sob`; it expresses edits via the pure
  // helpers in lib/sob.ts, so there's no per-field handler fan-out here.
  const setSob = (fn: (s: SobSchedule) => SobSchedule) =>
    setAnswers((a) => ({ ...a, sob: fn(a.sob ?? { columns: [], items: [] }) }));
  const toggleArrangement = (id: string) =>
    setAnswers((a) => ({
      ...a,
      arrangements: { ...a.arrangements, [id]: !a.arrangements[id] },
    }));
  const setEndorsements = (endorsements: EndorsementAnswer[]) =>
    setAnswers((a) => ({ ...a, endorsements }));

  const isDirty = JSON.stringify(answers) !== savedSnapshot.current;

  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  const isConfirmed =
    draft?.status === "confirmed" || Boolean(draft?.materialized_product_id);
  const confirmLabel = isConfirmed ? "Update setup" : "Confirm setup";
  const onConfirm = () => {
    if (confirmInFlight.current) return;
    confirmInFlight.current = true;
    confirm.mutate(
      { code: template.code, answers, templateVersion: template.version },
      {
        onSuccess: (r) => {
          setConfirmOpen(false);
          savedSnapshot.current = JSON.stringify(answers);
          onDirtyChange?.(false);
          const planMsg = `${r.plans_created + r.plans_updated} plan(s)`;
          const catMsg =
            r.categories_created > 0
              ? `, ${r.categories_created} categor${r.categories_created === 1 ? "y" : "ies"} seeded`
              : "";
          const matchMsg = r.rematched
            ? `, employees re-matched${r.employees_matched != null ? ` (${r.employees_matched} matched)` : ""}`
            : "";
          toast.success(
            `${template.code} configured - ${planMsg}${catMsg}${matchMsg}`,
          );
          onConfirmed?.();
        },
        onError: (e) => toast.error(formatError(e)),
        onSettled: () => {
          confirmInFlight.current = false;
        },
      },
    );
  };
  const enabledArrangements = Object.values(answers.arrangements).filter(
    Boolean,
  ).length;

  // Spouse/Child ticks control the dependant section and age-limit visibility.
  // Hidden category-level dependant settings are preserved until reselected.
  useEffect(() => {
    setAnswers((a) => {
      const current = a.eligibility.member_cover_eligibility;
      if (String(current ?? "").trim()) return a;
      return {
        ...a,
        eligibility: {
          ...a.eligibility,
          member_cover_eligibility: normalizeMemberCover(
            current,
            inferMemberCoverFromCategories(group?.categories ?? []),
          ),
        },
      };
    });
  }, [group?.categories]);

  const coveredMembers = selectedMemberCover(
    answers.eligibility.member_cover_eligibility,
  );
  const visibleEligibilityFields = template.eligibility_fields.filter((f) => {
    if (f.id === "spouse_age_limit") return coveredMembers.has("Spouse");
    if (f.id === "child_age_limit") return coveredMembers.has("Child");
    return true;
  });

  // Each setup section is one tab PANEL (content only — the tab bar supplies the
  // title). The backend orders `template.sections` per product family (medical,
  // travel, life, accident, statutory); the tabs follow that list, not hardcoded.
  const sectionInner: Record<string, React.ReactNode> = {
    header: (
      <div className="flex flex-col gap-4">
        {/* Two columns, not three: these are slip fields whose values are long
            (entity lists, addresses, period wording), and a third column made
            every one of them truncate. Every field takes one column — the long
            ones render as auto-growing textareas (see `isWideField`) and simply
            get taller, so Insured and Office address sit side by side and both
            stay fully readable. */}
        <div className="grid grid-cols-1 items-start gap-x-4 gap-y-3 md:grid-cols-2">
          {template.header_fields.map((f) => (
            <FieldControl
              key={f.id}
              field={f}
              value={String(answers.header[f.id] ?? "")}
              onChange={(v) => setHeader(f.id, v)}
              suggestions={suggestions?.header[f.id] ?? []}
            />
          ))}
        </div>
        {/* Set apart from the slip fields above because it behaves differently:
            everything above is transcribed wording, this one changes which
            employees match. Insured records the legal names (and is what the
            exported slip reproduces); these picked entities are the gate. */}
        <div className="rounded-md border border-border bg-muted/30 p-3">
          <InsuredPicker
            policyYearId={policyYearId}
            label="Entities covered · used for matching"
            hint="Which legal entities this product covers, picked from the employee listing so the spelling always matches. Only employees whose Entity value is one of these will match this product's employee categories. Leave empty to cover every entity. The Insured field above stays as the slip's wording — it is never used for matching."
            value={headerEntities}
            onChange={(next) => setHeader("entities", next)}
          />
        </div>
      </div>
    ),
    eligibility: (
      <div className="grid grid-cols-3 gap-3">
        {visibleEligibilityFields.map((f) => (
          <div
            key={f.id}
            className={
              f.type === "multichoice" ||
              f.type === "taglist" ||
              f.type === "textarea"
                ? "col-span-2"
                : undefined
            }
          >
            <FieldControl
              field={prepareEligibilityField(f)}
              value={answers.eligibility[f.id] ?? ""}
              onChange={(v) => setElig(f.id, v)}
              suggestions={suggestions?.eligibility[f.id] ?? []}
            />
          </div>
        ))}
      </div>
    ),
    // One row per unique employee category, with its plan assignments nested
    // below. Employee and dependant settings are edited together.
    basis_of_cover: (
      <EmployeeCategoryPlanTab
        policyYearId={policyYearId}
        productCode={template.code}
        productId={group?.product_id ?? null}
        hasDependants={memberCounts?.has_dependants ?? hasDependantsSelected}
        basisModel={template.basis_model}
        rateModel={template.rate_model}
        tiers={template.tiers}
        categories={group?.categories ?? []}
        onEditRule={onEditRule}
      />
    ),
    // Schedule of Benefits = what's covered: cover description + cover-term
    // fields + the benefit-line table + additional arrangements.
    // (Rate + premium are edited inline on each Category card, not here.)
    schedule_of_benefits: (
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
            Cover
          </Label>
          <textarea
            value={answers.cover_description}
            onChange={(e) =>
              setAnswers((a) => ({ ...a, cover_description: e.target.value }))
            }
            rows={2}
            placeholder="What this product covers…"
            className="rounded-md border border-input bg-card px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          />
        </div>

        <ScheduleOfBenefitsSection
          sob={answers.sob ?? { columns: [], items: [] }}
          plans={selectedPlans}
          columnAxis={template.column_axis}
          setSob={setSob}
        />

        {template.additional_arrangements.length > 0 && (
          <div className="flex flex-col gap-2.5">
            <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
              Additional arrangements · {enabledArrangements} enabled
            </Label>
            {template.additional_arrangements.map((a) => (
              <div key={a.id} className="flex items-start gap-3">
                <Switch
                  checked={Boolean(answers.arrangements[a.id])}
                  onCheckedChange={() => toggleArrangement(a.id)}
                />
                <span className="text-sm text-foreground">{a.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    ),
    endorsements: (
      <EndorsementsSection
        endorsements={answers.endorsements ?? []}
        onChange={setEndorsements}
      />
    ),
  };

  // Follow the backend's section order, but only tab through sections the form
  // actually renders — ids like `rate_table` are folded into the category cards
  // and have no panel of their own.
  const templateSections = (
    template.sections?.length ? template.sections : Object.keys(sectionInner)
  ).filter((id) => id in sectionInner);
  const sections = templateSections.includes("endorsements")
    ? templateSections
    : [...templateSections, "endorsements"];

  // Active tab, falling back to the first section if the parent's stored value
  // isn't valid for this product's section list.
  const activeId =
    activeSection && sections.includes(activeSection) ? activeSection : sections[0];

  const switchSection = (next: string) => {
    if (next === activeId) return;
    setActiveSection(next);
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Single-row section tabs. Section navigation stays inside the current
          edit session; leaving the product is guarded by the parent. */}
      <div className="config-nav flex items-center gap-1 overflow-x-auto overflow-y-hidden rounded-lg bg-muted/40 p-1">
        {sections.map((id) => {
          const count =
            id === "basis_of_cover"
              ? uniqueEmployeeCategoryCount(group?.categories ?? [])
              : id === "endorsements"
                ? answers.endorsements?.length ?? 0
                : 0;
          return (
            <button
              key={id}
              type="button"
              onClick={() => switchSection(id)}
              className={cn(
                "shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition-colors",
                activeId === id
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-card/70 hover:text-foreground",
              )}
            >
              {SECTION_LABELS[id] ?? id}
              {count > 0 && (
                <span className="ml-1.5 text-xs text-muted-foreground">
                  · {count}
                  {id === "basis_of_cover"
                    ? ` employee categor${count === 1 ? "y" : "ies"}`
                    : ""}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex flex-col gap-4">{sectionInner[activeId] ?? null}</div>

      <div className="flex items-center justify-between border-t border-border pt-3">
        <span className="text-xs text-muted-foreground">
          {draft?.status === "confirmed" ? (
            <span className="inline-flex items-center gap-1 text-good">
              <CircleCheck className="size-3.5" /> Previously confirmed
            </span>
          ) : null}
        </span>
        <div className="flex items-center gap-2">
          <Button
            onClick={() => setConfirmOpen(true)}
            disabled={selectedPlans.length === 0 || confirm.isPending}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>

      <AlertDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Confirm ${template.display_name} setup`}
        confirmLabel={confirmLabel}
        confirmVariant="default"
        tone="info"
        loading={confirm.isPending}
        onConfirm={onConfirm}
        description={
          <span>
            This {isConfirmed ? "updates" : "creates"} the{" "}
            <strong>{template.code}</strong> product setup and{" "}
            <strong>{selectedPlans.length}</strong> plan
            {selectedPlans.length === 1 ? "" : "s"} with their rates &
            Schedule of Benefits. Eligibility categories are managed in the
            Employee Category &amp; Plan Type cards and are left untouched here.
            Re-confirming refreshes the product and plans only.
          </span>
        }
      />
    </div>
  );
}
