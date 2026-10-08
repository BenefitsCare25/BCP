/** Public site configuration: which broker firm this host serves and how its
 *  staff sign in. `GET /public/site` needs no session, because the host the
 *  page was loaded from selects the firm.
 *
 *  It is read once per page load. The boot sequence, MSAL and the sign-in page
 *  share one cached promise, which is dropped after a failure so a retry
 *  fetches again. Components read it through `usePublicSite`.
 *
 *  The parser keeps only the fields it knows, so the endpoint can grow without
 *  older clients misreading it: a new field is one property on `PublicSite`
 *  and one line in `parseSite`.
 *
 *  On the HR and employee portals the request names the company
 *  (`?company=<slug>`), so a company's brand override applies there. Each
 *  company is read and cached separately; the broker app reads the firm's
 *  own (no company). */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ApiError } from "@/lib/errors";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export interface SiteFirm {
  name: string;
  slug: string;
}

/** The firm's Microsoft Entra directory, as MSAL needs it. */
export interface EntraStaffSignIn {
  tenant_id: string;
  client_id: string;
  authority: string;
  scopes: string[];
}

export interface StaffSignInMethods {
  /** Null when Microsoft 365 sign-in is not offered on this host. */
  entra: EntraStaffSignIn | null;
  /** Email and password, always with two-factor verification. */
  local: boolean;
}

/** The resolved white-label brand: built-in default ← firm ← company. */
export interface PublicBrand {
  product_name: string;
  short_name: string;
  /** "#rrggbb". */
  primary_color: string;
  accent_color: string;
  /** Accessible text colours on the two brand colours, derived server-side. */
  primary_foreground: string;
  accent_foreground: string;
  logo_url: string | null;
  mark_url: string | null;
  favicon_url: string | null;
  support_email: string | null;
  support_phone: string | null;
  show_platform_attribution: boolean;
  platform_name: string;
}

/** Today's look: what every surface shows before (or without) a brand read.
 *  Mirrors the backend's built-in brand (`services/brand.py`). */
export const DEFAULT_BRAND: PublicBrand = Object.freeze({
  product_name: "Inspro",
  short_name: "Inspro",
  primary_color: "#c11a2b",
  accent_color: "#c11a2b",
  primary_foreground: "#ffffff",
  accent_foreground: "#ffffff",
  logo_url: null,
  mark_url: null,
  favicon_url: null,
  support_email: "helpdesk@inspro.com.sg",
  support_phone: null,
  show_platform_attribution: true,
  platform_name: "Inspro",
});

export interface PublicSite {
  /** Null when the host is not linked to a broker firm. */
  firm: SiteFirm | null;
  staff_sign_in: StaffSignInMethods;
  brand: PublicBrand;
}

