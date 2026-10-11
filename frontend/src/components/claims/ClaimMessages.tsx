/** The claim conversation, broker side.
 *
 * The thread itself is `ThreadMessages`, shared with a member's question — what
 * lives here is the claim's own hooks, its read receipt, and the read-only gate.
 *
 * Automatic notices (submitted / approved / rejected / needs_info) appear here
 * too, so a broker can see exactly what the member was told and when, without
 * reconstructing it from the status history.
 */
import { useEffect } from "react";
import { toast } from "sonner";
import {
  useClaimMessages,
  useMarkClaimMessagesRead,
  useSendClaimMessage,
} from "@/api/claims";
import { useMe } from "@/api/hooks";
import { isReadOnlyAccess } from "@/lib/roles";
import { ThreadMessages } from "@/components/claims/ThreadMessages";
import { formatError } from "@/lib/errors";

export function ClaimMessages({
  claimId,
  stickyComposer = false,
  memberVisible = true,
}: {
  claimId: string;
  stickyComposer?: boolean;
  /** False for a LOG case or an HR submission: the member cannot open the
   *  claim, so nothing written here would reach them (the API refuses it). */
  memberVisible?: boolean;
}) {
  const messages = useClaimMessages(claimId);
  const send = useSendClaimMessage();
  const markRead = useMarkClaimMessagesRead();
  const { data: me } = useMe();

  // **Both writes here are behind `require_write_access`**, which 403s every
  // `broker_viewer`. Firing them anyway meant a read-only broker got a Send
  // button that always failed and — because the mutation carries
  // `localErrorHandling` — a silently 403ing read receipt on every claim they
  // opened, leaving the queue's "N new" badge lit forever with no explanation.
  const readOnly = isReadOnlyAccess(me?.role, me?.platform_access);

  // Opening the Messages section IS reading it. Gated on there being something
  // unread so re-opening a settled claim doesn't write on every view.
  const hasUnread = (messages.data ?? []).some((m) => m.unread);
  const markMutate = markRead.mutate;
  useEffect(() => {
    if (hasUnread && !readOnly) markMutate(claimId);
  }, [claimId, hasUnread, readOnly, markMutate]);

  return (
    <ThreadMessages
      // A claim changes in the sheet without unmounting it, so the composer is
      // remounted per claim — a half-typed message must never carry over to a
      // different member.
      key={claimId}
      idSuffix={claimId}
      messages={messages.data}
      loading={messages.isLoading}
      error={messages.isError}
      onRetry={() => void messages.refetch()}
      sending={send.isPending}
      stickyComposer={stickyComposer}
      emptyText="No messages yet. Submitting and deciding a claim posts one automatically."
      disabledReason={
        readOnly
          ? "Your access is read-only, so you can't write to the member from here."
          : !memberVisible
            ? "The member can't see this claim because it wasn't filed through their portal. Contact them or their HR directly."
            : undefined
      }
      onSend={
        readOnly || !memberVisible
          ? undefined
          : async (body) => {
              try {
                await send.mutateAsync({ claimId, body });
                toast.success("Sent to the member");
              } catch (err) {
                toast.error(formatError(err));
                throw err;
              }
            }
      }
    />
  );
}
