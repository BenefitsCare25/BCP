/** Firm console brand API — the firm's white-label brand, per-company
 *  overrides and their image assets.
 *
 *  Served to the firm's `firm_admin` (and a `system_admin` holding a write
 *  grant). Every write carries the `revision` it was based on; a stale one is
 *  refused with 409 `brand_revision_stale`. Keys carry the active company, as
 *  in `api/firm.ts`: it selects the firm a platform admin is looking at. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useSession } from "@/stores/session";
import { ConflictDetailError, errorCode, errorStatus, formatError } from "@/lib/errors";
import { api } from "./client";
import { forgetPublicSites, parseBrand, type PublicBrand } from "./public";

export type BrandAssetSlot = "logo" | "mark" | "favicon";
export const BRAND_ASSET_SLOTS: readonly BrandAssetSlot[] = ["logo", "mark", "favicon"];

export interface BrandAssetMeta {
  id: string;
  content_type: string;
  width: number;
  height: number;
  bytes: number;
  /** Public URL of the stored image. */
  url: string;
}

export type BrandAssets = Partial<Record<BrandAssetSlot, BrandAssetMeta>>;

/** Fields a firm and a company override share. Null inherits the layer
 *  below (company ← firm ← built-in). */
export interface BrandFields {
  product_name: string | null;
  short_name: string | null;
  primary_color: string | null;
  accent_color: string | null;
  support_email: string | null;
  support_phone: string | null;
  email_sender_name: string | null;
  email_reply_to: string | null;
}

/** Firm-governed fields: a company override that sets one is refused (422
 *  `brand_firm_only_field`). The card prefix is copied onto each company at
 *  creation, so changing it never renumbers existing cards. */
export interface FirmOnlyFields {
  email_from_address: string | null;
  show_platform_attribution: boolean | null;
  card_prefix: string | null;
}

export interface FirmBrandSettings extends BrandFields, FirmOnlyFields {
  assets: BrandAssets;
  revision: number;
}

export interface FirmBrand {
  settings: FirmBrandSettings;
  effective: PublicBrand;
  entitlements: { allow_hide_attribution: boolean };
  /** The From address and when the platform verified it (null: not yet). */
  email_from: { address: string | null; verified_at: string | null };
}

export type FirmBrandUpdate = BrandFields & FirmOnlyFields & { revision: number };

/** With no override every field is null and `revision` is 0. */
export interface CompanyBrandSettings extends BrandFields {
  assets: BrandAssets;
  revision: number;
}

export interface CompanyBrand {
  client_id: string;
  settings: CompanyBrandSettings;
  /** The company's resolved brand (its override over the firm's). */
  effective: PublicBrand;
  /** The firm brand it inherits from. */
  inherited: PublicBrand;
}

export function hasCompanyOverride(brand: CompanyBrand): boolean {
  return brand.settings.revision > 0;
}

export type CompanyBrandUpdate = BrandFields & { revision: number };

/** A brand refusal as one sentence: coded 422s carry `detail.message`, and a
 *  413 is an image over the limit. */
export function brandErrorMessage(error: unknown): string {
  if (errorStatus(error) === 413) return "Image is larger than 512 KB.";
  return formatError(error);
}

/** The brand changed under this form since it was loaded. */
export function isBrandRevisionStale(error: unknown): boolean {
  return (
    (error instanceof ConflictDetailError && error.detail.code === "brand_revision_stale") ||
    errorCode(error) === "brand_revision_stale"
  );
}

const firmBrandKey = (cid: string | null) => ["firm", "brand", cid] as const;
const companyBrandKey = (cid: string | null, clientId: string) =>
  ["firm", "brand", cid, "company", clientId] as const;

/** A saved brand shows on this very page (the broker app's own palette and
 *  logo), and on the platform console's view of the firm. */
function refreshBrandEverywhere(qc: QueryClient) {
  forgetPublicSites();
  void qc.invalidateQueries({ queryKey: ["public", "site"] });
  void qc.invalidateQueries({ queryKey: ["platform", "firms"] });
}

