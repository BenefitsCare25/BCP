/** Vocabulary shared by the claim form's pieces.
 *
 * The form was one 1,493-line component with ~25 pieces of state, against the
 * house 100-line function / 500-line file ceilings. It is split into this
 * module (constants + pure helpers), `useNewClaimForm` (the state machine) and
 * the section components — with no behaviour change: every rule that lived in
 * the original is carried over verbatim, including the comments explaining why
 * it is written the way it is. */
import type {
  ClaimIntakeSuggestion,
  InsuredClaimOption,
  IntakeSuggestFields,
} from "@/api/portal";

export const ACCEPT = ".pdf,.png,.jpg,.jpeg";
export const MAX_BYTES = 15 * 1024 * 1024;

/** How many documents the member may upload for AI autofill in one go — the
 * full set for one claim (e.g. tax invoice + itemised bill + discharge
 * summary). Must match the backend MAX_INTAKE_FILES. */
export const MAX_AUTOFILL_FILES = 3;

export const MAX_REMARKS = 500;

/** Fallback only — the live list rides on /portal/coverage-options so the
 * backend's ALLOWED_CURRENCIES stays the single source of truth. */
export const FALLBACK_CURRENCIES = ["SGD", "USD", "MYR", "EUR", "GBP", "AUD"];

/** The unified claim-type dropdown encodes the kind, the product, and the
 * claim-type entry index in one value (`insured:<code>:<idx>` / `flex:<name>`)
 * so `claim_kind` and `sub_type` are derived, never separately chosen. */
export const INSURED_PREFIX = "insured:";
export const FLEX_PREFIX = "flex:";

/** Sentinel for an unlisted/overseas hospital — frees the provider text input. */
export const OTHER_HOSPITAL = "__other__";

/** Friendly names for the autofill "double-check these" hint. */
export const LOW_CONF_LABELS: Record<string, string> = {
  provider_name: "provider",
  amount: "amount",
  incurred_date: "date",
  invoice_number: "invoice number",
  diagnosis: "diagnosis",
};

export const GROUP_LABELS = {
  outpatient: "Outpatient",
  inpatient: "Inpatient",
  other: "Other insurance",
} as const;

export type InsuredGroupKey = keyof typeof GROUP_LABELS;

export interface TypeEntry {
  value: string;
  label: string;
  product: InsuredClaimOption;
}

export type ReferralMode = "" | "upload" | "existing";

/** One queued claim in a multi-invoice upload: the invoice document that
 * anchors it plus the fields read off it. `uploadIndex` is the stable id
 * (original upload position) — used as the list key and removal handle so
 * duplicate file names can't collapse two queued claims into one. */
export interface PendingClaim {
  uploadIndex: number;
  fileName: string;
  file: File | null;
  slot: string | null;
  detectedType: string | null;
  fields: IntakeSuggestFields | null;
  lowConfidence: string[];
}

/** An autofill upload paired with the required-document slot the AI matched it
 * to (null = unknown → the first free slot). */
export interface AutofillDoc {
  file: File;
  slot: string | null;
  detectedType: string | null;
}

/** Classify a hospital name into its sector, mirroring the backend
 * `sg_hospitals.hospital_sector` (collapse whitespace, lowercase, fold curly
 * apostrophes and the " and " spelling) so the form and the submit check can't
 * disagree about which documents a hospitalisation claim requires. Returns
 * null for an unlisted/overseas name (the caller falls back to the private
 * default). */
export function normHospital(s: string): string {
  return s
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase()
    .replace(/’/g, "'")
    .replace(/ and /g, " & ");
}

export function sectorForHospital(
  hospitals: { name: string; sector: "govt" | "private" }[],
  name: string,
): "govt" | "private" | null {
  const n = normHospital(name);
  return hospitals.find((h) => normHospital(h.name) === n)?.sector ?? null;
}

/** Which uploaded files belong to THIS claim, and which start a queue.
 *
 * Multi-invoice upload: each anchor invoice is its own claim, because a claim is
 * the adjudication unit (per-visit limits, the duplicate-receipt SHA and
 * utilisation all key on it). The FIRST anchor prefills the open form — its
 * fields ARE the top-level suggestion — and the rest queue up and must NOT ride
 * along as this claim's evidence.
 *
 * Files are joined to documents on `upload_index`, never on file name: two
 * uploads can share a name, and the backend may skip an unreadable file, so
 * position in the original upload is the only stable key. */
export function planFromSuggestion(
  s: ClaimIntakeSuggestion,
  picked: File[],
): { autofillDocs: AutofillDoc[]; pendingClaims: PendingClaim[] } {
  const metaByIdx = new Map((s.documents ?? []).map((d) => [d.upload_index, d]));
  const anchors = s.multi_claim
    ? (s.documents ?? [])
        .filter((d) => d.claim_index != null)
        .sort((a, b) => (a.claim_index ?? 0) - (b.claim_index ?? 0))
    : [];
  const laterAnchors = anchors.slice(1);
  const laterIdx = new Set(laterAnchors.map((d) => d.upload_index));
  return {
    autofillDocs: picked
      .map((file, i) => ({ file, i }))
      .filter(({ i }) => !laterIdx.has(i))
      .map(({ file, i }) => ({
        file,
        slot: metaByIdx.get(i)?.doc_slot ?? null,
        detectedType: metaByIdx.get(i)?.detected_doc_type ?? null,
      })),
    pendingClaims: laterAnchors.map((d) => ({
      uploadIndex: d.upload_index,
      fileName: d.file_name,
      file: picked[d.upload_index] ?? null,
      slot: d.doc_slot ?? null,
      detectedType: d.detected_doc_type ?? null,
      fields: d.fields,
      lowConfidence: d.low_confidence ?? [],
    })),
  };
}
