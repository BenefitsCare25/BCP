import type { CoverageOptions } from "@/api/portal";

/** The name a member goes by: the bracketed preferred name when the roster
 *  carries one ("Chow Wo Keon (Raymond)" → "Raymond"), else the first word.
 *  An email is never a name, so it falls through to the generic greeting. */
export function familiarName(displayName: string): string {
  const preferred = displayName.match(/\(([^)]+)\)/)?.[1]?.trim();
  if (preferred) return preferred;
  if (displayName.includes("@")) return "there";
  return displayName.trim().split(/\s+/)[0] || "there";
}

/** The member's insurer ID for the card counter, from the first insured plan
 *  that carries one. Null when no insurer has issued one yet. */
export function firstMemberId(
  options: CoverageOptions | undefined,
): { insurer: string | null; id: string } | null {
  const hit = options?.insured?.find((option) => option.insurer_member_id);
  return hit?.insurer_member_id ? { insurer: hit.insurer, id: hit.insurer_member_id } : null;
}
