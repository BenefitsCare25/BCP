/** The e-form's steps, wrapped around the election deck.
 *
 * `MemberEnrollmentPanel` owns the elections; this module owns everything the
 * paper form asked for besides them — particulars, family eligibility,
 * documents and declarations, the signature — and hands the panel ready-made
 * slides. Split this way so the panel does not grow a second form inside it,
 * and so the broker preview (which never passes a form) is untouched.
 *
 * Deck order: Your details → products → (standard, leave) → Your family →
 * Read and agree → Review → Sign and send. */
import { useEffect, useState } from "react";
import type { ProductTierSet } from "@/api/enrollment";
import type { MemberFormContext } from "@/api/enrollmentForms";
import type { FormSignInput } from "@/api/portalEnrollmentForms";
import type { ProductState } from "@/components/enrollment/electionCore";
import type { DeckSlide } from "@/components/portal/leaf/Deck";
import { AgreeMount } from "./AgreeMount";
import { FamilyMount } from "./FamilyMount";
import { type ContactDraft, ParticularsMount, contactErrors } from "./ParticularsMount";
import { type ShareLine, SignMount } from "./SignMount";
import {
  type PendingRequests,
  applicableClauses,
  eligibilityProblems,
  familyProducts,
  memberShare,
  namesFamily,
  prunePending,
  ruleProblems,
} from "./formMath";

export const DETAILS_KEY = "details";
export const FAMILY_KEY = "family";
export const AGREE_KEY = "agree";
export const SIGN_KEY = "sign";

export interface FormDraft {
  dirty: boolean;
  contact: ContactDraft;
  setContact: (next: ContactDraft) => void;
  pending: PendingRequests;
  setPending: (next: PendingRequests) => void;
  accepted: Set<string>;
  setAccepted: (next: Set<string>) => void;
  opened: Set<string>;
  markOpened: (id: string) => void;
  signature: string;
  setSignature: (value: string) => void;
  confirmed: boolean;
  setConfirmed: (value: boolean) => void;
  /** Clear the signature after a successful send — a second send must be a
   *  second, deliberate signature. */
  resetSignature: () => void;
}

export function useFormDraft(ctx: MemberFormContext | null): FormDraft {
  const [contact, setContact] = useState<ContactDraft>({ contactNo: "", email: "" });
  const [pending, setPending] = useState<PendingRequests>({});
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [signature, setSignature] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [sentDraft, setSentDraft] = useState<string | null>(null);
  const seed = ctx?.particulars;

  // Seed the contact fields once the record arrives; never overwrite typing.
  useEffect(() => {
    if (!seed) return;
    setContact((c) =>
      c.contactNo || c.email
        ? c
        : { contactNo: seed.contact_no ?? "", email: seed.email ?? "" },
    );
  }, [seed]);

  return {
    dirty: JSON.stringify([contact, pending, [...accepted], signature, confirmed]) !== (sentDraft ??
      JSON.stringify([{ contactNo: seed?.contact_no ?? "", email: seed?.email ?? "" }, {}, [], "", false])),
    contact,
    setContact,
    pending,
    setPending,
    accepted,
    setAccepted,
    opened,
    markOpened: (id) => setOpened((s) => new Set(s).add(id)),
    signature,
    setSignature,
    confirmed,
    setConfirmed,
    resetSignature: () => {
      setSentDraft(JSON.stringify([contact, pending, [...accepted], "", false]));
      setSignature("");
      setConfirmed(false);
    },
  };
}

export interface FormSlideInput {
  ctx: MemberFormContext;
  draft: FormDraft;
  tierSets: ProductTierSet[];
  state: Record<string, ProductState>;
  disabled: boolean;
  /** Why the deck itself refuses to send (leave typo, overdrawn wallet). */
  blocked: string | null;
  /** The period lets members change family cover at all. */
  allowDeps: boolean;
  signing: boolean;
  onSign: () => void;
}

export interface FormSlides {
  details: DeckSlide;
  family: DeckSlide | null;
  agree: DeckSlide | null;
  sign: DeckSlide;
}

function shareLines(
  ctx: MemberFormContext,
  tierSets: ProductTierSet[],
  state: Record<string, ProductState>,
): ShareLine[] {
  const out: ShareLine[] = [];
  for (const contribution of ctx.contributions) {
    const ts = tierSets.find((t) => t.product_code === contribution.product_code);
    if (!ts) continue;
    const amount = memberShare(contribution, ts, state[ts.product_code], ctx.dependants);
    if (amount !== null) {
      out.push({ code: ts.product_code, name: ts.product_name ?? ts.product_code, amount });
    }
  }
  return out;
}

