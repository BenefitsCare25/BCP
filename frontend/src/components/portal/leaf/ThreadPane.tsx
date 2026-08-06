/** The STAGE beside the message index: one conversation, read where it was
 * picked.
 *
 * ── Why this exists, and why the rule it replaces was half right ────────────
 *
 * The Messages page used to be an index that opened its rows by NAVIGATING to
 * the thing they were about, on the stated grounds that "answering *what is
 * this about?* needs the claim beside it — the amount, the documents, whether
 * anything is being asked of them", and that a second reading surface would be
 * "the same conversation in two places, each able to be read while the other
 * still shows unread".
 *
 * The first half is true and this pane honours it: the claim's identity —
 * title, date, amount, provider, state — is printed ABOVE the thread, and the
 * claim's own page is one control away for the things a page is needed for
 * (documents, resending). What the navigation actually did was throw the index
 * away to show a column of text in the middle of an otherwise empty window, and
 * make every reply cost two navigations.
 *
 * The second half was never a design law, it was a cache worry — and the code
 * had already answered it. The claim page and this pane read the SAME query
 * keys through the SAME hooks and mark read through the same mutation, so React
 * Query dedupes them; two surfaces cannot hold different unread state for one
 * thread unless someone writes a second fetch. Don't.
 *
 * ── What must stay true ─────────────────────────────────────────────────────
 *
 * **Marking read belongs to the CONTAINERS in this file, never to the pane.**
 * These are mounted only on the member's own inbox. `ClaimDetailLeaf` keeps the
 * same split for the same reason — the broker's employee-view preview renders
 * the member's screens and must never clear the member's own unread mark.
 *
 * **A question's pane has no claim to open**, so it carries the reference it
 * names as text. The question does not live on that claim and that claim's
 * thread is untouched by it.
 */
import { useEffect } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { toast } from "sonner";
import {
  useMarkEnquiryMessagesRead,
  usePortalEnquiry,
  usePortalEnquiryMessages,
  useSendEnquiryMessage,
} from "@/api/portalEnquiries";
import {
  useMarkClaimMessagesRead,
  usePortalClaimMessages,
  useSendClaimMessage,
} from "@/api/portalMessages";
import { usePortalClaim } from "@/api/portal";
import { useCompany } from "@/components/portal/useCompany";
import { formatError, isNotFoundError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import { Mount } from "./Mount";
import { MessageThread } from "./MessageMount";
import {
  EnquiryStrike,
  SubjectStrike,
  subjectTitle,
} from "./ConversationMount";
import { ClaimStrike, claimBucket } from "./Strike";
import { claimTitle } from "./ClaimMount";
import { currencySymbol, moneyText } from "./Figure";
import { formatDay } from "./date";
import { goLinkClass } from "./Action";
import type { ConversationSubject } from "@/api/portalMessages";

/** The shell every conversation is read in: who/what it is, then the thread.
 *
 * `aside` carries the state and `context` the line that tells this thread from
 * a sibling of the same name — the same two facts the index row carries, so
 * picking a row and reading it are visibly the same object. */
function Pane({
  title,
  context,
  aside,
  link,
  children,
}: {
  title: string;
  context?: string;
  aside?: React.ReactNode;
  link?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Mount label={title} gloss={context} aside={aside} rise={false}>
      {link}
      {children}
    </Mount>
  );
}

/** Nothing picked yet — only reachable at the width where a stage exists, so it
 * names the gesture that fills it rather than apologising for being empty. */
export function ThreadPanePlaceholder() {
  return (
    <Mount label="Pick a conversation" rise={false}>
      <p className="text-row text-label">
        Choose one on the left to read it here and write back.
      </p>
    </Mount>
  );
}

function PaneNotice({ label, detail }: { label: string; detail: string }) {
  return (
    <Mount label={label} rise={false}>
      <p className="text-row text-label">{detail}</p>
    </Mount>
  );
}

/** One claim's conversation, with the claim's own identity above it. */
export function ClaimThreadPane({ claimId }: { claimId: string }) {
  const company = useCompany();
  const claim = usePortalClaim(claimId);
  const messages = usePortalClaimMessages(claimId);
  const send = useSendClaimMessage();
  const markRead = useMarkClaimMessagesRead();

  // Reading the pane IS reading the thread — it is rendered in full below.
  // Gated on there being something unread so re-opening a settled claim doesn't
  // fire a write (and three invalidations) on every pick. `mutate`, not
  // `mutateAsync`: a failed receipt must surface nothing to the member; the
  // worst case is a badge that clears on the next visit.
  const unreadHere = (messages.data ?? []).some((m) => m.unread);
  const markMutate = markRead.mutate;
  useEffect(() => {
    if (claimId && unreadHere) markMutate(claimId);
  }, [claimId, unreadHere, markMutate]);

  if (claim.isLoading) {
    return <PaneNotice label="Loading" detail="Fetching this conversation…" />;
  }
  if (claim.isError || !claim.data) {
    return (
      <PaneNotice
        label={
          isNotFoundError(claim.error)
            ? "We couldn't find that claim"
            : "We couldn't load this conversation"
        }
        detail={
          isNotFoundError(claim.error)
            ? "It may have been removed. Your other claims are on the claims page."
            : "Try picking it again in a moment."
        }
      />
    );
  }

  const data = claim.data;
  const title = claimTitle(data);
  const context = [
    formatDay(data.incurred_date),
    `${currencySymbol(data.currency)}${moneyText(data.amount_claimed)}`,
    data.provider_name,
  ]
    .filter(Boolean)
    .join(" · ");
  const isDraft = data.status === "draft";
  // A claim that is waiting on the MEMBER is the one case where opening the
  // claim is the point of the message, so the link says what it is for.
  const wants = claimBucket(data.status) === "attention";

  return (
    <Pane
      title={title}
      context={context}
      aside={<ClaimStrike status={data.status} />}
      link={
        <Link
          to="/portal/$company/claims/$claimId"
          params={{ company, claimId }}
          className={cn(goLinkClass(), "self-start")}
        >
          {wants ? "Open the claim to add what's missing" : "Open the claim"}
          <ArrowRight className="size-4" aria-hidden />
        </Link>
      }
    >
      <MessageThread
        messages={messages.data ?? []}
        sending={send.isPending}
        threadSubject={title}
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
                  await send.mutateAsync({ claimId, body });
                } catch (err) {
                  toast.error(formatError(err));
                  // Re-thrown so the composer KEEPS the text — the one failure
                  // here a member cannot recover from.
                  throw err;
                }
              }
        }
      />
    </Pane>
  );
}

