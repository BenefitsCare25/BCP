// Schedule-of-Benefits column model helpers.
//
// The SOB editor decouples "benefit columns" (the genuinely-varying benefit
// levels — usually 1 for life/CI, ≤4 for GHS) from "basis-of-cover plans" (the
// sum-insured tiers, which can be many: 22 for CDL's GCI). The grid is stored
// once as a shared row skeleton + a sparse per-column override, with each column
// mapping to ≥1 plan code.
//
// This module owns: building a SobSchedule from legacy per-plan answers (fresh
// template or migration of a pre-`sob` draft), and resolving a single plan's
// effective schedule back out. The backend mirror lives in
// app/services/sob_columns.py — keep the two in sync.

import type {
  BenefitItemAnswer,
  PlanAnswer,
  SobColumn,
  SobItemAnswer,
  SobSchedule,
  SobSubItemAnswer,
} from "@/types";

// Sentinel override value marking a per-column exclusion ("Not included in SOB"
// on a single plan). Stored as a normal value so every read path renders it
// without a special flag; the editor's "Not covered" toggle writes/clears it.
export const NOT_COVERED = "Not covered";

const uid = () => crypto.randomUUID();

// Per-plan value vector used to group plans into columns: only the genuinely
// per-plan-varying fields (item value, sub-item values, copay properties).
// Structural fields (number/name/note/limits) are shared, so they're excluded.
function planVector(items: BenefitItemAnswer[]): string {
  return JSON.stringify(
    items.map((b) => [
      b.value ?? "",
      (b.sub_items ?? []).map((s) => s.value ?? ""),
      b.kind === "copay" ? sortedEntries(b.properties) : null,
    ]),
  );
}

