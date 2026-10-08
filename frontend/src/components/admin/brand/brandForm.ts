/** Brand settings form model: the editable draft, its validation and the
 *  conversion to the API's nullable fields (blank = inherit). Shared by the
 *  firm brand and company override forms. */
import { useEffect, useState } from "react";
import type { BrandFields } from "@/api/brand";
import { HEX_COLOR } from "@/lib/brand";

export type FieldKey = keyof BrandFields;
export type ColorKey = "primary_color" | "accent_color";
/** Text inputs: the shared fields plus the firm-only From address and
 *  e-card prefix. */
export type TextKey = Exclude<FieldKey, ColorKey> | "email_from_address" | "card_prefix";

/** Firm-only fields ride along on the firm form; the company form ignores
 *  them. */
export type Draft = Record<FieldKey, string> & {
  email_from_address: string;
  card_prefix: string;
  show_platform_attribution: boolean;
};

export type Saved = Partial<BrandFields> & {
  email_from_address?: string | null;
  card_prefix?: string | null;
  show_platform_attribution?: boolean | null;
  revision: number;
};

export const FIELD_KEYS: readonly FieldKey[] = [
  "product_name",
  "short_name",
  "primary_color",
  "accent_color",
  "support_email",
  "support_phone",
  "email_sender_name",
  "email_reply_to",
];

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CARD_PREFIX = /^[A-Z0-9]{2,8}$/;

export interface TextFieldSpec {
  label: string;
  max: number;
  type: "text" | "email" | "tel";
  help?: string;
  email?: boolean;
  /** Normalise as typed. */
  transform?: (value: string) => string;
}

export const TEXT_FIELDS: Record<TextKey, TextFieldSpec> = {
  product_name: {
    label: "Product name",
    max: 80,
    type: "text",
    help: "Shown in page titles, headers, emails and the authenticator app.",
  },
  short_name: {
    label: "Short name",
    max: 30,
    type: "text",
    help: "The label under the home-screen icon when someone installs the site.",
  },
  support_email: { label: "Support email", max: 320, type: "email", email: true },
  support_phone: { label: "Support phone", max: 40, type: "tel" },
  email_sender_name: {
    label: "Email sender name",
    max: 120,
    type: "text",
    help: "The name system emails come from.",
  },
  email_reply_to: {
    label: "Reply-to address",
    max: 320,
    type: "email",
    email: true,
    help: "Where replies to system emails go.",
  },
  email_from_address: {
    label: "From address",
    max: 320,
    type: "email",
    email: true,
    help: "Used once the platform has verified it for your domain. Until then, emails come from the platform's address with your sender name.",
  },
  card_prefix: {
    label: "E-card number prefix",
    max: 8,
    type: "text",
    help: "2 to 8 letters or digits. Applies to companies created after you save. Existing companies keep their card numbers.",
    transform: (value) => value.toUpperCase(),
  },
};

export function draftOf(saved: Saved | null): Draft {
  const text = (value: string | null | undefined) => value ?? "";
  return {
    product_name: text(saved?.product_name),
    short_name: text(saved?.short_name),
    primary_color: text(saved?.primary_color).toLowerCase(),
    accent_color: text(saved?.accent_color).toLowerCase(),
    support_email: text(saved?.support_email),
    support_phone: text(saved?.support_phone),
    email_sender_name: text(saved?.email_sender_name),
    email_reply_to: text(saved?.email_reply_to),
    card_prefix: text(saved?.card_prefix),
    email_from_address: text(saved?.email_from_address),
    show_platform_attribution: saved?.show_platform_attribution !== false,
  };
}

export function sameDraft(a: Draft, b: Draft, firmFields: boolean): boolean {
  const keys: (keyof Draft)[] = firmFields
    ? [...FIELD_KEYS, "email_from_address", "card_prefix", "show_platform_attribution"]
    : [...FIELD_KEYS];
  return keys.every((key) =>
    typeof a[key] === "string" ? String(a[key]).trim() === String(b[key]).trim() : a[key] === b[key],
  );
}

export type Errors = Partial<Record<keyof Draft, string>>;

/** For a company override: the firm's own saved name and support email,
 *  which the company inherits. */
export interface InheritedRows {
  product_name: string | null;
  support_email: string | null;
}

export const SUPPORT_EMAIL_REQUIRED =
  "Add a support email: members of a renamed site are shown it instead of the platform helpdesk.";

