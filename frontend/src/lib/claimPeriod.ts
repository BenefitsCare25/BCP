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

export function claimPeriodHeaders(path: string): Record<string, string> {
  const id = selectedClaimPeriod();
  return id && (path.startsWith("/portal/claims") || path.startsWith("/portal/coverage-options"))
    ? { "X-Inspro-Claims-Year-ID": id } : {};
}