export function publicSiteKey(company?: string | null) {
  return ["public", "site", company ?? ""] as const;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function parseFirm(raw: unknown): SiteFirm | null {
  if (!isRecord(raw)) return null;
  const name = nonEmptyString(raw.name);
  const slug = nonEmptyString(raw.slug);
  return name && slug ? { name, slug } : null;
}

/** An incomplete or non-https directory configuration is treated as "not
 *  offered" rather than handed to MSAL. */
function parseEntra(raw: unknown): EntraStaffSignIn | null {
  if (!isRecord(raw)) return null;
  const clientId = nonEmptyString(raw.client_id);
  const authority = nonEmptyString(raw.authority);
  if (!clientId || !authority || !authority.startsWith("https://")) return null;
  const scopes = Array.isArray(raw.scopes)
    ? raw.scopes.filter((scope): scope is string => typeof scope === "string" && scope.trim() !== "")
    : [];
  return {
    tenant_id: nonEmptyString(raw.tenant_id) ?? "",
    client_id: clientId,
    authority: authority.replace(/\/+$/, ""),
    scopes,
  };
}

const HEX = /^#[0-9a-f]{6}$/i;

function hexOr(value: unknown, fallback: string): string {
  return typeof value === "string" && HEX.test(value.trim()) ? value.trim().toLowerCase() : fallback;
}

/** Only a same-origin path or an https URL may become an image source or
 *  favicon; anything else (javascript:, data:, http:) is dropped. */
function assetUrl(value: unknown): string | null {
  const url = nonEmptyString(value);
  if (!url) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  return url.startsWith("https://") ? url : null;
}

/** A missing or malformed brand (an older API) falls back field by field to
 *  today's look, never to blanks. */
export function parseBrand(raw: unknown): PublicBrand {
  if (!isRecord(raw)) return DEFAULT_BRAND;
  const d = DEFAULT_BRAND;
  return {
    product_name: nonEmptyString(raw.product_name) ?? d.product_name,
    short_name: nonEmptyString(raw.short_name) ?? nonEmptyString(raw.product_name) ?? d.short_name,
    primary_color: hexOr(raw.primary_color, d.primary_color),
    accent_color: hexOr(raw.accent_color, d.accent_color),
    primary_foreground: hexOr(raw.primary_foreground, d.primary_foreground),
    accent_foreground: hexOr(raw.accent_foreground, d.accent_foreground),
    logo_url: assetUrl(raw.logo_url),
    mark_url: assetUrl(raw.mark_url),
    favicon_url: assetUrl(raw.favicon_url),
    support_email: nonEmptyString(raw.support_email),
    support_phone: nonEmptyString(raw.support_phone),
    show_platform_attribution: raw.show_platform_attribution !== false,
    platform_name: nonEmptyString(raw.platform_name) ?? d.platform_name,
  };
}

function parseSite(raw: unknown): PublicSite {
  if (!isRecord(raw)) throw new Error("The site configuration could not be read.");
  const staff = isRecord(raw.staff_sign_in) ? raw.staff_sign_in : {};
  return {
    firm: parseFirm(raw.firm),
    staff_sign_in: {
      entra: parseEntra(staff.entra),
      local: staff.local === true,
    },
    brand: parseBrand(raw.brand),
  };
}

async function fetchPublicSite(company: string): Promise<PublicSite> {
  const query = company ? `?company=${encodeURIComponent(company)}` : "";
  const response = await fetch(`${API_BASE}/public/site${query}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Sign-in options are unavailable (HTTP ${response.status}).`,
      response.status,
    );
  }
  return parseSite(await response.json());
}

const pending = new Map<string, Promise<PublicSite>>();
const loaded = new Map<string, PublicSite>();

/** The site configuration for this page load, fetched at most once per
 *  company while it succeeds. No company: the host firm's own. */
export function loadPublicSite(company?: string | null): Promise<PublicSite> {
  const key = company ?? "";
  const existing = pending.get(key);
  if (existing) return existing;
  const request: Promise<PublicSite> = fetchPublicSite(key).then(
    (site) => {
      loaded.set(key, site);
      return site;
    },
    (error: unknown) => {
      if (pending.get(key) === request) pending.delete(key);
      throw error;
    },
  );
  pending.set(key, request);
  return request;
}

/** The configuration already read, or null before (or without) a successful
 *  read. Synchronous, for route guards and the API client. */
export function loadedPublicSite(company?: string | null): PublicSite | null {
  return loaded.get(company ?? "") ?? null;
}

export function usePublicSite(company?: string | null) {
  return useQuery({
    queryKey: publicSiteKey(company),
    queryFn: () => loadPublicSite(company),
    initialData: () => loadedPublicSite(company) ?? undefined,
    // Moving between companies keeps the last brand on screen until the
    // next one is read, rather than flashing the default.
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    gcTime: Infinity,
    // The sign-in page shows its own retry; nothing goes to the alert centre.
    meta: { localErrorHandling: true },
  });
}

/** Make the next read fetch again, so it sees a saved brand change. The last
 *  successful read stays available (`loadedPublicSite`) until it is replaced:
 *  staff sign-in reads it synchronously. */
export function forgetPublicSites(): void {
  pending.clear();
}
