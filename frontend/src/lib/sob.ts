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
  BenefitKind,
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
// per-plan-varying fields (item value, sub-item values, properties).
// Structural fields (number/name/note/limits) are shared, so they're excluded.
//
// `properties` participates for EVERY kind, not just copay: the dental axis
// (Panel/Non-Panel) lives there too, so two plans that differ only in their
// panel limits must stay separate columns instead of collapsing into one.
function planVector(items: BenefitItemAnswer[]): string {
  return JSON.stringify(
    items.map((b) => [
      b.value ?? "",
      (b.sub_items ?? []).map((s) => s.value ?? ""),
      varyingProps(b),
    ]),
  );
}

/**
 * Identity of a benefit row across columns: its NAME.
 *
 * Deliberately NOT the number. placement_slip_sob auto-assigns
 * number = str(len(items)+1) for name-first products (GBT/OSI/GD), so a plan
 * that omits an early row has every later row renumbered. Keying on the number
 * would make those rows look distinct, and unionRows would emit each benefit
 * twice - exactly the missing-row case the union exists to handle.
 *
 * The name is already this system's benefit identity: claims.py and
 * utilization.py both join on it. Names are unique within a plan; the number
 * only orders and displays. Internal whitespace is collapsed because a slip
 * cell wrapped across two lines arrives with a double space and is the same
 * benefit. A nameless row falls back to its number so blank rows do not all
 * collide into one.
 *
 * Mirror of sob_columns.benefit_row_key - keep the two identical. They drifted
 * once (one collapsed whitespace, the other did not), which made "Room  &
 * Board" one row to one consumer and two to another.
 */
