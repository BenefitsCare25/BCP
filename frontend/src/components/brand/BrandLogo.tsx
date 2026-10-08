import { cn } from "@/lib/cn";
import { isPlatformOwnBrand, showsAttribution } from "@/lib/brand";
import { useBrand, useBrandReady } from "./BrandProvider";

/** Which bundled file stands in on the platform's own brand. */
const FALLBACK = {
  /** Broker sidebar. */
  full: "/inspro-logo.png",
  /** HR and employee portal headers. */
  header: "/inspro-logo-header.png",
  /** Sign-in pages. */
  lockup: "/inspro-logo-mark.png",
} as const;

/** The firm's logo. Its uploaded logo when there is one; on the platform's own
 *  brand, today's bundled file; otherwise its mark beside the product name, so
 *  a white-labelled site never falls back to the platform's logo. */
export function BrandLogo({
  variant,
  className,
  width,
  height,
  decorative = false,
  wordmarkClassName,
}: {
  variant: keyof typeof FALLBACK;
  /** Applied to the <img>. */
  className?: string;
  /** Intrinsic size of the bundled fallback, for layout stability. */
  width?: number;
  height?: number;
  /** Beside visible text that already names the product (alt=""). */
  decorative?: boolean;
  /** Applied to the text wordmark used when the brand has no logo. */
  wordmarkClassName?: string;
}) {
  const brand = useBrand();
  const ready = useBrandReady();
  const alt = decorative ? "" : brand.product_name;
  // Holds the logo's space until the brand is known.
  if (!ready) return <span aria-hidden="true" className={cn("inline-block", className)} />;
  if (brand.logo_url) {
    return <img src={brand.logo_url} alt={alt} className={cn("object-contain", className)} />;
  }
  if (isPlatformOwnBrand(brand)) {
    return <img src={FALLBACK[variant]} alt={alt} width={width} height={height} className={className} />;
  }
  return (
    <span
      className={cn("inline-flex min-w-0 items-center gap-2 font-semibold text-foreground", wordmarkClassName)}
      aria-hidden={decorative || undefined}
    >
      {brand.mark_url && <img src={brand.mark_url} alt="" className="size-8 shrink-0 rounded-sm object-contain" />}
      <span className="truncate">{brand.product_name}</span>
    </span>
  );
}

/** The quiet "Powered by …" line, when the firm's brand shows it. */
export function PoweredBy({ className }: { className?: string }) {
  const brand = useBrand();
  const ready = useBrandReady();
  if (!ready || !showsAttribution(brand)) return null;
  return <p className={cn("text-xs text-muted-foreground", className)}>Powered by {brand.platform_name}</p>;
}
