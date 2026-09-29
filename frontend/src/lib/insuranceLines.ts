import type { InsuranceLine, RegistryEntry, SetupProductSummary } from "@/types";

export const INSURANCE_LINES: InsuranceLine[] = [
  "medical",
  "life",
  "general",
  "flex",
];

export const LINE_LABELS: Record<InsuranceLine, string> = {
  medical: "Medical Insurance",
  general: "General Insurance",
  life: "Life Insurance",
  flex: "Flex",
};

/** Separator between a product type and its variant label ("GHS-VTS") —
 *  mirrors backend `product_registry.VARIANT_SEP`. */
export const VARIANT_SEP = "-";

/**
 * Resolve a product code to its insurance line, defaulting to Medical.
 *
 * The API is the source of truth — `Product.line`, `CategoryGroup.line`, and
 * `SetupProductSummary.line` all carry the resolved line. This helper covers
 * the gap where we hold a bare product *code* and no API row yet (e.g. a
 * freshly typed Add-product code before the catalog refetch, or labelling an
 * upload toast from `ParseResult.prefilled_setups`) — pass the registry
 * entries from `useRegistry()` so the lookup uses the backend catalog instead
 * of a hardcoded mirror.
 */
export function lineForCode(
  code: string,
  registryEntries?: RegistryEntry[],
): InsuranceLine {
  const token = (code || "").trim().toUpperCase();
  // A product variant ("GHS-VTS") sits on its base type's line.
  const base = token.split(VARIANT_SEP, 1)[0];
  const entry =
    registryEntries?.find((e) => e.code === token) ??
    registryEntries?.find((e) => e.code === base);
  return entry?.line ?? "medical";
}

/**
 * A product is "added" to its tab — i.e. shows as a configurable card and gets
 * a coverage period — when the client has its own catalog row, a slip created
 * it, or a setup draft exists. Bare global recognition rows are not added.
 */
export function isProductAdded(
  p: SetupProductSummary,
  draftCodes: Set<string>,
): boolean {
  return p.is_client_product || p.has_slip_data || draftCodes.has(p.code);
}
