/** Messages, on the member's leaf.
 *
 * This file owns the THREAD — one conversation read in full, with the member's
 * own replies set apart. The index beside it is `ConversationMount`, which
 * lists THREADS rather than messages: a flat stream could not say which of a
 * member's claims a row belonged to.
 *
 * Four rules this file is easy to break:
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
 *
 * 4. **The date is printed once per DAY, not once per message.** Six replies
 *    exchanged in one afternoon each carried "1 Aug 2026, 18:51" and the eye
 *    had to read all six to find where a new day started. The rail carries the
 *    day; a message keeps only its clock.
 */
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Loader2, Send } from "lucide-react";
import type { ClaimMessage } from "@/api/portalMessages";
import { cn } from "@/lib/cn";
import { Action } from "./Action";
import { clockTime, dateKey, dayHeading } from "./date";

/** The unread count, on a chrome icon.
 *
 * A NUMBER, never a bare dot. `PortalMe` deliberately carries no unread field
 * (schemas/portal.py says why), and the phone dock's dot already means exactly
 * one thing — an enrolment window is open. A second unglossed dot in the same
 * chrome would leave neither explaining itself, so this one states its count
 * and its control carries the wording in its accessible name.
 *
 * `aria-hidden`: the figure is already in that name, and announcing it twice is
 * how "Messages 2 unread, 2" happens.
 *
 * Capped at 9+ so a three-digit inbox can't stretch the pill off its icon — the
 * exact figure stays in the label, where it can actually be read.
 */
export function UnreadBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      aria-hidden
      className={
        "absolute -right-0.5 -top-0.5 inline-flex min-w-4 items-center justify-center " +
        "rounded-pill bg-strike-pending px-1 text-2xs font-bold leading-4 text-bar"
      }
    >
      {count > 9 ? "9+" : count}
    </span>
  );
}

/** The accessible name for a Messages control. ONE implementation: the member's
 * shell and the broker's preview frame both render one, and wording that drifts
 * is wording only one surface's users ever hear. */
export function messagesLabel(unread: number) {
  return unread > 0 ? `Messages (${unread} unread)` : "Messages";
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

/** Initials, so a thread reads as a conversation between two parties without
 * spending a colour or an avatar image on it. */
function Who({ name, mine }: { name: string; mine: boolean }) {
  const initial = name.trim().charAt(0).toUpperCase() || "?";
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

/** The day rail. A rule with the date sitting in it, so a thread that runs over
 * weeks reads as a sequence of days rather than one undifferentiated column. */
function DayRule({ label }: { label: string }) {
  return (
    <li className="flex items-center gap-3 py-1" aria-hidden>
      <span className="h-px flex-1 bg-hairline/75" />
      <span className="leaf-label shrink-0 text-label">{label}</span>
      <span className="h-px flex-1 bg-hairline/75" />
    </li>
  );
}

function ThreadMessage({
  message,
  threadSubject,
}: {
  message: ClaimMessage;
  threadSubject?: string;
}) {
  // "You", not the member's own name. They are reading their own inbox, and
  // "You / Claims team / You" is scannable in a way that their full legal name
  // alternating with ours is not.
  const author = message.mine ? "You" : (message.author_name ?? "Unknown");
  // The subject is dropped on the member's OWN replies (they carry the
  // placeholder subject the index needs) AND when it merely repeats the title
  // of the thread it is in — a question page printed its own subject as the
  // frame's heading and again above the answer, 110px apart.
  const subject =
    !message.mine &&
    message.subject &&
    message.subject.trim() !== (threadSubject ?? "").trim()
      ? message.subject
      : null;
  return (
    <li className="flex gap-3">
      <Who name={author} mine={message.mine} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-row font-semibold text-record">{author}</span>
          <span className="text-2xs text-label">
            {clockTime(message.created_at)}
          </span>
          {message.unread && <UnreadDot />}
        </div>
        {subject && (
          <p className="mt-0.5 text-row font-medium text-record">{subject}</p>
        )}
        <p className="mt-1 whitespace-pre-line text-row text-record">
          {message.body}
        </p>
      </div>
    </li>
  );
}

const MAX_REPLY = 2000;

/** The composer grows with what is typed, from two rows.
 *
 * A fixed three-row box is ~110px of empty control on every settled thread, and
 * in the two-pane inbox that is 110px taken from the conversation above it. It
 * is capped so a long message scrolls inside the box rather than pushing the
 * Send button off a phone screen. */
function useAutoGrow(value: string) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);
  return ref;
}

export function MessageThread({
  messages,
  onSend,
  sending = false,
  /** Why replying isn't possible right now (a draft claim, the broker
   *  preview). Stated rather than silently hiding the composer — a missing
   *  control with no explanation reads as broken. */
  replyDisabledReason,
  /** The thread's own title, so a message doesn't reprint it as its subject. */
  threadSubject,
  placeholder = "Ask a question or add something we should know.",
  empty,
}: {
  messages: ClaimMessage[];
  onSend?: (body: string) => Promise<void> | void;
  sending?: boolean;
  replyDisabledReason?: string;
  threadSubject?: string;
  placeholder?: string;
  empty?: ReactNode;
}) {
  const [draft, setDraft] = useState("");
  const trimmed = draft.trim();
  const boxRef = useAutoGrow(draft);

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

  // One day heading per run of messages sent on the same day, computed while
  // rendering rather than by pre-grouping into arrays — the list is already in
  // order, so the only thing needed is "did the day change".
  let lastDay = "";

  return (
    <div className="space-y-4">
      {messages.length > 0 ? (
        <ul className="space-y-4">
          {messages.map((m) => {
            const day = dateKey(m.created_at);
            const heading = day !== lastDay ? dayHeading(m.created_at) : null;
            lastDay = day;
            return (
              <Fragment key={m.id}>
                {heading && <DayRule label={heading} />}
                <ThreadMessage message={m} threadSubject={threadSubject} />
              </Fragment>
            );
          })}
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
        <form
          className="space-y-2 border-t border-hairline/75 pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <label htmlFor="claim-reply" className="sr-only">
            Reply
          </label>
          <textarea
            ref={boxRef}
            id="claim-reply"
            rows={2}
            maxLength={MAX_REPLY}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            className={
              "leaf-focus w-full resize-none rounded-control border border-leaf-input " +
              "bg-bar/80 px-3 py-2.5 text-row text-record placeholder:text-label"
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
      ) : (
        replyDisabledReason && (
          <p className="border-t border-hairline/75 pt-4 text-row text-label">
            {replyDisabledReason}
          </p>
        )
      )}
    </div>
  );
}
