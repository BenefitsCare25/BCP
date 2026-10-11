import { createContext, useContext, type ReactNode } from "react";
import { FirmOwnerOnly } from "@/components/auth/FirmOwnerOnly";
import type { SetupAnswers } from "@/types";

/** What the stored setup draft already holds. The API refuses a non-owner's
 *  save that drops one of these (services/setup_removals.py), so only their
 *  Remove controls are firm-owner actions. An entry added in the current,
 *  unsaved edit can be taken out again by whoever added it. */
export interface SavedSetupEntries {
  /** Benefit rows and sub-benefits, by uid. */
  uids: ReadonlySet<string>;
  columnIds: ReadonlySet<string>;
  /** Saved limits per `LimitRows` list: a row uid, `subLimitsKey` for a
   *  sub-benefit's list, or `<row uid>-<column id>` for a column's own list. */
  limitCounts: ReadonlyMap<string, number>;
  /** Row uids with a saved plan-specific (per-column) limit list. */
  columnLimitRows: ReadonlySet<string>;
  endorsements: number;
  /** Claim-limit settings, as `<row uid>-<column id>`. */
  claimLimits: ReadonlySet<string>;
  /** Plan codes with an overall plan limit. */
  planClaimLimits: ReadonlySet<string>;
  /** Co-payment / qualifier field keys per row uid (shared and per column). */
  fieldKeys: ReadonlyMap<string, ReadonlySet<string>>;
}

const SavedEntriesContext = createContext<SavedSetupEntries | null>(null);

export const SavedEntriesProvider = SavedEntriesContext.Provider;

/** A sub-benefit's limit list, by position: slip-derived sub-benefits carry no
 *  uid, and only appended sub-benefits can be removed by a non-owner. */
export function subLimitsKey(itemUid: string, subIdx: number): string {
  return `${itemUid}#sub${subIdx}`;
}

export function savedSetupEntries(answers: SetupAnswers | null | undefined): SavedSetupEntries {
  const uids = new Set<string>();
  const limitCounts = new Map<string, number>();
  const claimLimits = new Set<string>();
  const columnLimitRows = new Set<string>();
  const fieldKeys = new Map<string, Set<string>>();
  const columns = answers?.sob?.columns ?? [];
  for (const item of answers?.sob?.items ?? []) {
    uids.add(item.uid);
    limitCounts.set(item.uid, item.limits?.length ?? 0);
    const keys = new Set(Object.keys(item.properties ?? {}));
    for (const column of columns) {
      const own = item.column_limits?.[column.id] ?? item.limits ?? [];
      limitCounts.set(`${item.uid}-${column.id}`, own.length);
      if (item.column_limits?.[column.id]?.length) columnLimitRows.add(item.uid);
      if (item.claim_limits?.[column.id]) claimLimits.add(`${item.uid}-${column.id}`);
      for (const key of Object.keys(item.column_properties?.[column.id] ?? {})) keys.add(key);
    }
    fieldKeys.set(item.uid, keys);
    (item.sub_items ?? []).forEach((sub, subIdx) => {
      uids.add(sub.uid);
      limitCounts.set(subLimitsKey(item.uid, subIdx), sub.limits?.length ?? 0);
    });
  }
  return {
    uids,
    columnIds: new Set(columns.map((column) => column.id)),
    limitCounts,
    columnLimitRows,
    endorsements: answers?.endorsements?.length ?? 0,
    claimLimits,
    planClaimLimits: new Set(Object.keys(answers?.sob?.plan_claim_limits ?? {})),
    fieldKeys,
  };
}

/** Null outside a setup form, where every entry counts as saved. */
export function useSavedEntries(): SavedSetupEntries | null {
  return useContext(SavedEntriesContext);
}

/** Wraps a Remove control: firm-owner only for a saved entry. */
export function RemoveGuard({ saved, children }: { saved: boolean; children: ReactNode }) {
  return saved ? <FirmOwnerOnly>{children}</FirmOwnerOnly> : <>{children}</>;
}
