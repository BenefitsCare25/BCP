/** Review signals for the broker's Schedule-of-Benefits editor.
 *
 * Two different questions, kept apart on purpose:
 *   - `rowIssues`: something is probably WRONG and needs a decision before
 *     confirming (a slip heading parsed as a benefit, a duplicate row, a count
 *     that will print as dollars).
 *   - `rowVisibility`: whether employees see each row on the portal, and why.
 *     Stated for every row, never as an error, so the broker can read the
 *     portal outcome straight down the table. */
import type { SobColumn, SobItemAnswer } from "@/types";
import { cellValue, copayFields, copayValue } from "@/lib/sob";
import { isAbsentValue, isAdminRow, isNotApplicable } from "@/lib/sobValues";

const HEADING_NAME = /^\s*(endorsements?|exclusions?|additional arrangements?|to note|list of exclusions?)\b|:\s*$/i;
const COUNT_NAME = /\b(?:number|no\.?|max(?:imum)?\.?(?:\s+no\.?)?)\s+(?:of\s+)?(visits?|days?|sessions?|treatments?|times)\b/i;
const BARE_NUMBER = /^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$/;
const STRUCTURAL_KINDS = new Set(["list", "scale", "group"]);

const rowKey = (name: string) => name.replace(/&/g, "and").toLowerCase().replace(/[^a-z0-9]/g, "");

/** Every value a row states across the benefit columns (cells + copay fields). */
function rowValues(item: SobItemAnswer, columns: SobColumn[]): string[] {
  if (item.kind === "copay") {
    return columns.flatMap((col) =>
      copayFields(item).map((field) => copayValue(item, col.id, field.key)),
    );
  }
  return columns.map((col) => cellValue(item, col.id));
}

export function rowIssues(items: SobItemAnswer[], columns: SobColumn[]): Map<string, string[]> {
  const issues = new Map<string, string[]>();
  const add = (uid: string, message: string) =>
    issues.set(uid, [...(issues.get(uid) ?? []), message]);
  const seen = new Map<string, string>();

  for (const item of items) {
    const name = item.name.trim();
    if (!name) add(item.uid, "Benefit has no name");
    else if (HEADING_NAME.test(name)) {
      add(item.uid, "Name looks like a slip heading, not a benefit — rename it to the benefit it prices");
    }
    const key = rowKey(name);
    if (key) {
      const first = seen.get(key);
      if (first) add(item.uid, `Same benefit as "${first}" — remove one`);
      else seen.set(key, name);
    }
    if (STRUCTURAL_KINDS.has(item.kind ?? "")) continue;
    const values = rowValues(item, columns);
    const hasSubValues = item.sub_items.some(
      (sub) => !isAbsentValue(sub.base_value) || Object.values(sub.overrides ?? {}).some((v) => !isAbsentValue(v)),
    );
    if (values.every((v) => !String(v ?? "").trim()) && !hasSubValues && !item.note) {
      add(item.uid, "No value in any plan — fill it in or remove the row");
    }
    const kind = item.kind ?? "amount";
    if (
      COUNT_NAME.test(name) &&
      (kind === "amount" || kind === "currency") &&
      values.some((v) => BARE_NUMBER.test(String(v ?? "").trim()))
    ) {
      add(item.uid, 'Looks like a count — set the value type to "Text" so it doesn\'t show as dollars');
    }
  }
  return issues;
}

export type RowVisibility = {
  /** Employees see nothing of this row. */
  hidden: boolean;
  /** False when no switch can put the row on the portal: it has no value on
   * any plan, and the member projection drops empty rows unconditionally. */
  toggleable: boolean;
  /** Short state for the Visible column. */
  label: string;
  /** The full why, for the tooltip. */
  reason: string;
};

/**
 * Whether employees see this row, and why — for EVERY row, not only hidden
 * ones, so a broker can read the portal outcome down the whole table.
 *
 * Mirrors `backend/app/services/member_schedule._clean_item`, in its order:
 * a row with nothing to show is dropped whatever the broker chose, so that
 * test comes first and makes the row non-toggleable; only then does the
 * broker's `member_hidden` choice, then the admin-wording default, apply.
 * The member projection runs per plan, so a row NA on some plans only is
 * still shown — to the plans that state a value.
 */
export function rowVisibility(item: SobItemAnswer, columns: SobColumn[]): RowVisibility {
  let partialNA = false;
  if (!STRUCTURAL_KINDS.has(item.kind ?? "")) {
    const stated = rowValues(item, columns).map((v) => String(v ?? "").trim()).filter(Boolean);
    const anyValue = stated.some((v) => !isAbsentValue(v));
    const anySub = item.sub_items.some(
      (sub) =>
        !isAbsentValue(sub.base_value) ||
        Object.values(sub.overrides ?? {}).some((v) => !isAbsentValue(v)) ||
        Boolean(sub.note?.trim()),
    );
    const anyLimit = (item.limits ?? []).some((limit) => !isAbsentValue(limit.value));
    // A note alone survives only on a row that never stated a value (a
    // heading like "Includes surgical implants"), never on a copay group.
    const noteSurvives = Boolean(item.note?.trim()) && stated.length === 0 && item.kind !== "copay";
    if (!anyValue && !anySub && !anyLimit && !noteSurvives) {
      const allNA = stated.length > 0 && stated.every(isNotApplicable);
      const notCovered = stated.length > 0 && !allNA;
      return {
        hidden: true,
        toggleable: false,
        label: allNA ? "Always hidden · NA" : notCovered ? "Always hidden · not covered" : "Always hidden · empty",
        reason: allNA
          ? "Always hidden: every plan says NA, so there is nothing to show employees."
          : notCovered
            ? "Always hidden: not covered on any plan, so there is nothing to show employees."
            : "Always hidden: no value on any plan yet. Fill in a value to show it.",
      };
    }
    partialNA = anyValue && stated.some(isAbsentValue);
  }
  if (item.member_hidden === true) {
    return {
      hidden: true,
      toggleable: true,
      label: "Hidden",
      reason: "Hidden: you turned this row off. Turn it on to show employees.",
    };
  }
  if (item.member_hidden !== false && isAdminRow(item.name)) {
    return {
      hidden: true,
      toggleable: true,
      label: "Hidden · default",
      reason: "Hidden by default: this reads as insurer admin wording, not a benefit. Turn it on to show employees.",
    };
  }
  return {
    hidden: false,
    toggleable: true,
    label: partialNA ? "Shown · NA on some plans" : "Shown",
    reason: partialNA
      ? "Shown to plans that state a value. Plans that say NA or Not covered leave this row out."
      : item.member_hidden === false
        ? "Shown: you turned this row on for employees."
        : "Shown to employees.",
  };
}
