/** Conversations, on the member's leaf.
 *
 * A list of THREADS, replacing a stream of messages. The stream could not
 * answer the one question a member brings to it — *which of my claims is this
 * about?* — because it identified a row by the message's subject ("We have your
 * claim", four times over) and a grey sub-line naming the claim TYPE, which is
 * not unique. One real CDL member holds two "Emergency Accidental Outpatient
 * Treatment" conversations and two "Follow up Pre-/Post-Hospitalisation" ones;
 * the only text separating each pair was a date inside a body snippet clamped
 * to a single line.
 *
 * So a row is a conversation, and it carries five things:
 *
 *   1. **the title** — the claim's own name, through `insuredClaimTitle`, so
 *      the portal names a claim in exactly one place;
 *   2. **when it last moved** — added because it was missing. Without it a
 *      notice from March and one from this morning read identically, and
 *      *is this still live?* is the first question an inbox is asked;
 *   3. **the identity line** — date · amount, which is what actually tells two
 *      claims of one type apart, or a question's TOPIC, which was being dropped
 *      so that every question a member had ever asked read `Question`;
 *   4. **the state** — the member's own `ClaimStrike` vocabulary;
 *   5. **the last word** — who wrote it, and one clamped line.
 *
 * The three parts sit on their own lines with the state RIGHT-aligned beside
 * the identity rather than beside the title. That is what lets the same row
 * render in a 300px index column and at full width: a title and a strike
 * reading `MORE INFO NEEDED` cannot share one line at index width, and the
 * title is the part that must not be the one to give way.
 *
 * **A row does not open a thread by itself — it reports a selection.** Where
 * the page is wide enough it moves the stage beside the list; where it is not,
 * it navigates. That switch lives in the page, not here, so this stays one
 * component across the member's inbox, the home tile and the broker's preview.
 */
import type { Conversation, ConversationSubject } from "@/api/portalMessages";
import { cn } from "@/lib/cn";
import { ClaimStrike, Strike } from "./Strike";
import { insuredClaimTitle } from "./ClaimMount";
import { formatDay, shortMoment } from "./date";
import { currencySymbol, moneyText } from "./Figure";

/** Stable identity for a conversation across both kinds. The list keys on it
 * and the stage selects on it, so it is defined once rather than spelled out at
 * each site — two spellings of a composite key is how a selected row comes to
 * highlight one thread while the stage shows another. */
export function conversationKey(c: Conversation): string {
  return subjectKey(c.subject);
}

export function subjectKey(s: ConversationSubject): string {
  return `${s.kind}:${s.id}`;
}

/** A subject's own name. Claims go through `insuredClaimTitle`, which is the
 * ONE rule the portal names a claim by — this used to re-derive it, and so did
 * not inherit its guard against the broker-side `LOG` sentinel: a member's
 * message index listed a conversation titled `LOG`. A question is titled by the
 * member's own words. Takes a subject rather than a whole conversation so the
 * nested `about_claim` composes with it. */
export function subjectTitle(s: ConversationSubject): string {
  if (s.kind === "enquiry") return s.subject || "Your question";
  if (s.claim_kind === "flex") return s.flex_category_name || "Flexible benefit";
  return insuredClaimTitle(s.claim_type, s.product_code);
}

export function conversationTitle(c: Conversation): string {
  return subjectTitle(c.subject);
}

/** What tells two conversations of the SAME title apart.
 *
 * For a claim that is date and amount — a claim TYPE is not unique on a real
 * roster. For a question it is the TOPIC it was filed under, plus the claim it
 * names if it names one. The topic label is SERVED (`topic_label`), never
 * title-cased here: the vocabulary has one home, and this line used to print
 * the literal word "Question" and nothing else, which made every question a
 * member held indistinguishable from every other. */
