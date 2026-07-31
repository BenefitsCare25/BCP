/** Typed calls + query hooks for panel e-cards (broker settings) and the
 * shared member-card types the renderer consumes. */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export type CardFace = "front" | "back";
export const CARD_FACES: CardFace[] = ["front", "back"];

/** Geometry is FRACTIONAL (0-1) of the artwork box: x/y anchor the field,
 * size is a fraction of artwork HEIGHT, max_width a fraction of its WIDTH. */
export interface PlacementField {
  key: string;
  face: CardFace;
  x: number;
  y: number;
  size: number;
  weight: number;
  align: "left" | "center" | "right";
  color: string;
  uppercase: boolean;
  max_width: number | null;
}

export interface CardPlacements {
  fields: PlacementField[];
}

export const DEFAULT_PLACEMENT: Omit<PlacementField, "key"> = {
  face: "front",
  x: 0.08,
  y: 0.5,
  size: 0.05,
  weight: 500,
  align: "left",
  // Ink colour PRINTED onto the broker's card artwork, not app chrome — it is
  // persisted per placement and edited by the broker, and the artwork is not
  // themed. A theme token would be wrong here: this must stay legible against
  // whatever image was uploaded, independent of the UI palette.
  color: "#111111",
  uppercase: false,
  max_width: null,
};

export interface PanelCard {
  id: string;
  insurer: string;
  panel_provider: string;
  name: string;
  display_label: string;
  has_front: boolean;
  has_back: boolean;
  aspect_ratio: number | null;
  placements: CardPlacements;
  assigned_policy_year_ids: string[];
  uploaded_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PanelCardInput {
  insurer: string;
  panel_provider: string;
  name: string;
}

export interface PolicyYearCard {
  id: string;
  policy_year_id: string;
  panel_card_id: string;
  card_name: string;
  product_id: string;
  product_code: string;
  product_name: string;
  employee_member_id_source: string;
  dependant_member_id_source: string;
  services: Record<string, boolean>;
  remarks: Record<string, string>;
  special_conditions: string | null;
  show_future_cards: boolean;
  created_at: string;
  updated_at: string;
}

export interface PolicyYearCardInput {
  panel_card_id: string;
  product_id: string;
  employee_member_id_source: string;
  dependant_member_id_source: string;
  services: Record<string, boolean>;
  remarks: Record<string, string>;
  special_conditions?: string | null;
  show_future_cards: boolean;
}

export interface CardFieldOption {
  key: string;
  label: string;
}

/** Server-owned vocabulary — the editor never hardcodes keys the API validates. */
export interface CardOptions {
  placement_keys: CardFieldOption[];
  member_id_sources: CardFieldOption[];
  services: CardFieldOption[];
  remark_keys: CardFieldOption[];
}

// ── Member-facing card payload (portal + preview share this shape) ───────────

export interface MemberCard {
  card_id: string;
  assignment_id: string;
  holder_type: "employee" | "dependant";
  holder_id: string;
  holder_name: string | null;
  product_code: string;
  product_name: string;
  card_name: string;
  aspect_ratio: number | null;
  has_front: boolean;
  has_back: boolean;
  placements: CardPlacements;
  values: Record<string, string>;
  services: { key: string; label: string }[];
  remarks: Record<string, string>;
  special_conditions: string | null;
}

export interface MemberCards {
  items: MemberCard[];
}

// ── Setup history (Locations + Cards, per benefit year) ──────────────────────

export interface SetupHistoryListing {
  id: string;
  display_label: string;
  type_label: string;
  country: string;
  clinic_count: number;
}

export interface SetupHistoryCard {
  id: string;
  card_name: string;
  product_code: string;
  product_name: string;
  employee_member_id_source: string;
  dependant_member_id_source: string;
  service_labels: string[];
  remark_keys: string[];
  special_conditions: string | null;
}

export interface SetupHistoryYear {
  policy_year_id: string;
  year: number;
  status: string;
  start_date: string;
  end_date: string;
  is_current: boolean;
  listings: SetupHistoryListing[];
  cards: SetupHistoryCard[];
}

export function usePanelSetupHistory() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["panel-setup-history", cid],
    queryFn: () =>
      api.get<{ years: SetupHistoryYear[] }>("/panel-setup/history"),
  });
}

