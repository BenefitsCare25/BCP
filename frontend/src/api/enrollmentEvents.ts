import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { portalApi } from "@/api/portalClient";

export interface EnrollmentEvent {
  id: string;
  enrollment_id: string;
  kind: string;
  title: string;
  message: string;
  reason: string | null;
  created_at: string;
  read_at: string | null;
  email_status: string;
  email_detail: string | null;
  window_name: string;
  closes_at: string;
}

export function useEnrollmentEvents(id: string) {
  return useQuery({
    queryKey: ["enrollment-events", id],
    queryFn: () => api.get<EnrollmentEvent[]>(`/enrollments/${id}/events`),
    refetchInterval: 15000,
  });
}

export function useEnrollmentNotices() {
  return useQuery({
    queryKey: ["portal", "enrollment-notices"],
    queryFn: () => portalApi.passiveGet<{ items: EnrollmentEvent[]; unread: number }>("/portal/enrollment/notices"),
    refetchInterval: 30000,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useEnrollmentState(id?: string) {
  return useQuery({
    queryKey: ["portal", "enrollment-state", id],
    queryFn: () => portalApi.passiveGet<{
      window_status: "draft" | "open" | "closed";
      opens_at: string; closes_at: string; member_self_service: boolean;
      latest_event_id: string | null;
    }>(`/portal/enrollment/state/${id}`),
    enabled: !!id,
    refetchInterval: 30000,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useReadEnrollmentNotice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => portalApi.post(`/portal/enrollment/notices/${id}/read`, {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["portal", "enrollment-notices"] }),
  });
}

export function useRetryEnrollmentEmail(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (eventId: string) => api.post<EnrollmentEvent>(`/enrollments/${id}/events/${eventId}/retry-email`, {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["enrollment-events", id] }),
    // The activity list toasts the refusal itself (e.g. no active web address).
    meta: { localErrorHandling: true },
  });
}
