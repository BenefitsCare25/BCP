/** Typed calls + query hooks for panel clinic listings (broker settings)
 * and the shared clinic-search types used by the locator UIs. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export type ClinicType = "gp" | "tcm" | "dental" | "sp";
export type PanelCountry = "SG" | "MY";

export const CLINIC_TYPE_OPTIONS: { value: ClinicType; label: string }[] = [
  { value: "gp", label: "GP" },
  { value: "tcm", label: "TCM" },
  { value: "dental", label: "Dental" },
  { value: "sp", label: "Specialist" },
];

export const COUNTRY_OPTIONS: { value: PanelCountry; label: string }[] = [
  { value: "SG", label: "Singapore" },
  { value: "MY", label: "Malaysia (JB)" },
];

export interface PanelListing {
  id: string;
  insurer: string;
  panel_provider: string;
  country: PanelCountry;
  clinic_type: ClinicType;
  label: string | null;
  display_label: string;
  type_label: string;
  clinic_count: number;
  source_filename: string | null;
  uploaded_at: string | null;
  tagged_policy_year_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface PanelListingInput {
  insurer: string;
  panel_provider: string;
  country: PanelCountry;
  clinic_type: ClinicType;
  label?: string | null;
}

export interface PanelUploadResult {
  listing: PanelListing;
  rows_total: number;
  imported: number;
  skipped_no_name: number;
  missing_coordinates: number;
}

export interface Clinic {
  id: string;
  name: string;
  code: string | null;
  zone: string | null;
  area: string | null;
  specialty: string | null;
  doctor: string | null;
  address: string | null;
  postal_code: string | null;
  phone: string | null;
  hours: {
    mon_fri?: string;
    sat?: string;
    sun?: string;
    public_holiday?: string;
  } | null;
  latitude: number | null;
  longitude: number | null;
  google_map_url: string | null;
  clinic_type: ClinicType;
  country: PanelCountry;
  type_label: string;
  panel_label: string;
  distance_km: number | null;
}

export interface ClinicTypeFacet {
  clinic_type: ClinicType;
  country: PanelCountry;
  label: string;
  count: number;
}

export interface ClinicSearch {
  total: number;
  offset: number;
  limit: number;
  located: boolean;
  items: Clinic[];
  filters: { clinic_types: ClinicTypeFacet[]; areas: string[] };
}

export interface ClinicSearchParams {
  clinic_type?: ClinicType;
  country?: PanelCountry;
  area?: string;
  q?: string;
  lat?: number;
  lng?: number;
  offset?: number;
  limit?: number;
}

/** Serialize locator params — shared by the portal + preview fetchers. */
export function clinicSearchQuery(params: ClinicSearchParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export interface PolicyYearPanels {
  policy_year_id: string;
  panel_listing_ids: string[];
}

// ── Broker hooks ──────────────────────────────────────────────────────────────

export function usePanelListings() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["panel-listings", cid],
    queryFn: () => api.get<PanelListing[]>("/panel-listings"),
  });
}

export function useCreatePanelListing() {
  const invalidate = usePanelInvalidator();
  return useMutation({
    mutationFn: (input: PanelListingInput) =>
      api.post<PanelListing>("/panel-listings", input),
    onSuccess: invalidate,
  });
}

export function useUpdatePanelListing() {
  const invalidate = usePanelInvalidator();
  return useMutation({
    mutationFn: (input: { id: string } & Partial<PanelListingInput>) => {
      const { id, ...body } = input;
      return api.patch<PanelListing>(`/panel-listings/${id}`, body);
    },
    onSuccess: invalidate,
  });
}

export function useDeletePanelListing() {
  const invalidate = usePanelInvalidator();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/panel-listings/${id}`),
    onSuccess: invalidate,
  });
}

export function useUploadPanelList() {
  const invalidate = usePanelInvalidator();
  return useMutation({
    mutationFn: (input: { id: string; file: File }) => {
      const fd = new FormData();
      fd.append("file", input.file);
      return api.upload<PanelUploadResult>(
        `/panel-listings/${input.id}/upload`,
        fd,
      );
    },
    onSuccess: invalidate,
    // The settings page shows a contextual result (imported/skipped counts).
    meta: { localErrorHandling: true },
  });
}

export function usePolicyYearPanels(policyYearId: string | undefined) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["policy-year-panels", policyYearId, cid],
    queryFn: () =>
      api.get<PolicyYearPanels>(`/policy-years/${policyYearId}/panels`),
    // Callers must pass an id validated against the ACTIVE client's year list
    // (a persisted selection can outlive a company switch or a deleted year);
    // localErrorHandling keeps any residual 404 from toasting globally.
    enabled: Boolean(policyYearId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

/** One company's enablement row for a listing (the "Enable for companies"
 * dialog). policy_year_id is the target year the checkbox acts on. */
export interface ListingCompany {
  client_id: string;
  client_name: string;
  policy_year_id: string | null;
  policy_year_label: string | null;
  enabled: boolean;
}

export function useListingCompanies(listingId: string | null) {
  return useQuery({
    queryKey: ["panel-listing-companies", listingId],
    queryFn: () =>
      api.get<ListingCompany[]>(`/panel-listings/${listingId}/companies`),
    enabled: Boolean(listingId),
  });
}

export function useSetListingCompanies() {
  const invalidate = usePanelInvalidator();
  return useMutation({
    mutationFn: (input: { listingId: string; clientIds: string[] }) =>
      api.put<ListingCompany[]>(`/panel-listings/${input.listingId}/companies`, {
        client_ids: input.clientIds,
      }),
    onSuccess: invalidate,
  });
}

export function useSetPolicyYearPanels() {
  const invalidate = usePanelInvalidator();
  return useMutation({
    mutationFn: (input: { policyYearId: string; panelListingIds: string[] }) =>
      api.put<PolicyYearPanels>(`/policy-years/${input.policyYearId}/panels`, {
        panel_listing_ids: input.panelListingIds,
      }),
    onSuccess: invalidate,
  });
}

function usePanelInvalidator() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["panel-listings"] });
    void queryClient.invalidateQueries({ queryKey: ["policy-year-panels"] });
    void queryClient.invalidateQueries({ queryKey: ["panel-listing-companies"] });
    void queryClient.invalidateQueries({ queryKey: ["panel-setup-history"] });
    // Locator caches (portal preview) key off the same data.
    void queryClient.invalidateQueries({ queryKey: ["portal-preview", "clinics"] });
  };
}
