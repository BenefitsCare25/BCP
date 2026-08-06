/** Questions — a thread a member starts that hangs off no claim.
 *
 * The thread itself is `MessageThread`, the same component the claim page uses;
 * these are only the hooks that open one and write in it.
 *
 * **There is no hook that creates a question about ONE claim**, deliberately.
 * The topic marked `routes_to_claim` sends the member to that claim's own
 * thread instead — a second thread tagged to a claim is two conversations about
 * one thing, each readable while the other still shows unread.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { portalApi } from "@/api/portalClient";
import type { ClaimMessage, ConversationSubject } from "@/api/portalMessages";

export interface EnquiryTopic {
  key: string;
  label: string;
  /** Opens the claim's own thread instead of creating a question. */
  routes_to_claim: boolean;
}

export interface Enquiry {
  id: string;
  topic: string;
  /** Served, like `ConversationSubject.topic_label` — see that field. */
  topic_label: string | null;
  topic_urgent: boolean;
  subject: string;
  status: "open" | "answered" | "closed";
  /** A claim this question NAMES without belonging to — context only. */
  about_claim: ConversationSubject | null;
  created_at: string;
  employee: { id: string; staff_id: string; employee_name: string | null } | null;
}

export interface EnquiryCreateInput {
  topic: string;
  subject: string;
  body: string;
  about_claim_id?: string | null;
}

/** The "What's it about?" picker. Served, so the vocabulary — and which option
 *  routes to a claim — has one home rather than a copy in this file. */
export function useEnquiryTopics() {
  return useQuery({
    queryKey: ["portal", "enquiry-topics"],
    queryFn: () => portalApi.get<EnquiryTopic[]>("/portal/enquiry-topics"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalEnquiry(enquiryId: string | null) {
  return useQuery({
    queryKey: ["portal", "enquiries", enquiryId],
    queryFn: () => portalApi.get<Enquiry>(`/portal/enquiries/${enquiryId}`),
    enabled: Boolean(enquiryId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalEnquiryMessages(enquiryId: string | null) {
  return useQuery({
    queryKey: ["portal", "enquiries", enquiryId, "messages"],
    queryFn: () =>
      portalApi.get<ClaimMessage[]>(`/portal/enquiries/${enquiryId}/messages`),
    enabled: Boolean(enquiryId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useCreateEnquiry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: EnquiryCreateInput) =>
      portalApi.post<Enquiry>("/portal/enquiries", input),
    onSuccess: () => {
      // The new thread is a conversation — it belongs at the top of the inbox
      // and in the shell's badge immediately.
      void qc.invalidateQueries({ queryKey: ["portal", "conversations"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useSendEnquiryMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { enquiryId: string; body: string }) =>
      portalApi.post<ClaimMessage>(
        `/portal/enquiries/${input.enquiryId}/messages`,
        { body: input.body },
      ),
    onSuccess: (_msg, input) => {
      void qc.invalidateQueries({
        queryKey: ["portal", "enquiries", input.enquiryId],
      });
      void qc.invalidateQueries({ queryKey: ["portal", "conversations"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Called when the member opens the thread. Mirrors the claim one, including
 *  the no-op guard — a member re-reading a settled question must not fire a
 *  write and three invalidations on every visit. */
export function useMarkEnquiryMessagesRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enquiryId: string) =>
      portalApi.post<{ marked: number }>(
        `/portal/enquiries/${enquiryId}/messages/read`,
        {},
      ),
    onSuccess: (out, enquiryId) => {
      if (out.marked === 0) return;
      void qc.invalidateQueries({ queryKey: ["portal", "enquiries", enquiryId] });
      void qc.invalidateQueries({ queryKey: ["portal", "conversations"] });
    },
    meta: { localErrorHandling: true },
  });
}
