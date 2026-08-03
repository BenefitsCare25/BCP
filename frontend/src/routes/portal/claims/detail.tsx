/** One claim: what it was for, where it got to, and what (if anything) the
 * member has to do next.
 *
 * This is the only screen where the strike ANIMATES. A verdict is the point of
 * the page, there is exactly one, and it arrives asynchronously — the three
 * conditions under which motion carries information rather than decorating.
 * The list deliberately renders its strikes complete. */
import { useEffect, useRef } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { ArrowLeft, Check, FileText, Loader2, Paperclip, Send } from "lucide-react";
import { toast } from "sonner";
import {
  usePortalClaim,
  useSubmitClaim,
  useUploadClaimDocument,
} from "@/api/portal";
import {
  useMarkClaimMessagesRead,
  usePortalClaimMessages,
  useSendClaimMessage,
} from "@/api/portalMessages";
import { MessageThread } from "@/components/portal/leaf/MessageMount";
import { Mount, MountRow, MountRule } from "@/components/portal/leaf/Mount";
import { Money } from "@/components/portal/leaf/Figure";
import { Strike } from "@/components/portal/leaf/Strike";
import { Action } from "@/components/portal/leaf/Action";
import { claimTitle } from "@/components/portal/leaf/ClaimMount";
import { formatDay } from "@/components/portal/leaf/date";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { formatError, isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

const ACCEPT = ".pdf,.png,.jpg,.jpeg";
const MAX_BYTES = 15 * 1024 * 1024;

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
 * say so with the controls attached. */
const STATE: Record<string, { label: string; tone: "approved" | "pending" | "review" | "rejected" }> = {
  draft: { label: "Not sent", tone: "review" },
  submitted: { label: "Under review", tone: "review" },
  ai_review_pending: { label: "Under review", tone: "review" },
  ai_verified: { label: "Under review", tone: "review" },
  ai_flagged: { label: "Under review", tone: "review" },
  needs_info: { label: "More info needed", tone: "pending" },
  approved: { label: "Approved", tone: "approved" },
  rejected: { label: "Rejected", tone: "rejected" },
};

export function PortalClaimDetailPage() {
  const { claimId } = useParams({ strict: false }) as { claimId: string };
  // THE RECEIPT. Submitting used to end in a three-second toast: no statement
  // of what was sent, no document manifest, nothing to come back to. The claim's
  // own page already holds every one of those, so the receipt is this page
  // arriving with `?submitted=true` rather than a screen of its own — which
  // also means a member who reopens the claim a week later reads the same
  // record, not a different one.
  const search = useSearch({ strict: false }) as { submitted?: boolean };
  const justSubmitted = search.submitted === true;
  const navigate = useNavigate();
  const company = useCompany();
  const claim = usePortalClaim(claimId);
  const messages = usePortalClaimMessages(claimId);
  const sendMessage = useSendClaimMessage();
  const markRead = useMarkClaimMessagesRead();
  const uploadDoc = useUploadClaimDocument();
  const submitClaim = useSubmitClaim();
  const fileInput = useRef<HTMLInputElement>(null);
  useDocumentTitle(claim.data ? claimTitle(claim.data) : "Claim");

  // Opening the claim IS opening the thread — it is rendered in full below, so
  // marking it read here is honest rather than optimistic. Gated on there being
  // something unread so a member re-reading a settled claim doesn't fire a
  // write (and three invalidations) on every visit. `markRead.mutate`, not
  // `mutateAsync`: a failed read receipt must not surface anything to the
  // member — the worst case is a badge that clears on the next visit.
  const thread = messages.data ?? [];
  const unreadHere = thread.some((m) => m.unread);
  // Does the conversation already carry the broker's decision? The three
  // decision events are the ones whose body embeds `decision_notes`; a
  // "submitted" notice does not, so it must not suppress the fallback below.
  const decisionInThread = thread.some(
    (m) =>
      m.event === "approved" ||
      m.event === "rejected" ||
      m.event === "needs_info",
  );
  const markMutate = markRead.mutate;
  useEffect(() => {
    if (claimId && unreadHere) markMutate(claimId);
  }, [claimId, unreadHere, markMutate]);

  if (claim.isLoading) return <LeafSkeleton label="Loading your claim" mounts={2} />;
  // Only a real 404 means the claim doesn't exist — any other failure gets a
  // retryable error state instead of a misleading "not found".
  if (claim.isError && !isNotFoundError(claim.error)) {
    return <PortalErrorState onRetry={() => void claim.refetch()} />;
  }
  if (claim.isError || !claim.data) {
    return (
      <Mount label="We couldn't find that claim">
        <p className="text-row text-label">
          It may have been removed. Your other claims are on the claims page.
        </p>
      </Mount>
    );
  }

  const data = claim.data;
  const editable = data.status === "draft" || data.status === "needs_info";
  const slots = data.required_doc_slots ?? [];
  const state = STATE[data.status] ?? {
    label: data.status,
    tone: "review" as const,
  };

  // Which files answer which requirement. Mirrors the backend's
  // `assert_documents_satisfy_slots`: a specific slot needs a document tagged
  // with its key, and the generic invoice/receipt slot accepts ANY attachment —
  // so it also claims the untagged ones (that is how a plain upload satisfies
  // it), which is what lets each requirement be shown WITH the file meeting it
  // instead of in a second list beside them.
  const docsForSlot = (key: string) =>
    key === GENERIC_SLOT
      ? data.documents.filter((d) => !d.doc_type || d.doc_type === GENERIC_SLOT)
      : data.documents.filter((d) => d.doc_type === key);
  const slotSatisfied = (key: string) =>
    key === GENERIC_SLOT
      ? data.documents.length > 0
      : data.documents.some((d) => d.doc_type === key);
  const allSlotsSatisfied = slots.every((s) => slotSatisfied(s.key));
  const missing = slots.filter((s) => !slotSatisfied(s.key));
  // Anything no requirement claims. Listed after them so nothing a member
  // uploaded is invisible, but marked as extra so it can't be mistaken for a
  // requirement that has been met.
  const claimedIds = new Set(slots.flatMap((s) => docsForSlot(s.key).map((d) => d.id)));
  const extraDocs = data.documents.filter((d) => !claimedIds.has(d.id));

  const addDocument = async (file: File | undefined, docType?: string) => {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      toast.error(`${file.name} is larger than 15 MB. Try a smaller photo or scan.`);
      return;
    }
    try {
      await uploadDoc.mutateAsync({ claimId: data.id, file, docType });
      await claim.refetch();
      toast.success("Added");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  // Resending stays on the claim and raises the receipt, rather than bouncing
  // to the list with a toast: the member has just added what was asked for, and
  // the thing they need to see is that THIS claim now has it.
  const resubmit = async () => {
    try {
      await submitClaim.mutateAsync(data.id);
      await claim.refetch();
      void navigate({
        to: "/portal/$company/claims/$claimId",
        params: { company, claimId: data.id },
        search: { submitted: true },
        replace: true,
      });
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    // Bounded measure, like the claim FORM above it (`max-w-xl`). This page is
    // one record read top to bottom, and at the shell's full 1024px three
    // things broke at once: every ledger row put ~700px of nothing between its
    // term and its value, the message bodies ran to ~150 characters a line
    // (double a comfortable measure), and a full-width primary pill read as a
    // banner. Widening a page is not the same as using the width.
    <div className="mx-auto max-w-3xl space-y-3">
      <button
        type="button"
        onClick={() => void navigate({ to: "/portal/$company/claims", params: { company } })}
        className="leaf-focus -ml-2 inline-flex min-h-11 items-center gap-1.5 px-2 text-row text-label"
      >
        <ArrowLeft className="size-4" aria-hidden /> All claims
      </button>

      {/* The receipt. A notice, never a second brand fill — it carries a strike
          in the pending ink and states what was sent and what happens next.
          It deliberately promises no turnaround: the AI check runs immediately
          but the decision is a person's, and prod cannot email a member yet. */}
      {justSubmitted && (
        <Mount>
          <Strike tone="pending">Sent</Strike>
          <p className="text-md font-semibold text-record">
            We have your claim
          </p>
          <p className="text-row text-label">
            Everything you sent is listed below, and this page is where its
            status appears. You don&rsquo;t need to send it again — if we need
            anything else, this claim will say so and you can add it here.
          </p>
        </Mount>
      )}

      <Mount
        label={claimTitle(data)}
        // `sub_type` frequently REPEATS the claim type verbatim (both read
        // "Emergency Accidental Outpatient Treatment"), so it is dropped when
        // it adds nothing — a gloss that restates its own heading is worse
        // than no gloss.
        gloss={
          [
            data.sub_type && data.sub_type.trim() !== claimTitle(data).trim()
              ? data.sub_type
              : null,
            data.provider_name,
          ]
            .filter(Boolean)
            .join(" · ") || null
        }
        aside={
          <Strike tone={state.tone} animate>
            {state.label}
          </Strike>
        }
      >
        {/* One column on a phone. The old two-up grid never collapsed, so a
            long diagnosis and a currency figure shared ~147px each. */}
        <dl className="divide-y divide-hairline/75">
          <MountRow term="Amount claimed">
            <Money value={data.amount_claimed} currency={data.currency} />
          </MountRow>
          {data.amount_approved != null && (
            <MountRow term="Approved">
              <Money
                value={data.amount_approved}
                currency={data.currency}
                emphasis="strong"
              />
            </MountRow>
          )}
          <MountRow term="Date of treatment">{formatDay(data.incurred_date)}</MountRow>
          {data.dependant_name && (
            <MountRow term="Who it's for">{data.dependant_name}</MountRow>
          )}
          {data.diagnosis && <MountRow term="Diagnosis">{data.diagnosis}</MountRow>}
          {data.invoice_number && (
            <MountRow term="Invoice number">{data.invoice_number}</MountRow>
          )}
          {(data.referral_document || data.referral_not_applicable) && (
            <MountRow term="Referral letter">
              {data.referral_document
                ? data.referral_document.file_name
                : "Not needed"}
            </MountRow>
          )}
          {data.remarks && <MountRow term="Your note">{data.remarks}</MountRow>}
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
        {data.decision_notes && !decisionInThread && (
          <>
            <MountRule className="my-3" />
            <h3 className="leaf-label mb-1">Note from the team</h3>
            <p className="text-row text-record">{data.decision_notes}</p>
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
      <Mount label={`Documents (${data.documents.length})`}>
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
                  <label className="leaf-focus inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-1.5 rounded-control border border-leaf-input bg-bar/80 px-3 text-row font-medium text-record">
                    <Paperclip className="size-4 shrink-0" aria-hidden />
                    {done ? "Replace" : "Attach"}
                    <span className="sr-only"> {slot.label}</span>
                    <input
                      type="file"
                      accept={ACCEPT}
                      className="sr-only"
                      disabled={uploadDoc.isPending}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        void addDocument(
                          f,
                          slot.key === GENERIC_SLOT ? undefined : slot.key,
                        );
                      }}
                    />
                  </label>
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
                </span>
              </li>
            ))}
          </ul>
        ) : data.documents.length > 0 ? (
          // Nothing to satisfy (the claim is sent, or carries no slots), so
          // this is a plain manifest: name and size together, because a file is
          // one object and pinning its size to the far edge of the column put
          // ~900px of dead space through the middle of a one-item list.
          <ul className="divide-y divide-hairline/75">
            {data.documents.map((doc) => (
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
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (fileInput.current) fileInput.current.value = "";
                void addDocument(f);
              }}
            />
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
                  disabled={uploadDoc.isPending}
                  onClick={() => fileInput.current?.click()}
                >
                  {uploadDoc.isPending ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Paperclip className="size-4" aria-hidden />
                  )}
                  Add another document
                </Action>
                {/* The page's one brand fill — sending the claim is what the
                    member came here to do. */}
                <Action
                  tone="primary"
                  block="phone"
                  disabled={submitClaim.isPending || !allSlotsSatisfied}
                  onClick={() => void resubmit()}
                >
                  {submitClaim.isPending ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Send className="size-4" aria-hidden />
                  )}
                  {data.status === "needs_info" ? "Send again" : "Send this claim"}
                </Action>
              </div>
              {/* Naming what's missing beats a disabled button with no
                  explanation — the member can't otherwise tell whether it's
                  broken or waiting on them. */}
              {!allSlotsSatisfied && (
                <p className="text-row text-label">
                  Attach {missing.map((s) => s.label).join(" and ")} first.
                </p>
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
        {messages.isLoading ? (
          <p className="text-row text-label">Loading&hellip;</p>
        ) : messages.isError ? (
          <p className="text-row text-label">
            We couldn&rsquo;t load the messages on this claim just now.
          </p>
        ) : (
          <MessageThread
            messages={messages.data ?? []}
            sending={sendMessage.isPending}
            replyDisabledReason={
              data.status === "draft"
                ? "Send this claim and you'll be able to write to us about it here."
                : undefined
            }
            onSend={
              data.status === "draft"
                ? undefined
                : async (body) => {
                    try {
                      await sendMessage.mutateAsync({ claimId: data.id, body });
                    } catch (err) {
                      toast.error(formatError(err));
                      // Re-thrown so the composer KEEPS the text: clearing a
                      // message the member typed because the request failed is
                      // the one outcome they can't recover from.
                      throw err;
                    }
                  }
            }
          />
        )}
      </Mount>
    </div>
  );
}