/** Plain function, not a hook: the panel calls it after its own early
 * returns, once the elections and the send-blockers are known. */
export function buildFormSlides(input: FormSlideInput): FormSlides {
  const { ctx, draft, tierSets, state, disabled } = input;
  const shares = shareLines(ctx, tierSets, state);
  const requestable = familyProducts(state, tierSets, input.allowDeps);
  const pending = prunePending(draft.pending, requestable, ctx.dependants);

  const family = namesFamily(state, tierSets, pending, ctx.dependants);
  const clauses = applicableClauses(ctx.clauses, family);
  const contactProblem = Object.values(contactErrors(draft.contact))[0];
  const problems = [
    ...(input.blocked ? [input.blocked] : []),
    ...ruleProblems(ctx.rules, state, tierSets, ctx.dependants),
    ...eligibilityProblems(state, tierSets, ctx.dependants),
    ...(contactProblem ? [`Your details: ${contactProblem}`] : []),
    ...(clauses.some((c) => !draft.accepted.has(c.id))
      ? ["Tick every declaration on the Read and agree step."]
      : []),
  ];
  const pendingCount = Object.keys(pending).length;
  const unaccepted = clauses.filter((c) => !draft.accepted.has(c.id)).length;

  return {
    details: {
      key: DETAILS_KEY,
      label: "Your details",
      mark: contactProblem ? "Needs fixing" : undefined,
      render: () => (
        <ParticularsMount
          particulars={ctx.particulars}
          draft={draft.contact}
          disabled={disabled}
          onChange={draft.setContact}
        />
      ),
    },
    family:
      ctx.dependants.length || ctx.eligibility_notes.length
        ? {
            key: FAMILY_KEY,
            label: "Your family",
            mark: pendingCount ? `${pendingCount} to add` : undefined,
            render: () => (
              <FamilyMount
                dependants={ctx.dependants}
                eligibilityNotes={ctx.eligibility_notes}
                familyProducts={requestable}
                pending={pending}
                disabled={disabled}
                onPendingChange={draft.setPending}
              />
            ),
          }
        : null,
    agree:
      clauses.length || ctx.documents.length
        ? {
            key: AGREE_KEY,
            label: "Read and agree",
            mark: !disabled && unaccepted ? `${unaccepted} to tick` : undefined,
            render: () => (
              <AgreeMount
                documents={ctx.documents}
                clauses={clauses}
                accepted={draft.accepted}
                opened={draft.opened}
                disabled={disabled}
                onAcceptedChange={draft.setAccepted}
                onOpened={draft.markOpened}
              />
            ),
          }
        : null,
    sign: {
      key: SIGN_KEY,
      label: "Sign and send",
      render: () => (
        <SignMount
          shares={shares}
          gstIncluded={ctx.contributions.some((c) => c.gst_included)}
          problems={disabled ? [] : problems}
          expectedName={ctx.particulars.name}
          signature={draft.signature}
          confirmed={draft.confirmed}
          signing={input.signing}
          disabled={disabled}
          latest={ctx.latest}
          submissionNote={ctx.submission_note}
          helpline={ctx.helpline}
          onSignatureChange={draft.setSignature}
          onConfirmedChange={draft.setConfirmed}
          onSign={input.onSign}
        />
      ),
    },
  };
}

/** The sign request body from the deck's state — requests pruned to plans
 * still requestable, declarations to those that apply. */
export function signPayload(
  draft: FormDraft,
  ctx: MemberFormContext,
  scope: {
    tierSets: ProductTierSet[];
    state: Record<string, ProductState>;
    allowDeps: boolean;
  },
  choices: Pick<FormSignInput, "elections" | "leave">,
): FormSignInput {
  const pending = prunePending(
    draft.pending,
    familyProducts(scope.state, scope.tierSets, scope.allowDeps),
    ctx.dependants,
  );
  const family = namesFamily(scope.state, scope.tierSets, pending, ctx.dependants);
  const clauses = applicableClauses(ctx.clauses, family);
  return {
    ...choices,
    particulars: {
      contact_no: draft.contact.contactNo.trim() || null,
      email: draft.contact.email.trim() || null,
    },
    pending_requests: Object.entries(pending).map(([dependant_id, product_codes]) => ({
      dependant_id,
      product_codes,
    })),
    accepted_clause_ids: clauses.filter((c) => draft.accepted.has(c.id)).map((c) => c.id),
    signature_name: draft.signature.trim(),
    confirm: draft.confirmed,
  };
}
