/** The claim conversation, member side.
 *
 * `mine` and `unread` are RELATIVE to whoever asked — the backend fills them
 * per surface. Never derive them here from `author_type`: the broker surface
 * uses the same shape with the opposite sense, and a client-side derivation
 * would have to be written twice and drift once.
 */
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { portalApi } from "@/api/portalClient";

export interface ClaimMessage {
  id: string;
  claim_id: string;
  author_type: "system" | "broker" | "member";
  /** Members always read "Claims team" for anything we wrote — the individual
   *  broker is never disclosed (see services/claim_messages.py). */
  author_name: string | null;
  subject: string;
  body: string;
  /** Set only on automatic notices: submitted | approved | rejected | needs_info. */
  event: string | null;
  created_at: string;
  mine: boolean;
  unread: boolean;
  /** Exactly one of these is set — a claim's thread, or a question's. */
  enquiry_id: string | null;
}

/** What a thread is ABOUT.
 *
 * The claim block is a deliberate SUBSET of `PortalClaim` — enough for
 * `claimTitle` plus the date and amount that tell two claims of the same type
 * apart, and nothing more: a list row has no use for documents or slots. */
export interface ConversationSubject {
  kind: "claim" | "enquiry";
  id: string;
  // ── claim ──────────────────────────────────────────────────────────────────
  claim_kind: "insured" | "flex" | null;
  claim_type: string | null;
  sub_type: string | null;
  product_code: string | null;
  flex_category_name: string | null;
  incurred_date: string | null;
  amount_claimed: number | null;
  currency: string | null;
  /** A claim's own status, or a question's `open | answered | closed`. */
  status: string | null;
  // ── question ───────────────────────────────────────────────────────────────
  /** The member's own headline — a question's title. */
  subject: string | null;
  topic: string | null;
  /** The topic's member-facing name, SERVED — the vocabulary lives on the
   *  backend and neither surface title-cases a raw key. */
  topic_label: string | null;
  /** BROKER triage only. True for a Letter of Guarantee request, the one topic
   *  where the delay is the harm; the member's own screens deliberately ignore
   *  it (the portal promises no turnaround anywhere). */
  topic_urgent: boolean;
  /** A claim this question NAMES without belonging to. Same shape, so the same
   *  helper composes its label — a reference, never a second thread. */
  about_claim: ConversationSubject | null;
}

export interface Conversation {
  subject: ConversationSubject;
  /** Only the last word. The whole thread lives on the claim's own page —
   *  there is deliberately no second reading surface. */
  last_message: ClaimMessage;
  message_count: number;
  unread: number;
}

export interface ConversationList {
  total: number;
  offset: number;
  limit: number;
  /** Unread MESSAGES across the WHOLE inbox, not just this page, and not the
   *  same thing as a conversation's own `unread`. */
  unread_total: number;
  items: Conversation[];
}

/** The first page of conversations, plus the whole-inbox unread count. Drives
 * the home tile (three rows, never pages) and the shell's Messages badge —
 * one query key, so the screen a member lands on pays for it once. */
export function usePortalConversations() {
  return useQuery({
    queryKey: ["portal", "conversations"],
    queryFn: () => portalApi.get<ConversationList>("/portal/conversations"),
    // "No active coverage" (404) is an inline empty state, not a toast — same
    // rule as every other portal data query.
    meta: { localErrorHandling: true },
    retry: false,
  });
}

/** The inbox PAGE, which must be able to reach every conversation.
 *
 * Grouping made the list far shorter than the message stream it replaced — a
 * year of claiming is conversations, not three notices per claim — but the
 * page still has to reach all of it: printing `total` above 50 rows with no
 * way to the rest strands the OLDEST, which is exactly where an unanswered
 * "we need something else" would be sitting. */
export function usePortalConversationPages() {
  return useInfiniteQuery({
    queryKey: ["portal", "conversations", "pages"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      portalApi.get<ConversationList>(
        `/portal/conversations?offset=${pageParam}&limit=${PAGE_SIZE}`,
      ),
    getNextPageParam: (last) => {
      const seen = last.offset + last.items.length;
      return seen < last.total ? seen : undefined;
    },
    meta: { localErrorHandling: true },
    retry: false,
  });
}

const PAGE_SIZE = 50;

export function usePortalClaimMessages(claimId: string | null) {
  return useQuery({
    queryKey: ["portal", "messages", "claim", claimId],
    queryFn: () =>
      portalApi.get<ClaimMessage[]>(`/portal/claims/${claimId}/messages`),
    enabled: Boolean(claimId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useSendClaimMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { claimId: string; body: string }) =>
      portalApi.post<ClaimMessage>(
        `/portal/claims/${input.claimId}/messages`,
        { body: input.body },
      ),
    onSuccess: (_msg, input) => {
      void qc.invalidateQueries({
        queryKey: ["portal", "messages", "claim", input.claimId],
      });
      void qc.invalidateQueries({ queryKey: ["portal", "conversations"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Called when the member opens a thread.
 *
 * `["portal","me"]` is deliberately NOT invalidated: the unread count lives on
 * `GET /portal/messages`, and `PortalMe` carries no unread field (it briefly
 * did, to badge Home in the shell — see schemas/portal.py). Refetching a hot
 * endpoint that cannot have changed is a request per thread opened. */
export function useMarkClaimMessagesRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) =>
      portalApi.post<{ marked: number }>(
        `/portal/claims/${claimId}/messages/read`,
        {},
      ),
    onSuccess: (out, claimId) => {
      // Nothing changed → don't churn the queries on every page view.
      if (out.marked === 0) return;
      void qc.invalidateQueries({
        queryKey: ["portal", "messages", "claim", claimId],
      });
      // Prefix match, so it also refreshes the paged inbox
      // (`["portal","messages","pages"]`) — its rows carry `unread` too.
      void qc.invalidateQueries({ queryKey: ["portal", "conversations"] });
    },
    meta: { localErrorHandling: true },
  });
}