/** A question's line of context: what it is filed under, and the claim it names
 * if it names one. Shared with the index row's `identityLine` in spirit but not
 * in code — the row abbreviates, the pane can afford the full sentence. */
function enquiryContext(
  topicLabel: string | null,
  about: ConversationSubject | null,
): string {
  return [
    topicLabel,
    about ? `About ${subjectTitle(about)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function EnquiryThreadPane({ enquiryId }: { enquiryId: string }) {
  const enquiry = usePortalEnquiry(enquiryId);
  const messages = usePortalEnquiryMessages(enquiryId);
  const send = useSendEnquiryMessage();
  const markRead = useMarkEnquiryMessagesRead();

  const unreadHere = (messages.data ?? []).some((m) => m.unread);
  const markMutate = markRead.mutate;
  useEffect(() => {
    if (enquiryId && unreadHere) markMutate(enquiryId);
  }, [enquiryId, unreadHere, markMutate]);

  if (enquiry.isLoading) {
    return <PaneNotice label="Loading" detail="Fetching this conversation…" />;
  }
  if (enquiry.isError || !enquiry.data) {
    return (
      <PaneNotice
        label={
          isNotFoundError(enquiry.error)
            ? "We couldn't find that question"
            : "We couldn't load this conversation"
        }
        detail="Your other conversations are in the list."
      />
    );
  }

  const data = enquiry.data;
  const closed = data.status === "closed";

  return (
    <Pane
      title={data.subject}
      context={enquiryContext(data.topic_label, data.about_claim)}
      aside={<EnquiryStrike status={data.status} />}
    >
      <MessageThread
        messages={messages.data ?? []}
        sending={send.isPending}
        threadSubject={data.subject}
        placeholder="Add anything else we should know."
        replyDisabledReason={
          closed
            ? "This question is closed. Ask a new one and we'll pick it up there."
            : undefined
        }
        onSend={
          closed
            ? undefined
            : async (body) => {
                try {
                  await send.mutateAsync({ enquiryId, body });
                } catch (err) {
                  toast.error(formatError(err));
                  throw err;
                }
              }
        }
      />
    </Pane>
  );
}

/** Whichever kind the picked conversation is. One switch, so the page never
 * branches on `kind` itself. */
export function ThreadPane({ subject }: { subject: ConversationSubject }) {
  return subject.kind === "enquiry" ? (
    <EnquiryThreadPane enquiryId={subject.id} />
  ) : (
    <ClaimThreadPane claimId={subject.id} />
  );
}

/** Re-exported so a caller composing its own header (the question ROUTE, on a
 * phone) keeps the pane's state vocabulary rather than declaring a second one. */
export { SubjectStrike };
