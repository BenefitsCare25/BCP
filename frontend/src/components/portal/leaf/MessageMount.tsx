/** Messages, on the member's leaf.
 *
 * Two renderings of the same rows, because they answer different questions:
 *
 *   `MessageRows`   — the inbox. "Is there anything I need to know?" Date rail,
 *                     subject, one clamped line of body. Scannable.
 *   `MessageThread` — one claim's conversation. "What was actually said?" Full
 *                     bodies, in order, with the member's own replies set apart.
 *
 * Three rules this file is easy to break:
 *
 * 1. **Unread is marked in INK, never in brand.** A red dot beside every notice
 *    would be the third brand fill on the home screen and would make routine
 *    acknowledgements read as alarms. Unread here is a weight change plus one
 *    small pending-ink dot — the same ink the enrolment deadline uses, because
 *    it means the same thing: something is waiting on you.
 *
 * 2. **A message body is the only place on this surface where line breaks are
 *    the author's.** `whitespace-pre-line` is load-bearing: the automatic
 *    notices are written as paragraphs and a broker pastes an insurer's
 *    wording. Collapsing it turns both into a wall.
 *
 * 3. **The composer is a form with an explicit submit**, and Enter inserts a
 *    newline rather than sending. The claim form learned this the hard way —
 *    implicit submission there created, uploaded and SENT a claim on one
 *    keystroke. A message is less costly to send by accident but is still
 *    something a person reads.
 */
import { useState, type ReactNode } from "react";
import { Loader2, Send } from "lucide-react";
import type { ClaimMessage } from "@/api/portalMessages";
import { cn } from "@/lib/cn";
import { Action } from "./Action";
import { MountRule } from "./Mount";
import { dayStamp, formatMoment } from "./date";

/** The month-over-day rail. `aria-hidden` on the two halves and one readable
 * label instead — "Feb / 11" read aloud as two fragments is worse than the
 * date said once. */
function DateRail({ iso }: { iso: string }) {
  const { month, day } = dayStamp(iso);
  return (
    <span className="flex w-9 shrink-0 flex-col items-center leading-none">
      <span aria-hidden className="text-2xs font-semibold uppercase tracking-[0.085em] text-label">
        {month}
      </span>
      <span aria-hidden className="mt-1 text-md font-semibold tabular-nums text-record">
        {day}
      </span>
    </span>
  );
}

function UnreadDot() {
  return (
    <>
      <span
        aria-hidden
        className="mt-1.5 size-1.5 shrink-0 rounded-pill bg-strike-pending"
      />
      <span className="sr-only">(unread)</span>
    </>
  );
}

/** One inbox row. `onOpen` makes the whole row the target — it goes to the
 * message's CLAIM, since a message is always about one. Without it the row is
 * inert (the broker preview, where a member action would walk the broker out of
 * their own application). */
function MessageRow({
  message,
  onOpen,
}: {
  message: ClaimMessage;
  onOpen?: (message: ClaimMessage) => void;
}) {
  const inner = (
    <>
      <DateRail iso={message.created_at} />
      <span className="min-w-0 flex-1">
        <span className="flex items-start gap-1.5">
          <span
            className={cn(
              "min-w-0 text-row text-record",
              message.unread ? "font-semibold" : "font-medium",
            )}
          >
            {message.subject}
          </span>
          {message.unread && <UnreadDot />}
        </span>
        {/* One line. The whole body is on the claim's own page, and a
            four-line preview turns a list of five notices into a page of
            prose — a needs-info notice alone runs to four.

            No `block` here: `line-clamp-1` works by setting
            `display: -webkit-box`, and a `block` alongside it wins in the
            cascade and silently un-clamps the line. It is already
            block-level. */}
        <span className="mt-0.5 line-clamp-1 text-row text-label">
          {message.body}
        </span>
        {message.claim_type && (
          <span className="mt-0.5 block truncate text-2xs text-label">
            {message.claim_type}
          </span>
        )}
      </span>
    </>
  );

  // The whole ROW is marked while it is unread, not just the subject — a 6px
  // dot is not an answer to "is there anything I need to know?" scanned at
  // arm's length. The wash is dropped the moment the thread is opened, which is
  // what makes the mark mean something: a page of highlighted rows that never
  // change is wallpaper.
  //
  // **Square corners, and the fill must not overflow the row's own box.** With
  // a rounded fill bled out past the row (`-mx-2 rounded-control`), two ADJACENT
  // unread rows fused into one blob and the list's hairline — drawn at the row's
  // real width — was swallowed between them. A row band is a table row, not a
  // chip: it runs the full width, it stops square, and the divider above it
  // stays visible. Consecutive unread rows then read as two rows, which is what
  // they are.
  //
  // Hover DEEPENS rather than replaces. Two different fills swapping on hover
  // makes an unread row appear to become read under the cursor.
  const fill = message.unread
    ? "bg-unread-wash hover:bg-shade/60"
    : "hover:bg-shade/60";

  if (!onOpen) {
    return (
      <li
        className={cn(
          "flex items-start gap-3 py-3",
          message.unread && "bg-unread-wash",
        )}
      >
        {inner}
      </li>
    );
  }
  return (
    // The button fills the row exactly, so the `divide-y` hairline on the `li`
    // is painted at the border box — outside the button's background — and
    // survives between two washed rows.
    <li>
      <button
        type="button"
        onClick={() => onOpen(message)}
        className={cn(
          "leaf-focus flex w-full items-start gap-3 py-3 text-left",
          "transition-colors duration-200 ease-leaf",
          fill,
        )}
      >
        {inner}
      </button>
    </li>
  );
}

