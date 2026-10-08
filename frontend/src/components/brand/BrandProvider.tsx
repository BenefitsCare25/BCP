import { createContext, useContext, useLayoutEffect, useMemo, type ReactNode } from "react";
import { useRouterState } from "@tanstack/react-router";
import { DEFAULT_BRAND, loadedPublicSite, usePublicSite, type PublicBrand } from "@/api/public";
import { applyBrand, brandCompanyForPath, markBrandProviderMounted } from "@/lib/brand";

interface BrandState {
  brand: PublicBrand;
  /** False until a brand is known (read, or its read failed). */
  ready: boolean;
  /** The broker firm serving this host, for the copyright line. */
  firmName: string | null;
}

const BrandContext = createContext<BrandState>({ brand: DEFAULT_BRAND, ready: true, firmName: null });

/** Reads `/public/site` for the current surface (with the company on the HR
 *  and employee portals) and applies its brand: palette variables, favicon and
 *  the bare document title. Until the read lands, the firm's own brand read at
 *  boot stands in; with neither, the document is left as served (today's look,
 *  or whatever the server injected) and logos hold their space empty, so a
 *  white-labelled site never flashes the platform's logo. A failed read falls
 *  back to today's look. */
export function BrandProvider({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const company = brandCompanyForPath(pathname);
  const site = usePublicSite(company);
  const resolved = site.data ?? loadedPublicSite() ?? null;
  // After a first failure the default stands in while the query retries.
  const ready = resolved !== null || site.isError || site.failureCount > 0;
  const brand = resolved?.brand ?? DEFAULT_BRAND;
  const firmName = resolved?.firm?.name ?? null;

  useLayoutEffect(() => {
    if (!ready) return;
    markBrandProviderMounted();
    applyBrand(brand);
  }, [brand, ready]);

  const value = useMemo(() => ({ brand, ready, firmName }), [brand, ready, firmName]);
  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrand(): PublicBrand {
  return useContext(BrandContext).brand;
}

export function useBrandReady(): boolean {
  return useContext(BrandContext).ready;
}

/** Who the copyright line names: the firm, else the product. */
export function useBrandOwner(): string {
  const { brand, firmName } = useContext(BrandContext);
  return firmName ?? brand.product_name;
}
