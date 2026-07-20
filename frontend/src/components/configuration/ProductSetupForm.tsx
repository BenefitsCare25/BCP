import { useEffect, useMemo, useRef, useState } from "react";
import { CircleCheck, Loader2, Save } from "lucide-react";
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
  useSaveSetup,
} from "@/api/hooks";
import type {
  BasisOfCoverRow,
  Category,
  CategoryGroup,
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
import { CategoryCards } from "./CategoryCards";
import { DependantCards } from "./DependantCard";
import { splitList } from "./setup/SetupPrimitives";
import { ScheduleOfBenefitsSection } from "./setup/ScheduleOfBenefitsSection";

interface Props {
  policyYearId: string;
  template: ProductTemplate;
  draft: ProductSetup | null;
  // Persisted categories for this product (the cards edit these directly).
  group?: CategoryGroup;
  // Opens the slim rule editor for a category.
  onEditRule: (c: Category) => void;
}

// Single-row tab labels per setup section.
const SECTION_LABELS: Record<string, string> = {
  header: "Header & Policy",
  eligibility: "Eligibility",
  basis_of_cover: "Employee Category & Plan Type",
  schedule_of_benefits: "SOB",
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
    return {
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
      arrangements: a.arrangements ?? arrangementDefaults(),
    };
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
  return {
    header: fieldDefaults(tpl.header_fields),
    eligibility: fieldDefaults(tpl.eligibility_fields),
    participation: "",
    cover_description: "",
    plans: fullPlans.map(planStub),
    sob: ensureSob(buildSobFromPlans(fullPlans), codes),
    rate_table: {},
    categories: [blankCategory()],
    arrangements: arrangementDefaults(),
  };
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
}: Props) {
  const [answers, setAnswers] = useState<SetupAnswers>(() =>
    buildAnswers(template, draft),
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  // Which section tab is open. Local: the form no longer remounts on the first
  // save (parent keys on `code`), so this survives naturally.
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const save = useSaveSetup(policyYearId);
  const confirm = useConfirmSetup(policyYearId);
  const { data: suggestions } = useFieldSuggestions(policyYearId, template.code);

  // Serialized snapshot of what's saved on the server, so we only auto-save on
  // tab-switch when the form is actually dirty (avoids materializing a draft for
  // a product the user merely clicked through, and redundant concurrent saves).
  const savedSnapshot = useRef<string>(JSON.stringify(answers));

  // Rebuild the form ONLY when the draft is replaced by a genuinely different
  // persisted draft (e.g. a slip re-upload mints a new setup id) — never on the
  // new→first-save transition (same data we just saved), which would clobber
  // edits made while that save was in flight.
  const builtFromId = useRef<string | null>(draft?.id ?? null);
  useEffect(() => {
    const currentId = draft?.id ?? null;
    if (currentId && builtFromId.current && currentId !== builtFromId.current) {
      const rebuilt = buildAnswers(template, draft);
      setAnswers(rebuilt);
      savedSnapshot.current = JSON.stringify(rebuilt);
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
  const { data: memberCounts } = useMemberCounts(
    policyYearId,
    template.code,
    template.has_dependants,
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
    setAnswers((a) => ({ ...a, eligibility: { ...a.eligibility, [id]: v } }));
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

  // Persist the current answers. `silent` skips the success toast (used by the
  // auto-save on tab switch). The saved snapshot is captured at call time so a
  // later dirty-check compares against exactly what was sent.
  const persist = (opts?: { silent?: boolean }) => {
    const snapshot = JSON.stringify(answers);
    save.mutate(
      { code: template.code, answers, templateVersion: template.version },
      {
        onSuccess: () => {
          savedSnapshot.current = snapshot;
          if (!opts?.silent) toast.success("Draft saved");
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  };
  const onSave = () => persist();
  const onConfirm = () =>
    confirm.mutate(
      { code: template.code, answers, templateVersion: template.version },
      {
        onSuccess: (r) => {
          setConfirmOpen(false);
          // Categories are managed in the cards, not created by confirm — only
          // mention them on the rare first-materialization seed (cats > 0).
          const planMsg = `${r.plans_created + r.plans_updated} plan(s)`;
          const catMsg =
            r.categories_created > 0
              ? `, ${r.categories_created} categor${r.categories_created === 1 ? "y" : "ies"} seeded`
              : "";
          const matchMsg = r.rematched
            ? `, employees re-matched${r.employees_matched != null ? ` (${r.employees_matched} matched)` : ""}`
            : "";
          toast.success(
            `${template.code} configured — ${planMsg}${catMsg}${matchMsg}`,
          );
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  const enabledArrangements = Object.values(answers.arrangements).filter(
    Boolean,
  ).length;

  // The product covers dependants when the catalog/slip says so (has_dependants
  // is authoritative — it unions the catalog flag with slip-parsed dependant
  // signals) or the eligibility field names Spouse/Child (legacy fallback).
  const coveredMembers = splitList(answers.eligibility.member_cover_eligibility ?? "");
  const showDependants =
    template.has_dependants ||
    coveredMembers.some((m) => ["Spouse", "Child", "Dependant"].includes(m));

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
            hint="Which legal entities this product covers, picked from the roster so the spelling always matches. Only employees whose roster Entity is one of these will match this product's categories. Leave empty to cover every entity. The Insured field above stays as the slip's wording — it is never used for matching."
            value={headerEntities}
            onChange={(next) => setHeader("entities", next)}
          />
        </div>
      </div>
    ),
    eligibility: (
      <div className="grid grid-cols-3 gap-3">
        {template.eligibility_fields.map((f) => (
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
              field={f}
              value={answers.eligibility[f.id] ?? ""}
              onChange={(v) => setElig(f.id, v)}
              suggestions={suggestions?.eligibility[f.id] ?? []}
            />
          </div>
        ))}
      </div>
    ),
    // Employee Category & Plan Type = the editable category cards (bound to the
    // persisted categories; edits autosave). Dependant cards follow when the
    // product covers dependants.
    basis_of_cover: (
      <div className="flex flex-col gap-5">
        <CategoryCards
          policyYearId={policyYearId}
          productCode={template.code}
          productId={group?.product_id ?? null}
          hasDependants={memberCounts?.has_dependants ?? template.has_dependants}
          basisModel={template.basis_model}
          rateModel={template.rate_model}
          tiers={template.tiers}
          categories={group?.categories ?? []}
          onEditRule={onEditRule}
        />
        {showDependants && (
          <div className="flex flex-col gap-2 border-t border-border pt-4">
            <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Dependant Category &amp; Plan Type · dependant participation &amp; rate
            </Label>
            <DependantCards
              policyYearId={policyYearId}
              productId={group?.product_id ?? null}
              rateModel={template.rate_model}
              categories={group?.categories ?? []}
            />
          </div>
        )}
      </div>
    ),
    // Schedule of Benefits = what's covered: cover description + cover-term
    // fields + the benefit-line table + additional arrangements.
    // (Rate + premium are edited inline on each Category card, not here.)
    schedule_of_benefits: (
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Cover
          </Label>
          <textarea
            value={answers.cover_description}
            onChange={(e) =>
              setAnswers((a) => ({ ...a, cover_description: e.target.value }))
            }
            rows={2}
            placeholder="What this product covers…"
            className="rounded-md border border-input bg-card px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring/40"
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
            <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">
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
  };

  // Follow the backend's section order, but only tab through sections the form
  // actually renders — ids like `rate_table` are folded into the category cards
  // and have no panel of their own.
  const sections = (
    template.sections?.length ? template.sections : Object.keys(sectionInner)
  ).filter((id) => id in sectionInner);

  // Active tab, falling back to the first section if the parent's stored value
  // isn't valid for this product's section list.
  const activeId =
    activeSection && sections.includes(activeSection) ? activeSection : sections[0];

  // Switching tabs auto-saves the draft so edits are never lost on navigation —
  // but only when the form is actually dirty and no save is already in flight,
  // so merely browsing tabs doesn't create a draft or fire redundant saves.
  const switchSection = (next: string) => {
    if (next === activeId) return;
    const dirty = JSON.stringify(answers) !== savedSnapshot.current;
    if (dirty && !save.isPending) persist({ silent: true });
    setActiveSection(next);
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Single-row section tabs. Switching tabs auto-saves the draft, and each
          tab has its own Save draft — so edits are never lost on navigation. */}
      <div className="config-nav flex items-center gap-1 overflow-x-auto overflow-y-hidden border-b border-border">
        {sections.map((id) => {
          const count =
            id === "basis_of_cover" ? group?.categories.length ?? 0 : 0;
          return (
            <button
              key={id}
              type="button"
              onClick={() => switchSection(id)}
              className={cn(
                "shrink-0 whitespace-nowrap border-b-2 -mb-px px-3 py-2 text-sm font-medium transition-colors",
                activeId === id
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {SECTION_LABELS[id] ?? id}
              {count > 0 && (
                <span className="ml-1.5 text-xs text-muted-foreground">
                  · {count}
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
          <Button variant="outline" onClick={onSave} disabled={save.isPending}>
            {save.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            Save draft
          </Button>
          <Button
            onClick={() => setConfirmOpen(true)}
            disabled={selectedPlans.length === 0}
          >
            Confirm & create
          </Button>
        </div>
      </div>

      <AlertDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Confirm ${template.display_name} setup`}
        confirmLabel="Confirm & create"
        confirmVariant="default"
        loading={confirm.isPending}
        onConfirm={onConfirm}
        description={
          <span>
            This creates the <strong>{template.code}</strong> product and{" "}
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
