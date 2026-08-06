/** "Messages" — every conversation the member is part of, most recently active
 * first, and the one they picked open beside it.
 *
 * It lists THREADS, not messages. As a stream it could not answer the question
 * a member actually brings to it: a claim TYPE is not unique on a real roster,
 * so two of one member's conversations printed the same title and the same
 * subject line, and the only thing separating them was a date inside a body
 * snippet clamped to one line.
 *
 * **An index and a STAGE, which is this portal's own device** — the same shape
 * `leaf/Deck` gives coverage, for the same reason: "the rail is what makes this
 * better than the stack". A row used to NAVIGATE to the thing it was about, so
 * a member on a laptop read a 320px column of rows in a 960px frame, spent a
 * whole page transition to read four lines, and spent another to come back.
 * Reading a conversation is now the page doing its job, not leaving it.
 *
 * Three things that keeps honest, all of them in `ThreadPane`:
 *   - the claim's own identity is printed above its thread, so "what is this
 *     about?" is still answered without opening anything;
 *   - the claim's page is one control away, for the things that need a page
 *     (documents, resending);
 *   - both surfaces read through the SAME hooks, so they cannot disagree about
 *     what has been read.
 *
 * **Below the stage width the behaviour is unchanged**: a row navigates to its
 * subject, exactly as it always did. Same routes, same URLs, same emailed
 * links — the phone is not a squeezed desktop and the stage is not a thing to
 * squeeze onto it.
 *
 * There are deliberately **no filter chips**. A member holds single-digit
 * conversations; four controls over five rows is furniture. Every row carries
 * its own state and its own unread count instead.
 */
import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Loader2, MessageSquarePlus } from "lucide-react";
import {
  usePortalConversationPages,
  type Conversation,
} from "@/api/portalMessages";
import { AskQuestionDialog } from "@/components/portal/AskQuestionDialog";
import { Action } from "@/components/portal/leaf/Action";
import {
  ConversationRows,
  conversationKey,
} from "@/components/portal/leaf/ConversationMount";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import {
  ThreadPane,
  ThreadPanePlaceholder,
} from "@/components/portal/leaf/ThreadPane";
import { useContainerWide } from "@/components/portal/leaf/useContainerWide";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

/** Below this the page is the index alone. 720px is where a 20rem index plus a
 * ~24rem thread both clear their own minimums — the portal's content column is
 * 64rem, so a laptop gets the stage and the broker's narrow preview frame does
 * not. */
const STAGE_AT = 720;