// ── Broker hooks ─────────────────────────────────────────────────────────────

export function useCardOptions() {
  return useQuery({
    queryKey: ["panel-card-options"],
    queryFn: () => api.get<CardOptions>("/panel-cards/options"),
    staleTime: Infinity,
  });
}

export function usePanelCards() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["panel-cards", cid],
    queryFn: () => api.get<PanelCard[]>("/panel-cards"),
  });
}

export function useCreatePanelCard() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: PanelCardInput) =>
      api.post<PanelCard>("/panel-cards", input),
    onSuccess: invalidate,
  });
}

export function useUpdatePanelCard() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: { id: string } & Partial<PanelCardInput>) => {
      const { id, ...body } = input;
      return api.patch<PanelCard>(`/panel-cards/${id}`, body);
    },
    onSuccess: invalidate,
  });
}

export function useDeletePanelCard() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/panel-cards/${id}`),
    onSuccess: invalidate,
  });
}

export function useUploadCardArtwork() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: { id: string; face: CardFace; file: File }) => {
      const fd = new FormData();
      fd.append("file", input.file);
      return api.upload<PanelCard>(
        `/panel-cards/${input.id}/artwork/${input.face}`,
        fd,
      );
    },
    onSuccess: invalidate,
    // The settings page surfaces the reason inline (bad file type / not an image).
    meta: { localErrorHandling: true },
  });
}

export function useDeleteCardArtwork() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: { id: string; face: CardFace }) =>
      api.delete<PanelCard>(`/panel-cards/${input.id}/artwork/${input.face}`),
    onSuccess: invalidate,
  });
}

export function useSetCardPlacements() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: { id: string; fields: PlacementField[] }) =>
      api.put<PanelCard>(`/panel-cards/${input.id}/placements`, {
        fields: input.fields,
      }),
    onSuccess: invalidate,
  });
}

export function usePolicyYearCards(policyYearId: string | undefined) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["policy-year-cards", policyYearId, cid],
    queryFn: () =>
      api.get<PolicyYearCard[]>(`/policy-years/${policyYearId}/cards`),
    // Callers pass an id validated against the ACTIVE client's year list; a
    // persisted selection can outlive a company switch.
    enabled: Boolean(policyYearId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useCreatePolicyYearCard() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: { policyYearId: string; body: PolicyYearCardInput }) =>
      api.post<PolicyYearCard>(
        `/policy-years/${input.policyYearId}/cards`,
        input.body,
      ),
    onSuccess: invalidate,
  });
}

export function useUpdatePolicyYearCard() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: {
      policyYearId: string;
      assignmentId: string;
      body: PolicyYearCardInput;
    }) =>
      api.put<PolicyYearCard>(
        `/policy-years/${input.policyYearId}/cards/${input.assignmentId}`,
        input.body,
      ),
    onSuccess: invalidate,
  });
}

export function useDeletePolicyYearCard() {
  const invalidate = useCardInvalidator();
  return useMutation({
    mutationFn: (input: { policyYearId: string; assignmentId: string }) =>
      api.delete<void>(
        `/policy-years/${input.policyYearId}/cards/${input.assignmentId}`,
      ),
    onSuccess: invalidate,
  });
}

function useCardInvalidator() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["panel-cards"] });
    void queryClient.invalidateQueries({ queryKey: ["policy-year-cards"] });
    void queryClient.invalidateQueries({ queryKey: ["panel-setup-history"] });
    void queryClient.invalidateQueries({ queryKey: ["portal-preview", "cards"] });
  };
}

/** Artwork rides an Authorization header, so it can't be loaded with a plain
 * <img src>. Fetch the blob once and hand the renderer an object URL, revoking
 * it on unmount/refetch so blobs don't accumulate. */
export function useArtworkObjectUrl(
  fetchBlob: (path: string) => Promise<Blob>,
  path: string | null,
): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) {
      setUrl(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    void fetchBlob(path)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // fetchBlob is a stable module-level function on both surfaces.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return url;
}

/** Broker-side artwork (config editor + employee view). */
export function useBrokerCardArtwork(
  cardId: string | null,
  face: CardFace,
  enabled = true,
): string | null {
  return useArtworkObjectUrl(
    (path) => api.download(path),
    cardId && enabled ? `/panel-cards/${cardId}/artwork/${face}` : null,
  );
}
