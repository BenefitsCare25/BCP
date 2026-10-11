import { usePortalSession } from "@/stores/portalSession";

function storageKey(): string {
  return `inspro-claim-period:${usePortalSession.getState().member?.id ?? "anonymous"}`;
}

export function selectedClaimPeriod(): string | null {
  try { return sessionStorage.getItem(storageKey()); } catch { return null; }
}

export function setClaimPeriod(id: string, reload = true): void {
  if (id) sessionStorage.setItem(storageKey(), id);
  else sessionStorage.removeItem(storageKey());
  // A new page lifetime clears all claim query/form caches together. This
  // switch is offered on the ledger, never over an unsaved claim form.
  if (reload) window.location.reload();
}

// Every endpoint a claim in the selected period is filed or answered through.
// Referral letters and visit anchors belong to the claim being filed: without
// the period, a run-off leaver filing last year's specialist claim resolved
// against the current year and was refused.
const CLAIM_PERIOD_PATHS = [
  "/portal/claims",
  "/portal/coverage-options",
  "/portal/referral-letters",
  "/portal/claim-anchors",
];

export function claimPeriodHeaders(path: string): Record<string, string> {
  const id = selectedClaimPeriod();
  return id && CLAIM_PERIOD_PATHS.some((prefix) => path.startsWith(prefix))
    ? { "X-Inspro-Claims-Year-ID": id } : {};
}
