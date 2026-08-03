/** "Messages" — everything anyone has written to this member about a claim,
 * newest first.
 *
 * The inbox does NOT open a message in place. Every message is ABOUT a claim,
 * and answering "what is this about?" needs the claim beside it — the amount,
 * the documents, whether anything is being asked of them. So a row navigates to
 * the claim, where the whole thread lives and where replying is possible. A
 * second reading surface here would be the same conversation in two places,
 * each able to be read while the other still shows unread.
 */
import { useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { usePortalMessagePages } from "@/api/portalMessages";
import { Action } from "@/components/portal/leaf/Action";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { MessageRows } from "@/components/portal/leaf/MessageMount";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { Strike } from "@/components/portal/leaf/Strike";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

export function PortalMessagesPage() {
  const navigate = useNavigate();
  const company = useCompany();
  const messages = usePortalMessagePages();
  useDocumentTitle("Messages");

  if (messages.isLoading) {
    return <LeafSkeleton label="Loading your messages" />;
  }

  // A 404 is "no active coverage" and falls through to the empty state below;
  // anything else is a fetch failure and must not read as "no messages".
  if (messages.isError && !isNotFoundError(messages.error)) {
    return <PortalErrorState onRetry={() => void messages.refetch()} />;
  }

  const pages = messages.data?.pages ?? [];
  const items = pages.flatMap((p) => p.items);
  // Both come off the LATEST page: the unread count is whole-inbox (not
  // page-local) and `total` can move while the member reads.
  const last = pages[pages.length - 1];
  const unread = last?.unread ?? 0;
  const total = last?.total ?? items.length;

  if (items.length === 0) {
    return (
      <Mount label="No messages yet">
        <p className="text-row text-label">
          When we have news about a claim &mdash; that we&rsquo;ve received it,
          that it&rsquo;s settled, or that we need something else &mdash; it
          will appear here.
        </p>
      </Mount>
    );
  }

  return (
    <Mount
      label={`${total} message${total === 1 ? "" : "s"}`}
      aside={unread > 0 ? <Strike tone="pending">{unread} unread</Strike> : undefined}
    >
      <MessageRows
        items={items}
        onOpen={(m) =>
          void navigate({
            to: "/portal/$company/claims/$claimId",
            params: { company, claimId: m.claim_id },
          })
        }
        className="-mt-1"
      />
      {/* The heading counts the WHOLE inbox, so the page has to be able to
          reach all of it — otherwise "63 messages" sits above 50 rows and the
          13 it strands are the OLDEST, which is exactly where an unanswered
          "we need something else" would be. */}
      {messages.hasNextPage && (
        <>
          <MountRule />
          <Action
            type="button"
            block="phone"
            disabled={messages.isFetchingNextPage}
            onClick={() => void messages.fetchNextPage()}
          >
            {messages.isFetchingNextPage && (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
            Show older messages
          </Action>
        </>
      )}
    </Mount>
  );
}