function sortedEntries(props: Record<string, string> | undefined): [string, string][] {
  return Object.entries(props ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
}

// Label a column from the plans it covers: the plan label when it owns exactly
// one, "All plans" when a single column covers everything, else "<first> +N".
function columnLabel(plans: PlanAnswer[], onlyColumn: boolean): string {
  if (plans.length === 1) return plans[0].label || plans[0].code;
  if (onlyColumn) return "All plans";
  const first = plans[0]?.label || plans[0]?.code || "";
  return plans.length > 1 ? `${first} +${plans.length - 1}` : first;
}

/**
 * Build a SobSchedule by de-duplicating per-plan benefit_items: plans whose
 * value vectors are identical collapse to one column. Used both for a fresh
 * template (all plans share the template default → one column) and to migrate a
 * legacy draft. Returns null when no plan carries benefit_items.
 */
export function buildSobFromPlans(plans: PlanAnswer[]): SobSchedule | null {
  const withItems = plans.filter((p) => Array.isArray(p.benefit_items));
  if (!withItems.length) return null;

  // Group plans by value vector, preserving first-seen order.
  const groups = new Map<string, PlanAnswer[]>();
  for (const p of withItems) {
    const key = planVector(p.benefit_items ?? []);
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
  }

  const onlyColumn = groups.size === 1;
  const columns: SobColumn[] = [];
  const repItems: BenefitItemAnswer[][] = []; // each column's representative items
  for (const members of groups.values()) {
    columns.push({
      id: uid(),
      label: columnLabel(members, onlyColumn),
      plan_codes: members.map((p) => p.code),
    });
    repItems.push(members[0].benefit_items ?? []);
  }

  // Canonical row count/structure comes from the first column's representative.
  const canonical = repItems[0] ?? [];
  const items: SobItemAnswer[] = canonical.map((base, idx) => {
    const baseValue = base.value ?? "";
    const overrides: Record<string, string | null> = {};
    const columnProps: Record<string, Record<string, string>> = {};
    const isCopay = base.kind === "copay";
    columns.forEach((col, ci) => {
      const cell = repItems[ci]?.[idx];
      const v = cell?.value ?? "";
      if (ci > 0 && v !== baseValue) overrides[col.id] = v;
      if (isCopay) columnProps[col.id] = { ...(cell?.properties ?? {}) };
    });

    const subItems: SobSubItemAnswer[] = (base.sub_items ?? []).map((sub, si) => {
      const subBase = sub.value ?? null;
      const subOverrides: Record<string, string | null> = {};
      columns.forEach((col, ci) => {
        if (ci === 0) return;
        const sv = repItems[ci]?.[idx]?.sub_items?.[si]?.value ?? null;
        if (sv !== subBase) subOverrides[col.id] = sv;
      });
      return {
        uid: sub.uid ?? uid(),
        key: sub.key,
        name: sub.name,
        note: sub.note ?? null,
        limits: sub.limits ?? [],
        kind: sub.kind,
        base_value: subBase,
        overrides: subOverrides,
      };
    });

    return {
      uid: base.uid ?? uid(),
      number: base.number,
      name: base.name,
      kind: base.kind ?? "amount",
      note: base.note ?? null,
      limits: base.limits ?? [],
      base_value: baseValue,
      overrides,
      // Axis (dental) values are plan-independent → shared on the row.
      properties: isCopay ? {} : { ...(base.properties ?? {}) },
      column_properties: isCopay ? columnProps : undefined,
      sub_items: subItems,
    };
  });

  return { columns, items };
}

/** Effective value of an item cell for a column (override falls back to base). */
export function cellValue(item: SobItemAnswer, columnId: string): string {
  const ov = item.overrides[columnId];
  return (ov ?? item.base_value ?? "") as string;
}

/** Effective value of a sub-item cell for a column. */
export function subCellValue(sub: SobSubItemAnswer, columnId: string): string {
  const ov = sub.overrides[columnId];
  return (ov ?? sub.base_value ?? "") as string;
}

/** True when a column's cell deviates from the row's base value. */
export function isOverridden(item: SobItemAnswer, columnId: string): boolean {
  const ov = item.overrides[columnId];
  return ov !== undefined && ov !== (item.base_value ?? "");
}

/**
 * Ensure the schedule has ≥1 column and every selected plan code is assigned to
 * exactly one column. Orphan codes (basis added after the SOB was built) fall
 * into the first column; codes no longer present are dropped. Idempotent.
 */
export function reconcileColumns(
  sob: SobSchedule,
  planCodes: string[],
): SobSchedule {
  let columns = sob.columns.length
    ? sob.columns
    : [{ id: uid(), label: "All plans", plan_codes: [] }];
  const known = new Set(planCodes);
  const assigned = new Set<string>();
  columns = columns.map((c) => {
    const codes = c.plan_codes.filter((code) => {
      if (!known.has(code) || assigned.has(code)) return false;
      assigned.add(code);
      return true;
    });
    return { ...c, plan_codes: codes };
  });
  const orphans = planCodes.filter((code) => !assigned.has(code));
  if (orphans.length) {
    columns = columns.map((c, i) =>
      i === 0 ? { ...c, plan_codes: [...c.plan_codes, ...orphans] } : c,
    );
  }
  return { ...sob, columns };
}

// ── Pure edit helpers (the editor is controlled via a single setSob) ─────────

const mapItem = (
  sob: SobSchedule,
  idx: number,
  fn: (it: SobItemAnswer) => SobItemAnswer,
): SobSchedule => ({
  ...sob,
  items: sob.items.map((it, i) => (i === idx ? fn(it) : it)),
});

export function setItemField(
  sob: SobSchedule,
  idx: number,
  patch: Partial<SobItemAnswer>,
): SobSchedule {
  return mapItem(sob, idx, (it) => ({ ...it, ...patch }));
}

/** Write a cell: the first column edits base_value; others write an override. */
export function setCell(
  sob: SobSchedule,
  idx: number,
  columnIndex: number,
  value: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    if (columnIndex === 0) return { ...it, base_value: value };
    const col = sob.columns[columnIndex];
    if (!col) return it;
    return { ...it, overrides: { ...it.overrides, [col.id]: value } };
  });
}

