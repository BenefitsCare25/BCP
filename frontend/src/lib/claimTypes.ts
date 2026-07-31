/** Canonical identity of a claim type's review setup.
 *
 * MUST mirror the backend's `claim_review_configs._norm_key` (inner
 * whitespace collapsed, case-folded) — the backend stores `claim_key`
 * normalized that way and resolves configs with it, so a looser key here
 * makes the UI disagree with what actually runs: a configured claim type
 * renders as "Default" while its rules are live, and "Customize" then 409s
 * with duplicate_claim_type.
 */
export function normalizeClaimKey(value: string | null | undefined): string {
  return String(value ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function claimTypeKey(
  claimKind: string,
  claimKey: string | null | undefined,
): string {
  return `${claimKind}:${normalizeClaimKey(claimKey)}`;
}
