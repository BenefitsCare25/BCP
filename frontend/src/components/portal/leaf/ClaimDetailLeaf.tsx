/** One claim, as its own claimant reads it.
 *
 * TWO surfaces render this: the member's own claim page
 * (`routes/portal/claims/detail.tsx`) and the broker's employee-view preview
 * (`components/operations/PortalFrame`). It is one component for the reason
 * every other `leaf/` component is — the preview's whole job is to be the
 * member's screen, and a second implementation of it can only ever drift into
 * being a broker's summary of that screen.
 *
 * **Nothing here fetches, navigates or mutates.** Every action arrives as a
 * prop, and an ABSENT handler renders its control disabled rather than hiding
 * it: the preview has to show the member's buttons where the member has them,
 * and a missing control with no explanation reads as broken. The composer
 * follows the same rule through `MessageThread`'s `replyDisabledReason`.
 *
 * The preview passes no handlers at all, which is also what keeps it read-only
 * by construction — and it deliberately has no way to mark the thread read:
 * that receipt belongs to the member, and a broker looking must never clear
 * their unread badge.
 */
import { useRef, type ReactNode } from "react";
import { Check, FileText, Loader2, Paperclip, Pencil, Send, X } from "lucide-react";
import type { PortalClaim } from "@/api/portal";
import type { ClaimMessage } from "@/api/portalMessages";
import { cn } from "@/lib/cn";
import { Action } from "./Action";
import { claimTitle } from "./ClaimMount";
import { formatDay } from "./date";
import { Money } from "./Figure";
import { MessageThread } from "./MessageMount";
import { ConversionNotice } from "@/components/portal/claims/ConversionNotice";
import { Mount, MountRow, MountRule } from "./Mount";
import { Strike } from "./Strike";

/** What a member may attach, and how much of it. Exported so the route's
 * upload handler enforces the same ceiling this input advertises — two numbers
 * would eventually disagree, and the one the member meets first is a silent
 * rejection after the upload. */
export const CLAIM_DOC_ACCEPT = ".pdf,.png,.jpg,.jpeg";
export const CLAIM_DOC_MAX_BYTES = 15 * 1024 * 1024;

// The generic invoice/receipt slot is satisfied by ANY attached document
// (mirrors the backend `assert_documents_satisfy_slots`); specific slots need
// a document tagged with the matching key.
const GENERIC_SLOT = "invoice_receipt";

/** Member-safe state vocabulary. Mirrors `Strike.tsx` — change both together.
 *
 * Label and tone only: an explanatory sentence under the strike ("We have it.
 * Nothing for you to do…") restated what the strike already says, and on a
 * screen whose job is "what happened to my claim?" the answer should be one
 * word, not a paragraph. Where the member DOES have to act, the sections below
 * say so with the controls attached.
 */
const STATE: Record<
  string,
  { label: string; tone: "approved" | "pending" | "review" | "rejected" }
> = {
  draft: { label: "Not sent", tone: "review" },
  submitted: { label: "Under review", tone: "review" },
  ai_review_pending: { label: "Under review", tone: "review" },
  ai_verified: { label: "Under review", tone: "review" },
  ai_flagged: { label: "Under review", tone: "review" },
  needs_info: { label: "More info needed", tone: "pending" },
  approved: { label: "Approved", tone: "approved" },
  // Kept in step with `Strike.CLAIM_STATE` — the same status must not read one
  // way in the ledger and another on the claim it opens.
  sent_to_insurer: { label: "Approved", tone: "approved" },
  paid: { label: "Paid", tone: "approved" },
  rejected: { label: "Rejected", tone: "rejected" },
};

const ATTACH_CLASS =
  "leaf-focus inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-control " +
  "border border-leaf-input bg-bar/80 px-3 text-row font-medium text-record";

/** Attach/Replace for one requirement.
 *
 * With no `onPick` it is a disabled `<button>`, not a `<label>` wrapping a
 * disabled input: a label still takes the click and opens nothing, which is the
 * one affordance worse than an obviously dead control. A real button also
 * announces as disabled and drops out of the tab order, which a styled `<span>`
 * does neither of.
 */