export function identityLine(s: ConversationSubject): string {
  if (s.kind === "enquiry") {
    return [
      s.topic_label || s.topic || "Question",
      s.about_claim ? `about ${subjectTitle(s.about_claim)}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return [
    s.incurred_date ? formatDay(s.incurred_date) : null,
    s.amount_claimed != null
      ? `${currencySymbol(s.currency)}${moneyText(s.amount_claimed)}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** A question's state, in the member's own strike vocabulary. `answered` is
 * approved-green because it is the good outcome; `open` carries the pending ink
 * that means "waiting on someone"; a closed thread is settled and quiet. */
const ENQUIRY_TONE: Record<string, "approved" | "pending" | "review"> = {
  open: "pending",
  answered: "approved",
  closed: "review",
};

const ENQUIRY_LABEL: Record<string, string> = {
  open: "Waiting",
  answered: "Answered",
  closed: "Closed",
};

/** A question's state, struck. Its own export because three surfaces need it
 * from something that is NOT a conversation subject — the member's pane, the
 * broker's preview frame, and the question route all hold an `Enquiry`. Three
 * private copies of the same two maps is how "Waiting" comes to read "open" on
 * one of them. */
export function EnquiryStrike({ status }: { status: string }) {
  return (
    <Strike tone={ENQUIRY_TONE[status] ?? "review"}>
      {ENQUIRY_LABEL[status] ?? status}
    </Strike>
  );
}

export function SubjectStrike({ subject }: { subject: ConversationSubject }) {
  if (!subject.status) return null;
  if (subject.kind === "enquiry") {
    return <EnquiryStrike status={subject.status} />;
  }
  return <ClaimStrike status={subject.status} />;
}

/** The count, on the row. A number rather than a dot for the same reason the
 * chrome badge is one — a mark that cannot say how much is waiting makes the
 * member open the thread to find out. */
function UnreadCount({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <>
      <span
        aria-hidden
        className={
          "inline-flex min-w-5 shrink-0 items-center justify-center rounded-pill " +
          "bg-strike-pending px-1.5 text-2xs font-bold leading-5 text-bar"
        }
      >
        {count > 9 ? "9+" : count}
      </span>
      <span className="sr-only">({count} unread)</span>
    </>
  );
}

function ConversationRow({
  conversation,
  onOpen,
  selected = false,
}: {
  conversation: Conversation;
  onOpen?: (conversation: Conversation) => void;
  selected?: boolean;
}) {
  const last = conversation.last_message;
  const identity = identityLine(conversation.subject);
  const inner = (
    <>
      <span className="flex items-baseline justify-between gap-3">
        <span
          className={cn(
            // Clamped to two lines rather than truncated: at index width a
            // real claim name ("Emergency Accidental Outpatient Treatment")
            // loses its last three words to an ellipsis, and those words are
            // most of what distinguishes it from the product beside it.
            "line-clamp-2 min-w-0 flex-1 text-md leading-5 text-record",
            conversation.unread ? "font-semibold" : "font-medium",
          )}
        >
          {conversationTitle(conversation)}
        </span>
        {/* When, not how long — a member reads their own inbox, they do not
            work a queue out of it. `tabular-nums` is inherited from `.leaf`. */}
        <span className="shrink-0 text-2xs text-label">
          {shortMoment(last.created_at)}
        </span>
      </span>
      <span className="mt-0.5 flex items-baseline justify-between gap-3">
        <span className="min-w-0 flex-1 truncate text-row text-label">
          {identity}
        </span>
        <span className="shrink-0">
          <SubjectStrike subject={conversation.subject} />
        </span>
      </span>
      {/* The last word, and who had it. `mine` is server-filled per surface —
          never derived from `author_type` here, which the broker surface reads
          with the opposite sense. */}
      <span className="mt-1.5 flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate text-row text-label">
          <span className="font-medium text-record">
            {last.mine ? "You" : last.author_name}
          </span>
          {" — "}
          {last.body}
        </span>
        <UnreadCount count={conversation.unread} />
      </span>
    </>
  );

  // Three fills, and the order they resolve in is the point. SELECTED wins: it
  // is the neutral `shade` every picker in this world marks its choice with
  // (see the Do-vs-Pick Rule — a list row picks a view, so it is never
  // terracotta), and a selected row that still wore the unread wash could not
  // be told from its neighbours. The wash marks the whole ROW and stops square,
  // so the list's hairline survives between two washed rows.
  const fill = selected
    ? "bg-shade"
    : conversation.unread
      ? "bg-unread-wash hover:bg-shade/60"
      : "hover:bg-shade/60";

  if (!onOpen) {
    return (
      <li className={cn("block py-3", conversation.unread && "bg-unread-wash")}>
        {inner}
      </li>
    );
  }
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(conversation)}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "leaf-focus block w-full px-3 py-3 text-left",
          "transition-colors duration-200 ease-leaf",
          fill,
        )}
      >
        {inner}
      </button>
    </li>
  );
}

export function ConversationRows({
  items,
  onOpen,
  selectedKey,
  className,
}: {
  items: Conversation[];
  /** Reports the picked conversation. Omitted where rows are inert. */
  onOpen?: (conversation: Conversation) => void;
  /** `conversationKey` of the one on stage, where a stage exists. */
  selectedKey?: string | null;
  className?: string;
}) {
  return (
    // `-mx-3` pairs with the row's own `px-3`: the fill of a selected or unread
    // row reaches the mount's edge while its TEXT stays on the mount's margin —
    // the One-Left-Edge Rule. Inset instead and every row's text steps in from
    // every other printed term in the frame.
    <ul className={cn("-mx-3 divide-y divide-hairline/75", className)}>
      {items.map((c) => {
        const key = conversationKey(c);
        return (
          <ConversationRow
            key={key}
            conversation={c}
            onOpen={onOpen}
            selected={selectedKey === key}
          />
        );
      })}
    </ul>
  );
}