/** Revert a column's cell back to the row's base value. */
export function clearCell(
  sob: SobSchedule,
  idx: number,
  columnId: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    const { [columnId]: _drop, ...rest } = it.overrides;
    return { ...it, overrides: rest };
  });
}

export function addItem(sob: SobSchedule): SobSchedule {
  return {
    ...sob,
    items: [
      ...sob.items,
      {
        uid: uid(),
        number: "",
        name: "",
        kind: "amount",
        note: null,
        limits: [],
        base_value: "",
        overrides: {},
        properties: {},
        sub_items: [],
      },
    ],
  };
}

export function removeItem(sob: SobSchedule, idx: number): SobSchedule {
  return { ...sob, items: sob.items.filter((_, i) => i !== idx) };
}

export function setSubField(
  sob: SobSchedule,
  idx: number,
  subIdx: number,
  patch: Partial<SobSubItemAnswer>,
): SobSchedule {
  return mapItem(sob, idx, (it) => ({
    ...it,
    sub_items: it.sub_items.map((s, j) => (j === subIdx ? { ...s, ...patch } : s)),
  }));
}

export function setSubCell(
  sob: SobSchedule,
  idx: number,
  subIdx: number,
  columnIndex: number,
  value: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => ({
    ...it,
    sub_items: it.sub_items.map((s, j) => {
      if (j !== subIdx) return s;
      if (columnIndex === 0) return { ...s, base_value: value };
      const col = sob.columns[columnIndex];
      if (!col) return s;
      return { ...s, overrides: { ...s.overrides, [col.id]: value } };
    }),
  }));
}

export function addSub(sob: SobSchedule, idx: number): SobSchedule {
  return mapItem(sob, idx, (it) => ({
    ...it,
    sub_items: [
      ...it.sub_items,
      { uid: uid(), key: "", name: "", base_value: null, overrides: {} },
    ],
  }));
}

export function removeSub(
  sob: SobSchedule,
  idx: number,
  subIdx: number,
): SobSchedule {
  return mapItem(sob, idx, (it) => ({
    ...it,
    sub_items: it.sub_items.filter((_, j) => j !== subIdx),
  }));
}

export function setProperty(
  sob: SobSchedule,
  idx: number,
  key: string,
  value: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => ({
    ...it,
    properties: { ...it.properties, [key]: value },
  }));
}

export function setColumnProperty(
  sob: SobSchedule,
  idx: number,
  columnId: string,
  key: string,
  value: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => ({
    ...it,
    column_properties: {
      ...(it.column_properties ?? {}),
      [columnId]: { ...(it.column_properties?.[columnId] ?? {}), [key]: value },
    },
  }));
}

// ── Copay qualifier fields ───────────────────────────────────────────────────
// The standard per-visit / co-payment / per-policy-year trio can be extended
// per item (A&E splits per-visit by Restructured vs Private hospital). Extra
// fields live as additional keys in column_properties; these helpers keep the
// key set identical across columns so the grid stays rectangular.

export const COPAY_FIELDS: { key: string; label: string }[] = [
  { key: "per_visit", label: "Per visit" },
  { key: "co_payment", label: "Co-payment" },
  { key: "per_policy_year", label: "Per policy year" },
];

export const COPAY_FIELD_PRESETS: { key: string; label: string }[] = [
  { key: "per_visit_restructured", label: "Per visit — Restructured Hospital" },
  { key: "per_visit_private", label: "Per visit — Private Hospital" },
  { key: "co_payment_restructured", label: "Co-payment — Restructured Hospital" },
  { key: "co_payment_private", label: "Co-payment — Private Hospital" },
  { key: "per_disability", label: "Per disability" },
];