function AttachControl({
  done,
  slotLabel,
  busy,
  disabledTitle,
  onPick,
}: {
  done: boolean;
  slotLabel: string;
  busy?: boolean;
  disabledTitle?: string;
  onPick?: (file: File | undefined) => void;
}) {
  const label = done ? "Replace" : "Attach";
  if (!onPick) {
    return (
      <button
        type="button"
        disabled
        title={disabledTitle}
        className={cn(ATTACH_CLASS, "cursor-not-allowed opacity-60")}
      >
        <Paperclip className="size-4 shrink-0" aria-hidden />
        {label}
        <span className="sr-only"> {slotLabel}</span>
      </button>
    );
  }
  return (
    <label className={cn(ATTACH_CLASS, "cursor-pointer")}>
      <Paperclip className="size-4 shrink-0" aria-hidden />
      {label}
      <span className="sr-only"> {slotLabel}</span>
      <input
        type="file"
        accept={CLAIM_DOC_ACCEPT}
        className="sr-only"
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          onPick(f);
        }}
      />
    </label>
  );
}

/** Remove one attached file.
 *
 * HIDDEN when there is no handler, where every other control on this page
 * renders disabled instead. The rule those follow — show the member's
 * affordances where the member has them — is about controls the member is meant
 * to reach; a permanently dead DESTRUCTIVE control next to someone's document
 * is only an invitation to keep clicking at it. The preview loses nothing: it
 * still shows the file.
 *
 * The server refuses a delete that would leave a required slot empty on a sent
 * claim (409 `documents_required`), so this does not try to predict which
 * removals are allowed — one rule, server-side, reported when it fires.
 */
function RemoveDocument({
  docId,
  fileName,
  busy,
  onRemove,
}: {
  docId: string;
  fileName: string;
  busy?: boolean;
  onRemove?: (docId: string) => void;
}) {
  if (!onRemove) return null;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => onRemove(docId)}
      className="leaf-focus ml-2 inline-flex items-center gap-1 align-baseline text-2xs text-label underline underline-offset-2 hover:text-strike-rejected disabled:opacity-60"
    >
      {busy ? (
        <Loader2 className="size-3 animate-spin" aria-hidden />
      ) : (
        <X className="size-3" aria-hidden />
      )}
      Remove
      <span className="sr-only"> {fileName}</span>
    </button>
  );
}

export interface ClaimDetailLeafProps {
  claim: PortalClaim;
  /** The claim's conversation, oldest first. */
  messages: ClaimMessage[] | undefined;
  messagesLoading?: boolean;
  messagesError?: boolean;
  /** Omitted ⇒ the composer is replaced by `replyDisabledReason`. */
  onSend?: (body: string) => Promise<void> | void;
  sending?: boolean;
  replyDisabledReason?: string;
  /** Omitted ⇒ every attach control renders disabled. */
  onAddDocument?: (file: File | undefined, docType?: string) => void;
  uploading?: boolean;
  /** Omitted ⇒ the send-claim control renders disabled. */
  onSubmit?: () => void;
  submitting?: boolean;
  /** Open the edit sheet. Omitted ⇒ the Correct details control renders
   *  disabled, which is how the broker preview shows the member's affordance
   *  without being able to use it. */
  onEdit?: () => void;
  /** Remove one attached document. Omitted ⇒ no remove control at all — unlike
   *  the others this one is HIDDEN rather than disabled, because a dead
   *  destructive control beside a file is an invitation to keep clicking. */
  onRemoveDocument?: (docId: string) => void;
  removingDocumentId?: string | null;
  /** Replaces the whole edit/send block. The sheet the member is editing in,
   *  hoisted here by the route so this component stays free of state. */
  editing?: ReactNode;
  /** Title on every control this surface has disabled. */
  disabledTitle?: string;
  /** How to get back to the ledger. The route navigates; the preview pops its
   *  own drill-in, so neither can be assumed here. */
  back?: ReactNode;
  /** The just-submitted receipt. */
  receipt?: boolean;
  /** The claim's currency conversion, shown above the Send control.
   *
   *  Passed IN rather than read off `claim` because this same component is the
   *  broker's employee-view preview, which passes nothing — a broker looking at
   *  a member's screen is not the party the figure is being put to. */
  conversion?: ConversionState;
}

export interface ConversionState {
  state: "not_required" | "converted" | "unavailable";
  currency: string;
  policyCurrency: string;
  amountClaimed: number;
  converted: number | null;
  rate: number | null;
  rateDate: string | null;
  stale: boolean;
  /** Already accepted on a previous visit — nothing left to show. */
  acknowledged: boolean;
}