function parseFirmBrand(raw: FirmBrand): FirmBrand {
  return {
    ...raw,
    settings: { ...raw.settings, assets: raw.settings?.assets ?? {} },
    effective: parseBrand(raw.effective),
    entitlements: { allow_hide_attribution: raw.entitlements?.allow_hide_attribution === true },
    email_from: {
      address: raw.email_from?.address ?? null,
      verified_at: raw.email_from?.verified_at ?? null,
    },
  };
}

export function useFirmBrand(enabled = true) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: firmBrandKey(cid),
    queryFn: async () => parseFirmBrand(await api.get<FirmBrand>("/firm/brand")),
    enabled,
  });
}

export function useUpdateFirmBrand() {
  const qc = useQueryClient();
  const cid = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: async (body: FirmBrandUpdate) =>
      parseFirmBrand(await api.put<FirmBrand>("/firm/brand", body)),
    onSuccess: (saved) => {
      qc.setQueryData(firmBrandKey(cid), saved);
      // Company overrides inherit from the firm, so their resolved brand moved.
      void qc.invalidateQueries({ queryKey: ["firm", "brand", cid, "company"] });
      refreshBrandEverywhere(qc);
    },
    meta: { localErrorHandling: true },
  });
}

function parseCompanyBrand(raw: CompanyBrand): CompanyBrand {
  return {
    client_id: raw.client_id,
    settings: { ...raw.settings, assets: raw.settings?.assets ?? {}, revision: raw.settings?.revision ?? 0 },
    effective: parseBrand(raw.effective),
    inherited: parseBrand(raw.inherited),
  };
}

/** Always answers; a company without an override has all-null settings at
 *  revision 0. */
export function useCompanyBrand(clientId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: companyBrandKey(cid, clientId ?? ""),
    queryFn: async () => parseCompanyBrand(await api.get<CompanyBrand>(`/firm/brand/companies/${clientId}`)),
    enabled: Boolean(clientId),
  });
}

export function useUpdateCompanyBrand(clientId: string) {
  const qc = useQueryClient();
  const cid = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: async (body: CompanyBrandUpdate) =>
      parseCompanyBrand(await api.put<CompanyBrand>(`/firm/brand/companies/${clientId}`, body)),
    onSuccess: (saved) => {
      qc.setQueryData(companyBrandKey(cid, clientId), saved);
      refreshBrandEverywhere(qc);
    },
    meta: { localErrorHandling: true },
  });
}

export function useDeleteCompanyBrand(clientId: string) {
  const qc = useQueryClient();
  const cid = useSession((s) => s.activeClientId);
  return useMutation({
    /** `revision`: the override last read; a newer one is refused as stale. */
    mutationFn: async (revision: number) =>
      parseCompanyBrand(
        await api.delete<CompanyBrand>(`/firm/brand/companies/${clientId}?revision=${revision}`),
      ),
    onSuccess: (saved) => {
      qc.setQueryData(companyBrandKey(cid, clientId), saved);
      refreshBrandEverywhere(qc);
    },
    meta: { localErrorHandling: true },
  });
}

/** `scope`: "firm", or the client id of a company override. */
function assetPath(slot: BrandAssetSlot, scope: string): string {
  return `/firm/brand/assets/${slot}?scope=${encodeURIComponent(scope)}`;
}

function invalidateScope(qc: QueryClient, cid: string | null, scope: string) {
  void qc.invalidateQueries({
    queryKey: scope === "firm" ? firmBrandKey(cid) : companyBrandKey(cid, scope),
  });
  if (scope === "firm") void qc.invalidateQueries({ queryKey: ["firm", "brand", cid, "company"] });
  refreshBrandEverywhere(qc);
}

export function useUploadBrandAsset(scope: string) {
  const qc = useQueryClient();
  const cid = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: ({ slot, file }: { slot: BrandAssetSlot; file: File }) => {
      const form = new FormData();
      form.append("file", file);
      // Answers the scope's whole brand (firm or company); refetched below.
      return api.upload<unknown>(assetPath(slot, scope), form);
    },
    onSuccess: () => invalidateScope(qc, cid, scope),
    meta: { localErrorHandling: true },
  });
}

export function useDeleteBrandAsset(scope: string) {
  const qc = useQueryClient();
  const cid = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: (slot: BrandAssetSlot) => api.delete<void>(assetPath(slot, scope)),
    onSuccess: () => invalidateScope(qc, cid, scope),
    meta: { localErrorHandling: true },
  });
}
