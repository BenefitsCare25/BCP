/** Review signals for the broker's Schedule-of-Benefits editor.
 *
 * Two different questions, kept apart on purpose:
 *   - `rowIssues`: something is probably WRONG and needs a decision before
 *     confirming (a slip heading parsed as a benefit, a duplicate row, a count
 *     that will print as dollars).
 *   - `memberVisibility`: nothing is wrong, but employees won't see this row
 *     (every plan says NA, or it's insurer admin wording). Shown so the broker
 *     knows what the portal will leave out, never as an error. */
import type { SobColumn, SobItemAnswer } from "@/types";
import { cellValue, copayFields, copayValue } from "@/lib/sob";
import { hiddenFromMembers, isAbsentValue, isNotApplicable } from "@/lib/sobValues";

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

export type MemberVisibility = { hidden: boolean; reason: string | null };

/** Whether employees will see this row at all, and why not. */
export function memberVisibility(item: SobItemAnswer, columns: SobColumn[]): MemberVisibility {
  if (hiddenFromMembers(item)) {
    return {
      hidden: true,
      reason: item.member_hidden === true ? "Hidden from employees" : "Insurer admin wording — hidden from employees",
    };
  }
  if (STRUCTURAL_KINDS.has(item.kind ?? "")) return { hidden: false, reason: null };
  const values = rowValues(item, columns).filter((v) => String(v ?? "").trim());
  const hasSubValues = item.sub_items.some((sub) => !isAbsentValue(sub.base_value));
  if (values.length > 0 && values.every(isAbsentValue) && !hasSubValues) {
    return {
      hidden: true,
      reason: values.every(isNotApplicable)
        ? "Every plan says NA — hidden from employees"
        : "Not covered on any plan — hidden from employees",
    };
  }
  return { hidden: false, reason: null };
}
