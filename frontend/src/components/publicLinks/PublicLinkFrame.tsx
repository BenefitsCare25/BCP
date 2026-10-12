import type { ReactNode } from "react";
import { BrandLogo, PoweredBy } from "@/components/brand/BrandLogo";
import { usePortalTranslation } from "@/i18n/portal";
import { PortalLanguageControl } from "@/i18n/PortalLanguageControl";
import { cn } from "@/lib/cn";
import { LeafScopeContext } from "@/lib/leaf-scope";

/** The page around a no-login link: the portal's own look (Figtree, white
 *  cards, blue actions) without any of its session furniture. A visitor here
 *  has no account, so there is no navigation to offer and nothing to sign out
 *  of — only the company the link belongs to and the language switch. */
export function PublicLinkFrame({
  company,
  children,
  wide = false,
  enrolment = false,
}: {
  company?: string | null;
  children: ReactNode;
  /** The enrolment form uses the wide deck; the download page stays narrow. */
  wide?: boolean;
  enrolment?: boolean;
}) {
  const pt = usePortalTranslation();
  return (
    <LeafScopeContext.Provider value>
      <div className={cn("leaf portal-clay portal-ui flex min-h-dvh flex-col", enrolment && "is-enrolment")}>
        <header className="clay-topbar">
          <div className="clay-topbar-inner mx-auto flex max-w-[1440px] items-center gap-3 py-2.5 pl-4 pr-2 sm:pl-5 sm:pr-3">
            <BrandLogo variant="header" width={125} height={40} className="h-9 w-auto shrink-0 sm:h-10" />
            {company && (
              <p className="min-w-0 flex-1 truncate pl-1 text-row font-semibold text-record">
                <span className="sr-only">{pt("Company")}: </span>{company}
              </p>
            )}
            <div className={cn("shrink-0", !company && "ml-auto")}>
              <PortalLanguageControl />
            </div>
          </div>
        </header>
        <main className="leaf-ground flex-1 px-4 pb-10 pt-5 sm:px-6 sm:pt-7">
          <div className={cn("mx-auto w-full", wide ? "max-w-6xl" : "max-w-xl")}>{children}</div>
        </main>
        <footer className="px-4 pb-6 text-center">
          <PoweredBy />
        </footer>
      </div>
    </LeafScopeContext.Provider>
  );
}

/** A full-page state on a link page: closed, expired, not found, done. One
 *  heading, the reason, and what to do next — never a dead end. */
export function LinkNotice({
  icon,
  title,
  children,
  tone = "neutral",
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  tone?: "neutral" | "good" | "warn";
}) {
  const pt = usePortalTranslation();
  return (
    <section
      className="leaf-mount mx-auto flex max-w-xl flex-col items-center gap-3 rounded-tile border border-glass-edge bg-bar px-6 py-8 text-center shadow-mount"
      aria-labelledby="link-notice-title"
    >
      <span
        aria-hidden
        className={cn(
          "grid size-12 place-items-center rounded-full",
          tone === "good" && "bg-strike-approved/12 text-strike-approved",
          tone === "warn" && "bg-strike-pending/15 text-strike-pending",
          tone === "neutral" && "bg-action-wash text-action-ink",
        )}
      >
        {icon}
      </span>
      <h1 id="link-notice-title" className="text-xl font-bold tracking-title text-record">{pt(title)}</h1>
      {children && <div className="space-y-2 text-row text-label">{children}</div>}
    </section>
  );
}
