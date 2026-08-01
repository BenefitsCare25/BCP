/** The claim conversation, broker side.
 *
 * **Everything sent from here is read by the member.** There is no internal-note
 * mode by design — broker-only reasoning belongs in the decision note and the AI
 * review, and a thread where some rows are hidden is the shape that eventually
 * leaks one. The composer says so above the box rather than in a tooltip.
 *
 * Automatic notices (submitted / approved / rejected / needs_info) appear here
 * too, so a broker can see exactly what the member was told and when, without
 * reconstructing it from the status history.
 */
import { useEffect, useState } from "react";
import { Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import {
  useClaimMessages,
  useMarkClaimMessagesRead,
  useSendClaimMessage,
  type ClaimMessage,
} from "@/api/claims";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";

const AUTHOR_LABEL: Record<ClaimMessage["author_type"], string> = {
  system: "Automatic",
  broker: "Sent by us",
  member: "From the member",
};

function MessageRow({ message }: { message: ClaimMessage }) {
  return (
    <li
      className={cn(
        "rounded-md border p-3",
        // The member's own words are the ones a broker is looking for in a long
        // thread, so they get the surface; ours sit on the page.
        message.author_type === "member"
          ? "border-border bg-card"
          : "border-border/60 bg-muted/40",
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-sm font-medium text-foreground">
          {message.author_name ?? "Unknown"}
        </span>
        <Badge variant="outline">{AUTHOR_LABEL[message.author_type]}</Badge>
        {message.unread && <Badge variant="warn">new</Badge>}
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">
          {fmtDateTime(message.created_at)}
        </span>
      </div>
      {/* A member's reply carries the placeholder subject the member's INBOX
          needs ("Your reply") — printed here it reads as the broker's own.
          The "From the member" badge already says whose it is. */}
      {message.author_type !== "member" && (
        <p className="mt-1 text-sm font-medium text-foreground">
          {message.subject}
        </p>
      )}
      {/* The automatic notices are written as paragraphs and a broker pastes an
          insurer's wording — collapsing the author's line breaks turns both
          into a wall. */}
      <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">
        {message.body}
      </p>
    </li>
  );
}

export function ClaimMessages({ claimId }: { claimId: string }) {
  const messages = useClaimMessages(claimId);
  const send = useSendClaimMessage();
  const markRead = useMarkClaimMessagesRead();
  const { data: me } = useMe();
  const [body, setBody] = useState("");

  // **Both writes here are behind `require_write_access`**, which 403s every
  // `broker_viewer`. Firing them anyway meant a read-only broker got a Send
  // button that always failed and — because the mutation carries
  // `localErrorHandling` — a silently 403ing read receipt on every claim they
  // opened, leaving the queue's "N new" badge lit forever with no explanation.
  const readOnly = me?.role === "broker_viewer";

  // Opening the sheet's Messages section IS reading it. Gated on there being
  // something unread so re-opening a settled claim doesn't write on every view.
  const hasUnread = (messages.data ?? []).some((m) => m.unread);
  const markMutate = markRead.mutate;
  useEffect(() => {
    if (hasUnread && !readOnly) markMutate(claimId);
  }, [claimId, hasUnread, readOnly, markMutate]);

  // A claim changes in the sheet without unmounting it — clear a half-typed
  // message when the broker moves to a different claim, or it would be sent to
  // the wrong member.
  useEffect(() => setBody(""), [claimId]);

  const submit = async () => {
    const text = body.trim();
    if (!text) return;
    try {
      await send.mutateAsync({ claimId, body: text });
      setBody(""); // only on success — a failed send must keep the text
      toast.success("Sent to the member");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-3">
      {messages.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : messages.isError ? (
        <p className="text-sm text-muted-foreground">
          Couldn&rsquo;t load this claim&rsquo;s messages.
        </p>
      ) : (messages.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No messages yet. Submitting and deciding a claim posts one
          automatically.
        </p>
      ) : (
        <ul className="space-y-2">
          {messages.data!.map((m) => (
            <MessageRow key={m.id} message={m} />
          ))}
        </ul>
      )}

      {readOnly ? (
        <p className="text-sm text-muted-foreground">
          Your access is read-only, so you can&rsquo;t write to the member from
          here.
        </p>
      ) : (
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label
          htmlFor={`claim-message-${claimId}`}
          className="block text-xs font-medium text-muted-foreground"
        >
          Write to the member (they will see this in their portal)
        </label>
        <textarea
          id={`claim-message-${claimId}`}
          rows={3}
          maxLength={4000}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="e.g. We've sent this to the insurer and expect an outcome shortly."
          className={
            "w-full rounded-md border border-input bg-background px-3 py-2 text-sm " +
            "text-foreground placeholder:text-subtle focus-visible:outline-none " +
            "focus-visible:ring-2 focus-visible:ring-ring/40"
          }
        />
        <Button type="submit" size="sm" disabled={!body.trim() || send.isPending}>
          {send.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
          Send message
        </Button>
      </form>
      )}
    </div>
  );
}