export function ClaimDetailLeaf({
  claim,
  messages,
  messagesLoading = false,
  messagesError = false,
  onSend,
  sending = false,
  replyDisabledReason,
  onAddDocument,
  uploading = false,
  onSubmit,
  submitting = false,
  onEdit,
  onRemoveDocument,
  removingDocumentId = null,
  editing,
  disabledTitle,
  back,
  receipt = false,
  conversion,
}: ClaimDetailLeafProps) {
  const fileInput = useRef<HTMLInputElement>(null);

  // SERVED, not derived. This was `status === "draft" || status ===
  // "needs_info"` — a mirror of a server rule, and the exact mirror that
  // stopped being true when the edit window widened to "until the broker
  // decides". The rule has one owner (`claims.member_editability`) and the
  // sentence explaining a refusal comes from the same place, so the member is
  // never given two accounts of why the form is shut.
  const editable = claim.member_editable;
  const slots = claim.required_doc_slots ?? [];
  const state = STATE[claim.status] ?? {
    label: claim.status,
    tone: "review" as const,
  };

  // Does the conversation already carry the broker's decision? The three
  // decision events are the ones whose body embeds `decision_notes`; a
  // "submitted" notice does not, so it must not suppress the fallback below.
  const decisionInThread = (messages ?? []).some(
    (m) =>
      m.event === "approved" ||
      m.event === "rejected" ||
      m.event === "needs_info",
  );

  // Which files answer which requirement. Mirrors the backend's
  // `assert_documents_satisfy_slots`: a specific slot needs a document tagged
  // with its key, and the generic invoice/receipt slot accepts ANY attachment —
  // so it also claims the untagged ones (that is how a plain upload satisfies
  // it), which is what lets each requirement be shown WITH the file meeting it
  // instead of in a second list beside them.
  const docsForSlot = (key: string) =>
    key === GENERIC_SLOT
      ? claim.documents.filter((d) => !d.doc_type || d.doc_type === GENERIC_SLOT)
      : claim.documents.filter((d) => d.doc_type === key);
  const slotSatisfied = (key: string) =>
    key === GENERIC_SLOT
      ? claim.documents.length > 0
      : claim.documents.some((d) => d.doc_type === key);
  const allSlotsSatisfied = slots.every((s) => slotSatisfied(s.key));
  const missing = slots.filter((s) => !slotSatisfied(s.key));
  // Anything no requirement claims. Listed after them so nothing a member
  // uploaded is invisible, but marked as extra so it can't be mistaken for a
  // requirement that has been met.
  const claimedIds = new Set(
    slots.flatMap((s) => docsForSlot(s.key).map((d) => d.id)),
  );
  const extraDocs = claim.documents.filter((d) => !claimedIds.has(d.id));

  const gloss =
    [
      // `sub_type` frequently REPEATS the claim type verbatim (both read
      // "Emergency Accidental Outpatient Treatment"), so it is dropped when it
      // adds nothing — a gloss that restates its own heading is worse than no
      // gloss.
      claim.sub_type && claim.sub_type.trim() !== claimTitle(claim).trim()
        ? claim.sub_type
        : null,
      claim.provider_name,
    ]
      .filter(Boolean)
      .join(" · ") || null;

  return (
    // Bounded measure, like the claim FORM above it (`max-w-xl`). This page is
    // one record read top to bottom, and at the shell's full 1024px three
    // things broke at once: every ledger row put ~700px of nothing between its
    // term and its value, the message bodies ran to ~150 characters a line
    // (double a comfortable measure), and a full-width primary pill read as a
    // banner. Widening a page is not the same as using the width.
    <div className="mx-auto max-w-3xl space-y-3">
      {back}

      {/* The receipt. A notice, never a second brand fill — it carries a strike
          in the pending ink and states what was sent and what happens next.
          It deliberately promises no turnaround: the AI check runs immediately
          but the decision is a person's, and prod cannot email a member yet. */}
      {receipt && (
        <Mount>
          <Strike tone="pending">Sent</Strike>
          <p className="text-md font-semibold text-record">We have your claim</p>
          <p className="text-row text-label">
            Everything you sent is listed below, and this page is where its
            status appears. You don&rsquo;t need to send it again — if we need
            anything else, this claim will say so and you can add it here.
          </p>
        </Mount>
      )}

      <Mount
        label={claimTitle(claim)}
        gloss={gloss}
        aside={
          <Strike tone={state.tone} animate>
            {state.label}
          </Strike>
        }
      >
        {/* Editing happens WHERE the values are read, not on a page of its
            own: the member is correcting one figure against a receipt in their
            hand, and a separate screen would take away the rest of the record
            they are checking it against. */}
        {editing ?? (
        <>
        {/* One column on a phone. The old two-up grid never collapsed, so a
            long diagnosis and a currency figure shared ~147px each. */}
        <dl className="divide-y divide-hairline/75">
          <MountRow term="Amount claimed">
            <Money value={claim.amount_claimed} currency={claim.currency} />
          </MountRow>
          {claim.amount_approved != null && (
            <MountRow term="Approved">
              <Money
                value={claim.amount_approved}
                currency={claim.currency}
                emphasis="strong"
              />
            </MountRow>
          )}
          <MountRow term="Date of treatment">
            {formatDay(claim.incurred_date)}
          </MountRow>
          {claim.dependant_name && (
            <MountRow term="Who it's for">{claim.dependant_name}</MountRow>
          )}
          {claim.doctor_name && (
            <MountRow term="Doctor seen">{claim.doctor_name}</MountRow>
          )}
          {claim.diagnosis && (
            <MountRow term="Diagnosis">{claim.diagnosis}</MountRow>
          )}
          {claim.invoice_number && (
            <MountRow term="Invoice number">{claim.invoice_number}</MountRow>
          )}
          {(claim.referral_document || claim.referral_not_applicable) && (
            <MountRow term="Referral letter">
              {claim.referral_document
                ? claim.referral_document.file_name
                : "Not needed"}
            </MountRow>
          )}
          {/* The visit this claim continues. Shown on BOTH detail surfaces —
              the member's and the broker's — because a link nobody can see is
              a link nobody can correct, and this is the one that decides
              whether the insurer pays the consultation at all. */}
          {claim.related_claim && (
            <MountRow term="Follows">
              {[
                claim.related_claim.provider_name?.trim(),
                claim.related_claim.admission_date &&
                claim.related_claim.discharge_date
                  ? `${formatDay(claim.related_claim.admission_date)} – ${formatDay(
                      claim.related_claim.discharge_date,
                    )}`
                  : formatDay(
                      claim.related_claim.admission_date ??
                        claim.related_claim.incurred_date,
                    ),
              ]
                .filter(Boolean)
                .join(" · ")}
            </MountRow>
          )}
          {claim.remarks && <MountRow term="Your note">{claim.remarks}</MountRow>}
        </dl>

        {/* A decision note normally arrives as a MESSAGE in the thread below —
            written at the moment of the decision, dated, and replyable — so
            printing it here as well would show the member the same sentence
            twice, only one of them answerable.

            But a claim DECIDED BEFORE messages existed has a note and no
            message to carry it, and dropping this block outright would have
            hidden every one of those notes from the member who was told to act
            on it. So it renders only when nothing in the thread already
            carries a decision. */}
        {claim.decision_notes && !decisionInThread && (
          <>
            <MountRule className="my-3" />
            <h3 className="leaf-label mb-1">Note from the team</h3>
            <p className="text-row text-record">{claim.decision_notes}</p>
          </>
        )}

        {/* Correcting the record the member is looking at. Quiet ink, not the
            page's brand fill — that belongs to sending the claim, which is
            what they came to do; fixing a mistyped figure is maintenance. */}
        {editable && (
          <>
            <MountRule className="my-3" />
            <Action
              type="button"
              block="phone"
              className="h-11"
              disabled={!onEdit}
              title={onEdit ? undefined : disabledTitle}
              onClick={onEdit}
            >
              <Pencil className="size-4" aria-hidden />
              Correct these details
            </Action>
          </>
        )}

        {/* Why the controls are gone. The server owns this sentence, so what
            they read here is what a refused request would have told them. */}
        {!editable && claim.member_edit_block && (
          <>
            <MountRule className="my-3" />
            <p className="text-row text-label">{claim.member_edit_block}</p>
          </>
        )}
        </>
        )}
      </Mount>

      {/* ── Documents ─────────────────────────────────────────────────────
          ONE list, keyed on what the claim REQUIRES.

          It used to be two: a file list, and beneath it a "what we still need"
          checklist whose ticked rows described those same files. So a
          one-document claim stated the same fact twice and put the Replace
          control in the second list, a rule and forty pixels away from the file
          it replaces. A requirement, the file that answers it and the control
          that changes that file are ONE thing, so they are one row.

          Files no requirement claims are still listed, marked as additional —
          nothing a member uploaded may be invisible, and nothing extra may be
          mistaken for a requirement that has been met. */}
      <Mount label={`Documents (${claim.documents.length})`}>
        {editable && slots.length > 0 ? (
          <ul className="divide-y divide-hairline/75">
            {slots.map((slot) => {
              const files = docsForSlot(slot.key);
              const done = files.length > 0;
              return (
                <li
                  key={slot.key}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5 first:pt-0"
                >
                  <span className="flex min-w-0 flex-1 items-start gap-2">
                    {done ? (
                      <Check
                        className="mt-0.5 size-4 shrink-0 text-strike-approved"
                        aria-hidden
                      />
                    ) : (
                      <FileText
                        className="mt-0.5 size-4 shrink-0 text-label"
                        aria-hidden
                      />
                    )}
                    <span className="min-w-0">
                      <span className="block text-row text-record">
                        {slot.label}
                      </span>
                      {/* The file IS the evidence the requirement is met, so it
                          reads as the row's own second line rather than as an
                          entry in a separate list. */}
                      {done ? (
                        files.map((f) => (
                          <span
                            key={f.id}
                            className="block break-all text-2xs text-label"
                          >
                            {f.file_name} · {(f.size_bytes / 1024).toFixed(0)} KB
                            <RemoveDocument
                              docId={f.id}
                              fileName={f.file_name}
                              busy={removingDocumentId === f.id}
                              onRemove={onRemoveDocument}
                            />
                          </span>
                        ))
                      ) : (
                        <span className="block text-2xs text-label">
                          Not attached yet
                        </span>
                      )}
                      {done && <span className="sr-only">(attached)</span>}
                    </span>
                  </span>
                  {/* Ink, not brand: a claim needing four documents would
                      otherwise carry four brand-coloured controls. */}
                  <AttachControl
                    done={done}
                    slotLabel={slot.label}
                    busy={uploading}
                    disabledTitle={disabledTitle}
                    onPick={
                      onAddDocument &&
                      ((f) =>
                        onAddDocument(
                          f,
                          slot.key === GENERIC_SLOT ? undefined : slot.key,
                        ))
                    }
                  />
                </li>
              );
            })}
            {extraDocs.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center gap-2 py-2.5 first:pt-0"
              >
                <FileText className="size-4 shrink-0 text-label" aria-hidden />
                <span className="min-w-0 break-all text-row text-record">
                  {doc.file_name}
                </span>
                <span className="shrink-0 text-2xs tabular-nums text-label">
                  {(doc.size_bytes / 1024).toFixed(0)} KB · additional
                  <RemoveDocument
                    docId={doc.id}
                    fileName={doc.file_name}
                    busy={removingDocumentId === doc.id}
                    onRemove={onRemoveDocument}
                  />
                </span>
              </li>
            ))}
          </ul>
        ) : claim.documents.length > 0 ? (
          // Nothing to satisfy (the claim is sent, or carries no slots), so
          // this is a plain manifest: name and size together, because a file is
          // one object and pinning its size to the far edge of the column put
          // ~900px of dead space through the middle of a one-item list.
          <ul className="divide-y divide-hairline/75">
            {claim.documents.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center gap-2.5 py-2 first:pt-0"
              >
                <FileText className="size-4 shrink-0 text-label" aria-hidden />
                <span className="min-w-0 break-all text-row text-record">
                  {doc.file_name}
                </span>
                <span className="shrink-0 text-row tabular-nums text-label">
                  {(doc.size_bytes / 1024).toFixed(0)} KB
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-row text-label">Nothing attached yet.</p>
        )}

        {editable && (
          <>
            {/* No divider. These two act ON the list directly above them, and a
                rule between them said they were a separate concern. The mount's
                `gap-3` is the whole separation they need — and a rule inside a
                `flex flex-col gap-3` costs 48px of run (12 gap + 12 + 12 + 12
                gap), which is what made this card twice the height of every
                other one in the portal. */}
            {onAddDocument && (
              <input
                ref={fileInput}
                type="file"
                accept={CLAIM_DOC_ACCEPT}
                className="sr-only"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (fileInput.current) fileInput.current.value = "";
                  onAddDocument(f);
                }}
              />
            )}
            {/* ONE action row, in task order: add what's missing, then send.
                Both are `block: "phone"` — full width on a phone, natural width
                from `sm` up. A brand pill stretched across a 1024px column is a
                BANNER, not a button (leaf/Action.tsx says so in as many words),
                and stacking a natural-width pill above a full-width one left
                two controls of the same pair at two different widths with
                nothing aligning them. */}
            <div className="space-y-2">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                {/* `h-12` matches the primary's height. The two tones are
                    normally used apart, where `quiet`'s 44px is right; sharing
                    one row, a 4px difference reads as a mistake rather than as
                    hierarchy — which the fill is already carrying. */}
                <Action
                  type="button"
                  block="phone"
                  className="h-12"
                  disabled={uploading || !onAddDocument}
                  title={onAddDocument ? undefined : disabledTitle}
                  onClick={() => fileInput.current?.click()}
                >
                  {uploading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Paperclip className="size-4" aria-hidden />
                  )}
                  Add another document
                </Action>
                {/* The page's one brand fill — sending the claim is what the
                    member came here to do.

                    Gated on `member_can_submit`, NOT on `editable`. The two
                    were one flag until editing stayed open after submission,
                    at which point every claim in the queue grew a Send button
                    that could only 409: a claim in review is theirs to CORRECT
                    but not theirs to send again. */}
                {claim.member_can_submit && (
                  <Action
                    tone="primary"
                    block="phone"
                    disabled={submitting || !allSlotsSatisfied || !onSubmit}
                    title={onSubmit ? undefined : disabledTitle}
                    onClick={onSubmit}
                  >
                    {submitting ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <Send className="size-4" aria-hidden />
                    )}
                    {claim.status === "needs_info"
                      ? "Send again"
                      : "Send this claim"}
                  </Action>
                )}
              </div>
              {/* Naming what's missing beats a disabled button with no
                  explanation — the member can't otherwise tell whether it's
                  broken or waiting on them. Only alongside a Send control: on
                  a claim already in review it is not what they are waiting to
                  do, and the assessor will ask if something is missing. */}
              {claim.member_can_submit && !allSlotsSatisfied && (
                <p className="text-row text-label">
                  Attach {missing.map((s) => s.label).join(" and ")} first.
                </p>
              )}
              {/* What the claim converts to. Placed beside the Send control
                  rather than up beside the amount: pressing Send accepts this
                  figure, so it belongs where that decision is made. */}
              {claim.member_can_submit &&
                conversion &&
                conversion.state !== "not_required" &&
                !conversion.acknowledged && (
                  <ConversionNotice
                    quote={{
                      currency: conversion.currency,
                      policy_currency: conversion.policyCurrency,
                      amount: conversion.amountClaimed,
                      converted: conversion.converted,
                      rate: conversion.rate,
                      as_of_date: null,
                      rate_date: conversion.rateDate,
                      stale: conversion.stale,
                      source: null,
                      available: conversion.state === "converted",
                      note:
                        conversion.state === "converted"
                          ? conversion.stale && conversion.rateDate
                            ? `Using the rate published on ${conversion.rateDate} — none is published for ${claim.incurred_date}.`
                            : null
                          : null,
                    }}
                    loading={false}
                    currency={conversion.currency}
                    policyCurrency={conversion.policyCurrency}
                  />
                )}
            </div>
          </>
        )}
      </Mount>

      {/* ── Messages ──────────────────────────────────────────────────────
          The conversation about THIS claim: what we told them, when, and their
          replies. A draft has no thread — nothing has been sent, so there is
          nobody at the other end — and the mount says so rather than showing an
          empty box with a Send button that would 409. */}
      <Mount label="Messages">
        {messagesLoading ? (
          <p className="text-row text-label">Loading&hellip;</p>
        ) : messagesError ? (
          <p className="text-row text-label">
            We couldn&rsquo;t load the messages on this claim just now.
          </p>
        ) : (
          <MessageThread
            messages={messages ?? []}
            sending={sending}
            // So an automatic notice does not reprint the claim's own name as
            // its subject directly under the heading that already carries it.
            threadSubject={claimTitle(claim)}
            replyDisabledReason={replyDisabledReason}
            onSend={onSend}
          />
        )}
      </Mount>
    </div>
  );
}
