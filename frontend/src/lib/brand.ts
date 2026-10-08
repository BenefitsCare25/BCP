/** Applying a firm's brand to the document, plus the colour arithmetic the
 *  brand settings need for their live contrast check.
 *
 *  Palette: the stylesheets carry today's colours as literals. A brand whose
 *  colours differ from the built-in default sets `--brand-*` on `<html>` and
 *  `data-brand="custom"`; the `[data-brand="custom"]` rules (styles/brand.css,
 *  and the scoped sign-in sheets) then derive every tint from those. A default
 *  brand therefore renders byte-for-byte today's palette, and a server that
 *  injects the same attributes into `index.html` gets a flash-free first
 *  paint with no client change. */
import { DEFAULT_BRAND, loadPublicSite, type PublicBrand } from "@/api/public";
import { currentHrTenantSlug, currentPortalTenantSlug } from "@/lib/tenant";
import { setBaseDocumentTitle } from "@/lib/useDocumentTitle";

export const HEX_COLOR = /^#[0-9a-f]{6}$/i;

const PALETTE_VARS = [
  ["--brand-primary", "primary_color"],
  ["--brand-primary-foreground", "primary_foreground"],
  ["--brand-accent", "accent_color"],
  ["--brand-accent-foreground", "accent_foreground"],
] as const satisfies ReadonlyArray<readonly [string, keyof PublicBrand]>;

/** True when any brand colour differs from today's palette. */
export function hasCustomPalette(brand: PublicBrand): boolean {
  return PALETTE_VARS.some(
    ([, field]) => String(brand[field]).toLowerCase() !== String(DEFAULT_BRAND[field]).toLowerCase(),
  );
}

export function applyBrandPalette(brand: PublicBrand): void {
  const root = document.documentElement;
  if (hasCustomPalette(brand)) {
    for (const [name, field] of PALETTE_VARS) root.style.setProperty(name, String(brand[field]));
    root.dataset.brand = "custom";
  } else {
    for (const [name] of PALETTE_VARS) root.style.removeProperty(name);
    delete root.dataset.brand;
  }
  document
    .querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    ?.setAttribute("content", brand.primary_color);
}

const DEFAULT_HREF = "data-default-href";
const DEFAULT_TYPE = "data-default-type";

/** Points every `rel="icon"` link at the brand favicon, or back at the
 *  bundled icons when the brand has none. */
export function applyFavicon(url: string | null): void {
  const links = document.querySelectorAll<HTMLLinkElement>('link[rel="icon"]');
  links.forEach((link) => {
    if (!link.hasAttribute(DEFAULT_HREF)) {
      link.setAttribute(DEFAULT_HREF, link.getAttribute("href") ?? "");
      link.setAttribute(DEFAULT_TYPE, link.getAttribute("type") ?? "");
    }
    const href = url ?? link.getAttribute(DEFAULT_HREF) ?? "";
    if (link.getAttribute("href") !== href) link.setAttribute("href", href);
    const type = url ? "" : link.getAttribute(DEFAULT_TYPE) ?? "";
    if (type) link.setAttribute("type", type);
    else link.removeAttribute("type");
  });
}

/** The platform's own brand (product name = platform name): its bundled
 *  logo files are the right fallback. Any other brand never shows them. */
export function isPlatformOwnBrand(brand: PublicBrand): boolean {
  return brand.product_name.trim().toLowerCase() === brand.platform_name.trim().toLowerCase();
}

/** "Powered by …" shows when the brand asks for it and is not the platform's
 *  own: on the platform's own sites it would only repeat the product name. */
export function showsAttribution(brand: PublicBrand): boolean {
  return brand.show_platform_attribution && !isPlatformOwnBrand(brand);
}

// ── Contrast ────────────────────────────────────────────────────────────────

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of a "#rrggbb" colour. */
export function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** White or black, whichever reads better on `hex`. A preview of an unsaved
 *  colour; the saved brand's foreground comes from the server. */
export function readableForeground(hex: string): string {
  return contrastRatio(hex, "#ffffff") >= contrastRatio(hex, "#000000") ? "#ffffff" : "#000000";
}

// ── Applying ────────────────────────────────────────────────────────────────

export function applyBrand(brand: PublicBrand): void {
  applyBrandPalette(brand);
  applyFavicon(brand.favicon_url);
  setBaseDocumentTitle(brand.product_name);
}

/** The company whose brand a page shows: the tenant of the HR and employee
 *  portals, none (the host firm's own) everywhere else. */
export function brandCompanyForPath(pathname: string): string | null {
  if (pathname === "/portal" || pathname.startsWith("/portal/")) return currentPortalTenantSlug() || null;
  if (pathname === "/hr" || pathname.startsWith("/hr/")) return currentHrTenantSlug() || null;
  return null;
}

let providerMounted = false;

/** Called by `BrandProvider` once it owns the document's brand, so a late
 *  boot read can no longer overwrite what the current route shows. */
export function markBrandProviderMounted(): void {
  providerMounted = true;
}

/** Start reading the brand before React renders and apply it the moment it
 *  arrives. The broker app awaits the same read during boot, so it paints
 *  branded; the portals usually have it before their route chunk loads. */
export function primeBrand(): void {
  loadPublicSite(brandCompanyForPath(window.location.pathname)).then(
    (site) => {
      if (!providerMounted) applyBrand(site.brand);
    },
    () => {
      // Today's look stays; BrandProvider retries through its query.
    },
  );
}