export function MessageRows({
  items,
  onOpen,
  className,
}: {
  items: ClaimMessage[];
  onOpen?: (message: ClaimMessage) => void;
  className?: string;
}) {
  return (
    <ul className={cn("divide-y divide-hairline/75", className)}>
      {items.map((m) => (
        <MessageRow key={m.id} message={m} onOpen={onOpen} />
      ))}
    </ul>
  );
}

/** Initials, so a thread reads as a conversation between two parties without
 * spending a colour or an avatar image on it. */
function Who({ name, mine }: { name: string | null; mine: boolean }) {
  const initial = (name ?? "?").trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-8 shrink-0 items-center justify-center rounded-pill text-row font-semibold",
        mine ? "bg-action-wash text-action-ink" : "bg-shade text-record",
      )}
    >
      {initial}
    </span>
  );
}

function ThreadMessage({ message }: { message: ClaimMessage }) {
  return (
    <li className="flex gap-3">
      <Who name={message.author_name} mine={message.mine} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-row font-semibold text-record">
            {message.author_name ?? "Unknown"}
          </span>
          <span className="text-2xs text-label">
            {formatMoment(message.created_at)}
          </span>
          {message.unread && <UnreadDot />}
        </div>
        {/* The subject is dropped on the member's OWN replies: they carry the
            placeholder subject the inbox needs, and printing "Your reply" above
            every line the member typed is furniture repeating itself. */}
        {!message.mine && (
          <p className="mt-0.5 text-row font-medium text-record">
            {message.subject}
          </p>
        )}
        <p className="mt-1 whitespace-pre-line text-row text-record">
          {message.body}
        </p>
      </div>
    </li>
  );
}

const MAX_REPLY = 2000;

export function MessageThread({
  messages,
  onSend,
  sending = false,
  /** Why replying isn't possible right now (a draft claim, the broker
   *  preview). Stated rather than silently hiding the composer — a missing
   *  control with no explanation reads as broken. */
  replyDisabledReason,
  empty,
}: {
  messages: ClaimMessage[];
  onSend?: (body: string) => Promise<void> | void;
  sending?: boolean;
  replyDisabledReason?: string;
  empty?: ReactNode;
}) {
  const [draft, setDraft] = useState("");
  const trimmed = draft.trim();

  // The draft is cleared ONLY on success. A failed send that also wiped the box
  // loses text the member typed — the one failure here they cannot undo. The
  // caller reports the error (it owns the wording); this just keeps the text
  // and swallows the rejection so it isn't unhandled.
  const send = async () => {
    if (!onSend || !trimmed || sending) return;
    try {
      await onSend(trimmed);
      setDraft("");
    } catch {
      /* handled by the caller */
    }
  };

  return (
    <div className="space-y-4">
      {messages.length > 0 ? (
        <ul className="space-y-4">
          {messages.map((m) => (
            <ThreadMessage key={m.id} message={m} />
          ))}
        </ul>
      ) : (
        empty ?? (
          <p className="text-row text-label">
            Nothing here yet. We&rsquo;ll write to you if anything about this
            claim needs your attention.
          </p>
        )
      )}

      {onSend ? (
        <>
          <MountRule />
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <label htmlFor="claim-reply" className="leaf-label">
              Reply
            </label>
            <textarea
              id="claim-reply"
              rows={3}
              maxLength={MAX_REPLY}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask a question or add something we should know."
              className={
                "leaf-focus w-full rounded-control border border-leaf-input bg-bar/80 " +
                "px-3 py-2.5 text-row text-record placeholder:text-label"
              }
            />
            <Action
              type="submit"
              tone="quiet"
              block="phone"
              disabled={!trimmed || sending}
            >
              {sending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Send className="size-4" aria-hidden />
              )}
              Send
            </Action>
          </form>
        </>
      ) : (
        replyDisabledReason && (
          <>
            <MountRule />
            <p className="text-row text-label">{replyDisabledReason}</p>
          </>
        )
      )}
    </div>
  );
}
