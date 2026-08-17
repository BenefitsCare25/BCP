/** One claim: what it was for, where it got to, and what (if anything) the
 * member has to do next.
 *
 * The page BODY is `leaf/ClaimDetailLeaf` — shared with the broker's
 * employee-view preview, so the two can never drift into showing a member's
 * record differently. What lives here is everything the preview must not have:
 * the mutations, the router, and the read receipt.
 *
 * The claim's strike is the only one in the portal that ANIMATES. A verdict is
 * the point of the page, there is exactly one, and it arrives asynchronously —
 * the three conditions under which motion carries information rather than
 * decorating. The list deliberately renders its strikes complete.
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import {
  downloadPortalClaimDocument,
  useAmendClaim,
  useDeleteClaimDocument,
  usePortalClaim,
  useConfirmConversion,
  useSubmitClaim,
  useUploadClaimDocument,
  type ClaimAmendInput,
} from "@/api/portal";
import { ClaimEditSheet } from "@/components/portal/claims/ClaimEditSheet";
import {
  useMarkClaimMessagesRead,
  usePortalClaimMessages,
  useSendClaimMessage,
} from "@/api/portalMessages";
import {
  ClaimDetailLeaf,
  CLAIM_DOC_MAX_BYTES,
} from "@/components/portal/leaf/ClaimDetailLeaf";
import { Mount } from "@/components/portal/leaf/Mount";
import { claimTitle } from "@/components/portal/leaf/ClaimMount";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { formatError, isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

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
  const confirmConversion = useConfirmConversion();
  const amendClaim = useAmendClaim();
  const removeDoc = useDeleteClaimDocument();
  // The edit sheet's open state, and the SERVER's last word on the attempt.
  // The error is held here rather than toasted because it belongs beside the
  // field it is about — a duplicate invoice number or an out-of-period date is
  // something to fix in the form, not an event to acknowledge and dismiss.
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [removingDocId, setRemovingDocId] = useState<string | null>(null);
  // The revision the member has SEEN, which is NOT simply the latest one.
  //
  // Every document they attach or remove bumps the claim's revision server-side
  // (`stamp_document_amendment`) — and "fix the figure AND replace the receipt"
  // is the ordinary correction, with the Documents card sitting live below the
  // open sheet. So the sheet cannot key its concurrency token off the current
  // revision (a broker's correction would then be overwritten unseen) NOR off
  // the one it opened on (the member's own upload would 409 them out of their
  // own edit). It tracks the bumps THEY caused: set when the sheet opens,
  // advanced by their own document changes, and left behind by anyone else's.
  //
  // For the same reason the sheet is NOT remounted on `data.revision`. It was,
  // and attaching a receipt mid-edit silently discarded everything typed — the
  // form re-captured its baseline from the refetch with nothing on screen to
  // say so. It unmounts on close, which is all the reset a reopen needs.
  const [seenRevision, setSeenRevision] = useState(0);
  useDocumentTitle(claim.data ? claimTitle(claim.data) : "Claim");

  // Opening the claim IS opening the thread — it is rendered in full below, so
  // marking it read here is honest rather than optimistic. Gated on there being
  // something unread so a member re-reading a settled claim doesn't fire a
  // write (and three invalidations) on every visit. `markRead.mutate`, not
  // `mutateAsync`: a failed read receipt must not surface anything to the
  // member — the worst case is a badge that clears on the next visit.
  //
  // This is deliberately NOT in `ClaimDetailLeaf`: the broker's preview renders
  // that same body, and a broker looking at a member's screen must never clear
  // the member's own unread mark.
  const unreadHere = (messages.data ?? []).some((m) => m.unread);
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

  /** Refetch, and record the resulting revision as one the member has seen —
   *  for use after an action THEY took. */
  const refetchAsSeen = async () => {
    const next = await claim.refetch();
    if (next.data) setSeenRevision(next.data.revision);
  };

  const addDocument = async (file: File | undefined, docType?: string) => {
    if (!file) return;
    if (file.size > CLAIM_DOC_MAX_BYTES) {
      toast.error(`${file.name} is larger than 15 MB. Try a smaller photo or scan.`);
      return;
    }
    try {
      await uploadDoc.mutateAsync({ claimId: data.id, file, docType });
      await refetchAsSeen();
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
      // A foreign claim the member has not yet accepted the conversion on is
      // refused by submit (409 `fx_confirmation_required`). Accepted here
      // rather than surfaced as an error: the figure is ON THIS PAGE, directly
      // above the button they just pressed, so pressing it IS the acceptance —
      // the same rule the new-claim form follows. The server still checks the
      // figure against its own before recording it.
      if (data.fx_state === "converted" && !data.fx_acknowledged_at) {
        await confirmConversion.mutateAsync({
          claimId: data.id,
          convertedAmount: data.amount_converted ?? 0,
        });
      }
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

  const isDraft = data.status === "draft";

  const saveEdit = async (patch: ClaimAmendInput) => {
    setEditError(null);
    try {
      await amendClaim.mutateAsync({ claimId: data.id, patch });
      await refetchAsSeen();
      setEditing(false);
      toast.success("Updated");
    } catch (err) {
      // Stays in the sheet, holding what they typed. The server's sentence is
      // the one they need — a duplicate invoice number, a date outside the
      // policy year, a claim someone decided while the sheet was open.
      setEditError(formatError(err));
      // And the page BEHIND the sheet is refreshed, deliberately without
      // advancing `seenRevision`. A refusal is often a refusal ABOUT the record
      // ("this claim was changed after you opened it" / "a broker has decided
      // it"), and without this the member was reading a stale claim under an
      // error explaining it — with no way back but leaving the page. What they
      // typed is untouched; only the record below it catches up.
      void claim.refetch();
    }
  };

  const removeDocument = async (docId: string) => {
    setRemovingDocId(docId);
    try {
      await removeDoc.mutateAsync({ claimId: data.id, docId });
      await refetchAsSeen();
      toast.success("Removed");
    } catch (err) {
      toast.error(formatError(err));
    } finally {
      setRemovingDocId(null);
    }
  };

  return (
    <ClaimDetailLeaf
      claim={data}
      messages={messages.data}
      messagesLoading={messages.isLoading}
      messagesError={messages.isError}
      back={
        <button
          type="button"
          onClick={() =>
            void navigate({ to: "/portal/$company/claims", params: { company } })
          }
          className="leaf-focus -ml-2 inline-flex min-h-11 items-center gap-1.5 px-2 text-row text-label"
        >
          <ArrowLeft className="size-4" aria-hidden /> All claims
        </button>
      }
      receipt={justSubmitted}
      onAddDocument={(file, docType) => void addDocument(file, docType)}
      uploading={uploadDoc.isPending}
      onSubmit={() => void resubmit()}
      conversion={{
        state: data.fx_state,
        currency: data.currency,
        policyCurrency: data.policy_currency,
        amountClaimed: data.amount_claimed,
        converted: data.amount_converted,
        rate: data.fx_rate,
        rateDate: data.fx_rate_date,
        stale: data.fx_stale,
        acknowledged: data.fx_acknowledged_at !== null,
      }}
      submitting={submitClaim.isPending}
      onEdit={() => {
        setEditError(null);
        setSeenRevision(data.revision);
        setEditing(true);
      }}
      onRemoveDocument={(docId) => void removeDocument(docId)}
      onDownloadDocument={(docId) => {
        const document = data.documents.find((item) => item.id === docId);
        if (!document) return;
        void downloadPortalClaimDocument(data.id, document).catch((error) => {
          toast.error(formatError(error));
        });
      }}
      removingDocumentId={removingDocId}
      editing={
        editing ? (
          <ClaimEditSheet
            claim={data}
            expectedRevision={seenRevision}
            saving={amendClaim.isPending}
            error={editError}
            onSave={(patch) => void saveEdit(patch)}
            onCancel={() => {
              setEditError(null);
              setEditing(false);
            }}
          />
        ) : undefined
      }
      sending={sendMessage.isPending}
      replyDisabledReason={
        isDraft
          ? "Send this claim and you'll be able to write to us about it here."
          : undefined
      }
      onSend={
        isDraft
          ? undefined
          : async (body) => {
              try {
                await sendMessage.mutateAsync({ claimId: data.id, body });
              } catch (err) {
                toast.error(formatError(err));
                // Re-thrown so the composer KEEPS the text: clearing a message
                // the member typed because the request failed is the one
                // outcome they can't recover from.
                throw err;
              }
            }
      }
    />
  );
}