function normalizeKey(name: string | undefined, fallback: string | undefined): string {
  const key = (name ?? "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
  if (key) return key;
  return `#${(fallback ?? "").split(/\s+/).filter(Boolean).join(" ").toLowerCase()}`;
}

function rowKey(item: { number?: string; name?: string }): string {
  return normalizeKey(item.name, item.number);
}

function subKey(sub: { key?: string; name?: string }): string {
  return normalizeKey(sub.name, sub.key);
}

/**
 * Every distinct row across all columns, in first-seen order. Column 0 defines
 * the order it knows about; rows only a later column carries are inserted after
 * the row they followed there rather than all being appended at the end.
 */
function unionRows(repItems: BenefitItemAnswer[][]): BenefitItemAnswer[] {
  const out: BenefitItemAnswer[] = [];
  const seen = new Set<string>();
  for (const items_ of repItems) {
    let anchor = out.length;
    for (const row of items_) {
      const key = rowKey(row);
      if (seen.has(key)) {
        const at = out.findIndex((r) => rowKey(r) === key);
        if (at >= 0) anchor = at + 1;
        continue;
      }
      seen.add(key);
      out.splice(anchor, 0, row);
      anchor += 1;
    }
  }
  return out;
}

function sortedEntries(props: Record<string, string> | undefined): [string, string][] {
  return Object.entries(props ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
}

// Qualifier keys the slip parser mirrors into `limits` as well as `properties`
// (placement_slip_sob._SOB_PROPERTY_PATTERNS). `limits` is SHARED across
// columns, so these can never be represented per-column - including them in the
// grouping vector would split columns on a difference the model cannot store.
// Mirrored in sob_columns._SHARED_PROPERTY_KEYS.
const SHARED_PROPERTY_KEYS = new Set([
  "maximum_days",
  "qualification_period",
  "co_insurance",
  "surgical_schedule",
]);

/**
 * The per-plan-varying subset of a row's `properties`, serialised for equality.
 * Copay fields and the dental Panel/Non-Panel axis vary by plan; the parser's
 * shared qualifier keys do not.
 *
 * Takes the ROW (not the bag) so an absent `properties` key and an empty one
 * both normalise to the same result - otherwise they compare unequal and force
 * a needless per-column split.
 */
function varyingProps(
  item: { properties?: Record<string, string> } | undefined,
): string {
  return JSON.stringify(
    sortedEntries(item?.properties).filter(([k]) => !SHARED_PROPERTY_KEYS.has(k)),
  );
}

// Label a column from the plans it covers. Mirror of
// `sob_columns._column_label` — keep the two in sync.
//
// A slip header can name several plan codes at once ("PLAN 1/U01/U04/U06"),
// which are fanned out into one plan each; a column normally regroups exactly
// those, so it prints the header verbatim rather than the lossy "Plan 1 +3".
// Every member must carry a header (else the label would under-state what the
// column covers), and value-identical headers can merge into one column, so
// join the distinct ones. Without headers, fall back to the old summary.
function columnLabel(plans: PlanAnswer[], onlyColumn: boolean): string {
  const labels: string[] = [];
  for (const p of plans) {
    const src = (p.source_label ?? "").trim();
    if (!src) {
      labels.length = 0;
      break;
    }
    if (!labels.includes(src)) labels.push(src);
  }
  if (labels.length) return labels.join(" + ");
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

  // Row skeleton = the UNION of rows across every column (mirrors
  // sob_columns._union_rows). Taking only the first column's rows silently
  // discarded lines that a richer later plan introduced.
  const canonical = unionRows(repItems);
  // Rows match across columns by IDENTITY, not position — a column missing an
  // early row must not shift every later row up by one.
  const byKey = repItems.map(
    (items_) => new Map(items_.map((r) => [rowKey(r), r])),
  );

  const items: SobItemAnswer[] = canonical.map((base) => {
    const key = rowKey(base);
    // `properties` is PER-COLUMN whenever any plan differs on it — copay fields
    // and the dental Panel/Non-Panel axis both live there, and both can
    // legitimately vary by plan. Rows where every plan agrees keep the shared
    // bag so the common case stays compact.
    const firstProps = varyingProps(byKey[0]?.get(key));
    const perColumnProps = columns.some(
      (_, ci) => ci > 0 && varyingProps(byKey[ci]?.get(key)) !== firstProps,
    );
    // A column that genuinely lacks the row is EXCLUDED from it — which is what
    // NOT_COVERED means — rather than quietly inheriting the base. That
    // includes column 0, whose cell IS the base value.
    //
    // **This deliberately DIVERGES from the backend's `sob_from_plan_items`,
    // which treats a null cell as "not stated → inherit".** Do not "resync" it.
    // That rule is only safe where the parser can still tell a blank cell from
    // an explicit "NA" (it carries `not_applicable` for exactly this). Here the
    // input is a LEGACY per-plan draft, in which both were already flattened to
    // null — so inheriting would hand a plan a benefit its slip said "NA" to,
    // and overstating cover is the one error worse than blanking a row.
    const cells = columns.map((_, ci) => {
      const cell = byKey[ci]?.get(key);
      return cell ? (cell.value ?? "") : NOT_COVERED;
    });
    const baseValue = cells[0] ?? "";
    const overrides: Record<string, string | null> = {};
    const columnProps: Record<string, Record<string, string>> = {};
    columns.forEach((col, ci) => {
      if (ci > 0 && cells[ci] !== baseValue) overrides[col.id] = cells[ci];
      if (perColumnProps)
        columnProps[col.id] = { ...(byKey[ci]?.get(key)?.properties ?? {}) };
    });

    const subItems: SobSubItemAnswer[] = (base.sub_items ?? []).map((sub) => {
      const sKey = subKey(sub);
      const subBase = sub.value ?? null;
      const subOverrides: Record<string, string | null> = {};
      columns.forEach((col, ci) => {
        if (ci === 0) return;
        const sv =
          byKey[ci]?.get(key)?.sub_items?.find((s) => subKey(s) === sKey)?.value ??
          null;
        // Only a genuine difference is an override; null means "inherit", so
        // never persist it as one (the backend would read it as a real blank).
        if (sv !== null && sv !== subBase) subOverrides[col.id] = sv;
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
      properties: perColumnProps ? {} : { ...(base.properties ?? {}) },
      column_properties: perColumnProps ? columnProps : undefined,
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

/** Human-facing row label. Dash-prefixed outpatient keys are storage identity,
 * not negative numbers, so brokers see their actual meaning instead. */
export function benefitNumberLabel(
  item: Pick<SobItemAnswer, "kind" | "number">,
): string {
  const current = (item.number ?? "").trim();
  const group = item.kind === "copay" ? current.match(/^-?(\d+)$/) : null;
  return group ? `Group ${group[1]}` : current;
}

/** Convert an edited human group label back to the parser-safe dash identity. */
export function storedBenefitNumber(label: string, kind: BenefitKind): string {
  if (kind === "copay") {
    const group = label.match(/^\s*(?:group|g)?\s*(\d+)\s*$/i);
    if (group) return `-${group[1]}`;
  }
  return label;
}

/** Effective value of a sub-item cell for a column. */
export function subCellValue(sub: SobSubItemAnswer, columnId: string): string {
  const ov = sub.overrides[columnId];
  return (ov ?? sub.base_value ?? "") as string;
}

/**
 * True when a column's cell deviates from the row's base value.
 *
 * Must agree with `cellValue`: a `null` override means "inherit", so it is NOT
 * an override — reporting it as one put a "reset" affordance on cells that were
 * already showing the inherited value.
 */
export function isOverridden(item: SobItemAnswer, columnId: string): boolean {
  const ov = item.overrides[columnId];
  return ov != null && ov !== (item.base_value ?? "");
}

/** Sub-item counterpart of `isOverridden`. */
export function isSubOverridden(sub: SobSubItemAnswer, columnId: string): boolean {
  const ov = sub.overrides[columnId];
  return ov != null && ov !== (sub.base_value ?? "");
}

/**
 * Effective dental-axis value ("Panel" / "Non-Panel") for one column.
 *
 * Axis values are PER-COLUMN (`column_properties`), so two dental plans can
 * carry different panel limits. Falls back to the row-level `properties` bag,
 * which is where drafts authored before that change stored them — and which
 * `resolve_plan_schedule` still merges under the per-column values, so both
 * shapes project correctly.
 */
export function axisValue(
  item: SobItemAnswer,
  columnId: string,
  label: string,
): string {
  return (
    item.column_properties?.[columnId]?.[label] ??
    item.properties?.[label] ??
    ""
  );
}

/** Columns no basis-of-cover plan maps to — their values reach nobody. */
export function unassignedColumns(sob: SobSchedule): SobColumn[] {
  return sob.columns.filter((c) => c.plan_codes.length === 0);
}

/**
 * Ensure the schedule has ≥1 column and no plan code is assigned to more than
 * one column; codes no longer present are dropped. Idempotent.
 *
 * An orphan code (a basis added after the SOB was built) joins the column ONLY
 * when there is exactly one — then it unambiguously covers the whole product.
 * With several benefit levels it stays UNASSIGNED, mirroring
 * `sob_columns._column_id_for_plan`: dropping orphans into `columns[0]` gave
 * them the first — usually richest — schedule, and because that guess was
 * written back into `plan_codes` and autosaved, it silently became the truth
 * the server then trusted. Unassigned surfaces as "Not assigned" in
 * ColumnManager for the broker to resolve.
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
  if (orphans.length && columns.length === 1) {
    columns = [{ ...columns[0], plan_codes: [...columns[0].plan_codes, ...orphans] }];
  }
  const planClaimLimits = Object.fromEntries(
    Object.entries(sob.plan_claim_limits ?? {}).filter(([code]) => known.has(code)),
  );
  return {
    ...sob,
    columns,
    plan_claim_limits: sob.plan_claim_limits ? planClaimLimits : undefined,
  };
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

const invalidateClaimLimits = (
  item: SobItemAnswer,
  columnIds: string[],
): SobItemAnswer => {
  if (!item.claim_limits || columnIds.length === 0) return item;
  const affected = new Set(columnIds);
  let changed = false;
  const claimLimits = Object.fromEntries(
    Object.entries(item.claim_limits).map(([columnId, setting]) => {
      if (!affected.has(columnId) || setting.status === "needs_review") {
        return [columnId, setting];
      }
      changed = true;
      return [columnId, { ...setting, status: "needs_review" as const }];
    }),
  );
  return changed ? { ...item, claim_limits: claimLimits } : item;
};

export function setItemField(
  sob: SobSchedule,
  idx: number,
  patch: Partial<SobItemAnswer>,
): SobSchedule {
  if (patch.kind !== undefined) {
    const { kind, ...rest } = patch;
    const withKind = setItemKind(sob, idx, kind as BenefitKind);
    return Object.keys(rest).length ? mapItem(withKind, idx, (it) => ({ ...it, ...rest })) : withKind;
  }
  return mapItem(sob, idx, (it) => ({ ...it, ...patch }));
}

/**
 * Change a row's value type, MIGRATING the data that type owns.
 *
 * `copay` stores its fields per column (`column_properties`); every other kind
 * stores plan-independent values on `properties`. Switching between them used
 * to leave the old bag in place, unreachable and invisible — the broker saw the
 * fields empty and their values silently stopped being exported.
 *
 * Nothing is deleted: values move to where the new kind reads them, and
 * `sub_items` are left intact (list/scale reuse them as rows, so a round-trip
 * back to a valued kind restores the original grid).
 */
export function setItemKind(
  sob: SobSchedule,
  idx: number,
  kind: BenefitKind,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    const was = it.kind ?? "amount";
    if (was === kind) return { ...it, kind };

    if (kind === "copay") {
      // Fan the shared bag out to every column so each starts from what the
      // row already said, rather than blank.
      const shared = it.properties ?? {};
      const columnProps: Record<string, Record<string, string>> = {};
      for (const col of sob.columns) {
        columnProps[col.id] = { ...shared, ...(it.column_properties?.[col.id] ?? {}) };
      }
      return { ...it, kind, properties: {}, column_properties: columnProps };
    }

    if (was === "copay") {
      // Collapse back to the shared bag, first column wins (it is the base).
      const first = sob.columns[0]?.id;
      const merged: Record<string, string> = { ...(it.properties ?? {}) };
      for (const col of [...sob.columns].reverse()) {
        Object.assign(merged, it.column_properties?.[col.id] ?? {});
      }
      if (first) Object.assign(merged, it.column_properties?.[first] ?? {});
      return { ...it, kind, properties: merged, column_properties: undefined };
    }

    return { ...it, kind };
  });
}

/** Write a cell: the first column edits base_value; others write an override. */
export function setCell(
  sob: SobSchedule,
  idx: number,
  columnIndex: number,
  value: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    if (columnIndex === 0) {
      if (value === (it.base_value ?? "")) return it;
      const inheritedColumnIds = sob.columns
        .filter((col, index) => index === 0 || it.overrides[col.id] == null)
        .map((col) => col.id);
      return invalidateClaimLimits(
        { ...it, base_value: value },
        inheritedColumnIds,
      );
    }
    const col = sob.columns[columnIndex];
    if (!col) return it;
    const previous = it.overrides[col.id] ?? it.base_value ?? "";
    if (value === previous) return it;
    // Writing the base value back into a column clears the override rather
    // than storing a redundant copy that would drift if the base changes.
    if (value === (it.base_value ?? "")) {
      const { [col.id]: _drop, ...rest } = it.overrides;
      return invalidateClaimLimits({ ...it, overrides: rest }, [col.id]);
    }
    return invalidateClaimLimits(
      { ...it, overrides: { ...it.overrides, [col.id]: value } },
      [col.id],
    );
  });
}

/**
 * Split a clipboard payload into one value per row.
 *
 * SOB data originates in spreadsheets, so a broker correcting a column pastes a
 * vertical selection. Excel/Sheets serialise that as newline-separated rows;
 * when the selection is more than one column wide each row is tab-separated, so
 * take the FIRST cell of each — pasting a two-column selection into one column
 * should fill that column, not concatenate.
 */
export function parsePastedColumn(text: string): string[] {
  const rows = text.replace(/\r\n?/g, "\n").split("\n");
  // A trailing newline is what Excel appends after the last cell; it is not an
  // extra (blank) row and must not wipe the value below the paste.
  if (rows.length > 1 && rows[rows.length - 1] === "") rows.pop();
  return rows.map((r) => (r.split("\t")[0] ?? "").trim());
}

/**
 * Fill a column downwards from `startIdx` with `values`, one per benefit row.
 *
 * Stops at the end of the schedule — pasting more rows than exist never creates
 * benefit lines, because a stray paste must not invent coverage.
 */
export function pasteColumn(
  sob: SobSchedule,
  startIdx: number,
  columnIndex: number,
  values: string[],
): SobSchedule {
  let next = sob;
  values.forEach((value, offset) => {
    const idx = startIdx + offset;
    if (idx >= sob.items.length) return;
    next = setCell(next, idx, columnIndex, value);
  });
  return next;
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

/** Move a benefit row one position up (-1) or down (+1). No-op at the ends. */
export function moveItem(
  sob: SobSchedule,
  idx: number,
  delta: number,
): SobSchedule {
  const to = idx + delta;
  if (idx < 0 || idx >= sob.items.length || to < 0 || to >= sob.items.length) {
    return sob;
  }
  const items = [...sob.items];
  const [row] = items.splice(idx, 1);
  items.splice(to, 0, row);
  return { ...sob, items };
}

/**
 * Rewrite every row's displayed `number` to its position (1, 2, 3 …).
 *
 * `number` is free text while ORDER is the array index, so the two drift apart
 * as soon as a row is inserted or moved — "Add benefit line" appends with a
 * blank number, and reordering leaves the old numbering behind. This is the
 * one-click reconciliation.
 *
 * Letter enumerators (GCGP's "A".."G") are left alone. Outpatient copay
 * groups participate in the numeric sequence but retain a dash-prefixed
 * storage identity so a group label cannot collide with a genuine source row.
 */
export function renumberItems(sob: SobSchedule): SobSchedule {
  let n = 0;
  return {
    ...sob,
    items: sob.items.map((it) => {
      const current = (it.number ?? "").trim();
      const isGroup =
        it.kind === "copay" && (current === "" || /^-?\d+$/.test(current));
      const isSequential = current === "" || /^\d+$/.test(current) || isGroup;
      if (!isSequential) return it;
      n += 1;
      return { ...it, number: isGroup ? `-${n}` : String(n) };
    }),
  };
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
      // Writing the base value back into a column clears the override rather
      // than storing a redundant copy that would drift if the base changes.
      if (value === (s.base_value ?? "")) {
        const { [col.id]: _drop, ...rest } = s.overrides;
        return { ...s, overrides: rest };
      }
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

export function setColumnProperty(
  sob: SobSchedule,
  idx: number,
  columnId: string,
  key: string,
  value: string,
): SobSchedule {
  return mapItem(sob, idx, (it) => {
    const previous =
      it.column_properties?.[columnId]?.[key] ?? it.properties?.[key] ?? "";
    if (previous === value) return it;
    const next = {
      ...it,
      column_properties: {
        ...(it.column_properties ?? {}),
        [columnId]: {
          ...(it.column_properties?.[columnId] ?? {}),
          [key]: value,
        },
      },
    };
    return key === "per_policy_year"
      ? invalidateClaimLimits(next, [columnId])
      : next;
  });
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
export function propertyLabel(key: string): string {
  const known = [...COPAY_FIELDS, ...COPAY_FIELD_PRESETS].find((f) => f.key === key);
  if (known) return known.label;
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Ordered field list for a copay item: the standard trio, then any extra keys
 * present in shared properties or a column override. Known qualifiers use the
 * preset order so parser-extracted and manually added fields render consistently.
 */
export function copayFields(item: SobItemAnswer): { key: string; label: string }[] {
  const seen = new Set(COPAY_FIELDS.map((f) => f.key));
  const extraKeys: string[] = [];
  for (const props of [
    item.properties,
    ...Object.values(item.column_properties ?? {}),
  ]) {
    for (const key of Object.keys(props)) {
      if (!seen.has(key)) {
        seen.add(key);
        extraKeys.push(key);
      }
    }
  }
  const presetOrder = new Map(
    COPAY_FIELD_PRESETS.map((field, index) => [field.key, index]),
  );
  const extras = extraKeys
    .sort(
      (left, right) =>
        (presetOrder.get(left) ?? Number.MAX_SAFE_INTEGER) -
        (presetOrder.get(right) ?? Number.MAX_SAFE_INTEGER),
    )
    .map((key) => ({ key, label: propertyLabel(key) }));
  return [...COPAY_FIELDS, ...extras];
}

/** Effective copay value for one column (column override → shared value). */
export function copayValue(
  item: SobItemAnswer,
  columnId: string,
  key: string,
): string {
  return item.column_properties?.[columnId]?.[key] ?? item.properties?.[key] ?? "";
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
    const { [key]: _sharedDrop, ...properties } = it.properties ?? {};
    const next = { ...it, properties, column_properties: colProps };
    return key === "per_policy_year"
      ? invalidateClaimLimits(next, sob.columns.map((column) => column.id))
      : next;
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
    const claimLimits = { ...(it.claim_limits ?? {}) };
    delete claimLimits[columnId];
    return {
      ...it,
      overrides,
      column_properties: it.column_properties ? colProps : undefined,
      claim_limits: it.claim_limits ? claimLimits : undefined,
      sub_items: it.sub_items.map((s) => {
        const { [columnId]: _s, ...subOv } = s.overrides;
        return { ...s, overrides: subOv };
      }),
    };
  });
  return { ...sob, columns, items };
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