export function PortalMessagesPage() {
  const navigate = useNavigate();
  const company = useCompany();
  const conversations = usePortalConversationPages();
  const [measureFrame, wide] = useContainerWide(STAGE_AT);
  const [asking, setAsking] = useState(false);
  // The picked thread rides the URL, so it survives a reload and can be sent to
  // someone. `strict: false` because the route validates it loosely — an alien
  // value simply matches no conversation and falls back to the newest.
  const { open } = useSearch({ strict: false }) as { open?: string };
  useDocumentTitle("Messages");

  const pages = conversations.data?.pages ?? [];
  const items = pages.flatMap((p) => p.items);
  // Both come off the LATEST page: the unread count is whole-inbox (not
  // page-local) and `total` can move while the member reads.
  const last = pages[pages.length - 1];
  const unread = last?.unread_total ?? 0;
  const total = last?.total ?? items.length;

  // Picking is a VIEW change where there is a stage and a navigation where
  // there isn't — one rule, decided by whether the thread has somewhere to be
  // read. `replace` so a member flicking through five conversations doesn't
  // bury the page they came from under five history entries.
  const pick = (c: Conversation) => {
    if (wide) {
      void navigate({
        to: "/portal/$company/messages",
        params: { company },
        search: { open: conversationKey(c) },
        replace: true,
      });
      return;
    }
    void navigate(
      c.subject.kind === "enquiry"
        ? {
            to: "/portal/$company/questions/$enquiryId",
            params: { company, enquiryId: c.subject.id },
          }
        : {
            to: "/portal/$company/claims/$claimId",
            params: { company, claimId: c.subject.id },
          },
    );
  };

  /** Where a JUST-ASKED question is read. The same rule `pick` follows, which
   *  is why it is a function and not a navigate inlined in the dialog: below
   *  the stage width `?open=` is inert, so sending a question dropped the
   *  member back on the list with nothing to show for it. */
  const openEnquiry = (enquiryId: string) => {
    if (wide) {
      void navigate({
        to: "/portal/$company/messages",
        params: { company },
        search: { open: `enquiry:${enquiryId}` },
      });
      return;
    }
    void navigate({
      to: "/portal/$company/questions/$enquiryId",
      params: { company, enquiryId },
    });
  };

  // Icon AND word: this is an action, and an action's label is its gloss. The
  // icon-only rule is for NAVIGATION (see PortalShell).
  //
  // It opens a DIALOG rather than navigating. Asking is short, focused and
  // abandonable, and the page behind it is the context for it — the answer is
  // frequently a conversation the member already holds.
  const ask = (
    <Action tone="primary" block="phone" onClick={() => setAsking(true)}>
      <MessageSquarePlus className="size-4" aria-hidden />
      Ask a question
    </Action>
  );
  const askDialog = (
    <AskQuestionDialog
      open={asking}
      onClose={() => setAsking(false)}
      onCreated={openEnquiry}
    />
  );

  // The newest conversation is on stage when nothing has been picked — the same
  // thing tapping the top row would do, and it is rendered in full, so the read
  // receipt it fires is honest. Never a thread the member has not been shown.
  const selected =
    items.find((c) => conversationKey(c) === open) ?? (wide ? items[0] : undefined);
  const selectedKey = selected ? conversationKey(selected) : null;

  const head = (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <p className="text-row text-label">
        {total === 0
          ? "No conversations yet"
          : `${total} conversation${total === 1 ? "" : "s"}`}
        {unread > 0 && (
          <>
            {" · "}
            <span className="font-semibold text-strike-pending">
              {unread} unread
            </span>
          </>
        )}
      </p>
      {/* The action has something to belong to now. It used to float alone on
          the ground above the list, which is the one placement DESIGN.md warns
          about for the nav row, arriving in the page instead. */}
      <div className="w-full sm:w-auto">{ask}</div>
    </div>
  );

  if (conversations.isLoading) {
    return <LeafSkeleton label="Loading your messages" />;
  }

  // A 404 is "no active coverage" and falls through to the empty state below;
  // anything else is a fetch failure and must not read as "no messages".
  if (conversations.isError && !isNotFoundError(conversations.error)) {
    return <PortalErrorState onRetry={() => void conversations.refetch()} />;
  }

  if (items.length === 0) {
    return (
      <>
        <Mount label="No messages yet">
          <p className="text-row text-label">
            When we have news about a claim &mdash; that we&rsquo;ve received
            it, that it&rsquo;s settled, or that we need something else &mdash;
            it will appear here. You can also ask us anything at all.
          </p>
          <div>{ask}</div>
        </Mount>
        {askDialog}
      </>
    );
  }

  const index = (
    <Mount rise={false}>
      <ConversationRows
        items={items}
        onOpen={pick}
        selectedKey={wide ? selectedKey : null}
      />
      {/* The head counts every conversation, so the page has to be able to
          reach all of them — otherwise the ones it strands are the OLDEST,
          which is exactly where an unanswered "we need something else" would
          be sitting. */}
      {conversations.hasNextPage && (
        <>
          <MountRule />
          <Action
            type="button"
            block
            disabled={conversations.isFetchingNextPage}
            onClick={() => void conversations.fetchNextPage()}
          >
            {conversations.isFetchingNextPage && (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
            Show older
          </Action>
        </>
      )}
    </Mount>
  );

  return (
    <div ref={measureFrame}>
      {head}
      {wide ? (
        // `items-start` so the index does not stretch to a long thread's
        // height, and the index scrolls WITHIN the viewport rather than with
        // the page — a list you have to scroll back up to is not an index.
        <div className="grid grid-cols-[22rem_minmax(0,1fr)] items-start gap-3">
          <div className="sticky top-4 max-h-[calc(100vh-8rem)] overflow-y-auto">
            {index}
          </div>
          {selected ? (
            // Keyed on the conversation, so switching threads remounts the pane
            // — which discards a half-typed reply rather than carrying it to the
            // next member of the conversation, and re-runs the read receipt.
            <ThreadPane key={selectedKey} subject={selected.subject} />
          ) : (
            <ThreadPanePlaceholder />
          )}
        </div>
      ) : (
        index
      )}
      {askDialog}
    </div>
  );
}
