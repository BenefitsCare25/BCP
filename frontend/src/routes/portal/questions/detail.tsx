/** One question: what was asked, what we said back, and the box to write again.
 *
 * The whole body is `leaf/ThreadPane`'s question pane — the SAME component the
 * Messages stage renders — so a conversation reads identically whether it was
 * opened beside the index on a laptop or pushed as its own screen on a phone.
 * This route is now only the things a pane must not own: the router, the back
 * link, and the document title.
 *
 * It used to carry its own copy of the header, the state vocabulary, the read
 * receipt and the send handler. That is where the doubled title came from —
 * the frame printed the subject and the answer beneath it printed the same
 * string again as its own subject line, because only one of the two copies knew
 * about the other.
 *
 * A CLOSED question has no composer. It was ended deliberately, and a reply
 * landing in it would sit unread with nobody expecting it — the server refuses
 * for the same reason, so a visible box would be a control that 409s. That rule
 * lives in the pane, with the send it governs.
 */
import { useNavigate, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { usePortalEnquiry } from "@/api/portalEnquiries";
import { EnquiryThreadPane } from "@/components/portal/leaf/ThreadPane";
import { useCompany } from "@/components/portal/useCompany";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function PortalQuestionDetailPage() {
  const { enquiryId } = useParams({ strict: false }) as { enquiryId: string };
  const navigate = useNavigate();
  const company = useCompany();
  // The header data is fetched by the pane; this reads the same cached query
  // for the tab title rather than threading it back out of the pane.
  const enquiry = usePortalEnquiry(enquiryId);
  useDocumentTitle(enquiry.data?.subject ?? "Question");

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <button
        type="button"
        onClick={() =>
          void navigate({ to: "/portal/$company/messages", params: { company } })
        }
        className="leaf-focus -ml-2 inline-flex min-h-11 items-center gap-1.5 px-2 text-row text-label"
      >
        <ArrowLeft className="size-4" aria-hidden /> Messages
      </button>

      <EnquiryThreadPane enquiryId={enquiryId} />
    </div>
  );
}