export function validateDraft(draft: Draft, firmFields: boolean, firmRow?: InheritedRows): Errors {
  const errors: Errors = {};
  for (const key of ["primary_color", "accent_color"] as const) {
    const value = draft[key].trim();
    if (value && !HEX_COLOR.test(value)) errors[key] = "Enter a colour as #rrggbb, such as #c11a2b.";
  }
  const textKeys = (Object.keys(TEXT_FIELDS) as TextKey[]).filter(
    (key) => firmFields || (key !== "email_from_address" && key !== "card_prefix"),
  );
  for (const key of textKeys) {
    const spec = TEXT_FIELDS[key];
    const value = draft[key].trim();
    if (!value) continue;
    if (value.length > spec.max) errors[key] = `Keep it to ${spec.max} characters or fewer.`;
    else if (spec.email && !EMAIL.test(value)) errors[key] = "Enter an email address, such as help@example.com.";
  }
  const prefix = draft.card_prefix.trim();
  if (firmFields && prefix && !CARD_PREFIX.test(prefix)) {
    errors.card_prefix = "Use 2 to 8 letters or digits, without spaces.";
  }
  // Server rule: a brand with its own product name needs its own support
  // email (a company may inherit the firm's), never the platform helpdesk.
  const product = draft.product_name.trim() || firmRow?.product_name;
  const support = draft.support_email.trim() || firmRow?.support_email;
  if (product && !support && !errors.support_email) errors.support_email = SUPPORT_EMAIL_REQUIRED;
  return errors;
}

/** The draft as API fields: trimmed, blank = null (inherit). */
export function fieldsOf(draft: Draft): BrandFields {
  const value = (raw: string) => raw.trim() || null;
  return {
    product_name: value(draft.product_name),
    short_name: value(draft.short_name),
    primary_color: value(draft.primary_color)?.toLowerCase() ?? null,
    accent_color: value(draft.accent_color)?.toLowerCase() ?? null,
    support_email: value(draft.support_email),
    support_phone: value(draft.support_phone),
    email_sender_name: value(draft.email_sender_name),
    email_reply_to: value(draft.email_reply_to),
  };
}

/** The draft beside the saved settings it was based on.
 *
 *  A refetch that only moved images or the revision (an upload) is adopted
 *  silently, keeping unsaved edits. A refetch with other people's edits is
 *  adopted only when nothing is being edited; otherwise the draft keeps its
 *  base revision and the save is refused as stale, which offers a reload. */
export function useBrandDraft(saved: Saved | null, firmFields: boolean) {
  const [baseline, setBaseline] = useState<Saved | null>(saved);
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved));
  const [adoptNext, setAdoptNext] = useState(false);

  useEffect(() => {
    if (saved === baseline) return;
    const fromSaved = draftOf(saved);
    const fromBase = draftOf(baseline);
    if (adoptNext || sameDraft(draft, fromBase, firmFields) || sameDraft(draft, fromSaved, firmFields)) {
      setAdoptNext(false);
      setBaseline(saved);
      setDraft(fromSaved);
    } else if (sameDraft(fromSaved, fromBase, firmFields)) {
      setBaseline(saved);
    }
    // Otherwise someone else changed fields being edited: keep the base.
  }, [saved, baseline, draft, firmFields, adoptNext]);

  return {
    draft,
    /** A user edit: a pending adoption no longer applies. */
    setDraft: (next: Draft | ((current: Draft) => Draft)) => {
      setAdoptNext(false);
      setDraft(next);
    },
    revision: baseline?.revision ?? 0,
    dirty: !sameDraft(draft, draftOf(baseline), firmFields),
    /** After a save or a reload: take the next saved value as-is. */
    adoptSaved: () => setAdoptNext(true),
    reset: () => {
      setBaseline(saved);
      setDraft(draftOf(saved));
    },
  };
}

/** Built-in values the public brand does not carry (backend
 *  `services/brand.py`), for the firm form's placeholders. */
export const BUILT_IN_EXTRA = { email_sender_name: "Inspro Benefits Portal", card_prefix: "INS" } as const;

/** A server refusal that belongs to one field, shown there instead of below
 *  the form. */
export function fieldRefusal(code: string | undefined): keyof Draft | null {
  return code === "brand_support_email_required" ? "support_email" : null;
}