/** Human label for a copay property key ("per_visit_private" → "Per visit — Private"). */
export function copayFieldLabel(key: string): string {
  const known = [...COPAY_FIELDS, ...COPAY_FIELD_PRESETS].find((f) => f.key === key);
  if (known) return known.label;
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Ordered field list for a copay item: the standard trio, then any extra keys
 * present on any column (first-seen order) — so parser-extracted variants and
 * manually added qualifiers render for every column.
 */
export function copayFields(item: SobItemAnswer): { key: string; label: string }[] {
  const seen = new Set(COPAY_FIELDS.map((f) => f.key));
  const extras: { key: string; label: string }[] = [];
  for (const props of Object.values(item.column_properties ?? {})) {
    for (const key of Object.keys(props)) {
      if (!seen.has(key)) {
        seen.add(key);
        extras.push({ key, label: copayFieldLabel(key) });
      }
    }
  }
  return [...COPAY_FIELDS, ...extras];
}

/** Add a qualifier field to a copay item on every column (blank values). */
export function addCopayField(
  sob: SobSchedule,
  idx: number,
  key: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    const colProps = { ...(it.column_properties ?? {}) };
    for (const col of sob.columns) {
      const props = { ...(colProps[col.id] ?? {}) };
      if (!(key in props)) props[key] = "";
      colProps[col.id] = props;
    }
    return { ...it, column_properties: colProps };
  });
}

/** Remove a qualifier field from a copay item across all columns. */
export function removeCopayField(
  sob: SobSchedule,
  idx: number,
  key: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    const colProps: Record<string, Record<string, string>> = {};
    for (const [colId, props] of Object.entries(it.column_properties ?? {})) {
      const { [key]: _drop, ...rest } = props;
      colProps[colId] = rest;
    }
    return { ...it, column_properties: colProps };
  });
}

export function addColumn(sob: SobSchedule): SobSchedule {
  const n = sob.columns.length + 1;
  return {
    ...sob,
    columns: [...sob.columns, { id: uid(), label: `Plan ${n}`, plan_codes: [] }],
  };
}

export function removeColumn(sob: SobSchedule, columnId: string): SobSchedule {
  if (sob.columns.length <= 1) return sob; // keep at least one
  const dropped = sob.columns.find((c) => c.id === columnId);
  const columns = sob.columns.filter((c) => c.id !== columnId);
  // Re-home the dropped column's plans onto the first remaining column.
  if (dropped?.plan_codes.length && columns[0]) {
    columns[0] = {
      ...columns[0],
      plan_codes: [...columns[0].plan_codes, ...dropped.plan_codes],
    };
  }
  // Strip overrides/column_properties that referenced the removed column.
  const items = sob.items.map((it) => {
    const { [columnId]: _o, ...overrides } = it.overrides;
    const colProps = { ...(it.column_properties ?? {}) };
    delete colProps[columnId];
    return {
      ...it,
      overrides,
      column_properties: it.column_properties ? colProps : undefined,
      sub_items: it.sub_items.map((s) => {
        const { [columnId]: _s, ...subOv } = s.overrides;
        return { ...s, overrides: subOv };
      }),
    };
  });
  return { columns, items };
}

export function setColumnLabel(
  sob: SobSchedule,
  columnId: string,
  label: string,
): SobSchedule {
  return {
    ...sob,
    columns: sob.columns.map((c) => (c.id === columnId ? { ...c, label } : c)),
  };
}

/** Move a plan code to a column (removing it from whichever column held it). */
export function assignPlan(
  sob: SobSchedule,
  planCode: string,
  columnId: string,
): SobSchedule {
  return {
    ...sob,
    columns: sob.columns.map((c) => {
      const without = c.plan_codes.filter((code) => code !== planCode);
      return c.id === columnId
        ? { ...c, plan_codes: [...without, planCode] }
        : { ...c, plan_codes: without };
    }),
  };
}
