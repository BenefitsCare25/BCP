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
  /** Populated only in the cross-claim inbox — the thread sits on its claim. */
  claim_type: string | null;
  claim_status: string | null;
}

export interface ClaimMessageList {
  total: number;
  offset: number;
  limit: number;
  /** Unread across the WHOLE inbox, not just this page. */
  unread: number;
  items: ClaimMessage[];
}

/** The first page of the inbox, plus the whole-inbox unread count. Drives the
 * home tile, which shows three rows and never pages. */
export function usePortalMessages() {
  return useQuery({
    queryKey: ["portal", "messages"],
    queryFn: () => portalApi.get<ClaimMessageList>("/portal/messages"),
    // "No active coverage" (404) is an inline empty state, not a toast — same
    // rule as every other portal data query.
    meta: { localErrorHandling: true },
    retry: false,
  });
}

/** The inbox PAGE, which must be able to reach every message.
 *
 * The server caps a page at 50 (`core/pagination.MAX_LIMIT` bounds it at 200),
 * and a member accumulates roughly three notices per claim — so a year of
 * ordinary claiming passes 50. Printing `total` above 50 rows with no way to
 * reach the rest strands the OLDEST messages, which is exactly where an
 * unanswered "we need something else" would be sitting. */
export function usePortalMessagePages() {
  return useInfiniteQuery({
    queryKey: ["portal", "messages", "pages"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      portalApi.get<ClaimMessageList>(
        `/portal/messages?offset=${pageParam}&limit=${PAGE_SIZE}`,
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
      void qc.invalidateQueries({ queryKey: ["portal", "messages"] });
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
      void qc.invalidateQueries({ queryKey: ["portal", "messages"] });
    },
    meta: { localErrorHandling: true },
  });
}
