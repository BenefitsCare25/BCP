/** The portal's chrome.
 *
 * Designed at phone width and *widened* for desktop — never a desktop layout
 * squeezed down.
 *
 * **Desktop is ONE row**: mark, pill navigation, benefit-year selector, account
 * controls. There is deliberately no primary action in it. A wayfinding row is
 * made of destinations, so an action dropped at its end has nothing to belong
 * to — it floated after the last link, crowded the bar's bottom edge, and
 * competed with the year selector above it. "Submit a claim" lives in the page
 * instead, which also makes the two viewports agree: it is a full-width pill on
 * a phone for exactly the same reason.
 *
 * **The brand appears at most twice** (The Twice Rule): the mark, and the one
 * action in the page. That is why the active navigation item is a filled
 * `bg-shade` pill with ink text rather than a red underline — a bar carrying a
 * red logo, a red rule and a red button made the button read as a sticker.
 *
 * The mark is **excluded on mobile** by request, and the phone bar carries the
 * member's name instead. Exactly one `h1` is in the accessibility tree at any
 * viewport: the two below are toggled with `hidden`, which is `display:none` and
 * therefore removes the other from the tree entirely.
 *
 * Mirrored by the broker preview in `components/operations/PortalFrame` —
 * change both together. */
import { useState, type ComponentType } from "react";
import {
  Link,
  Outlet,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import {
  CalendarCheck,
  CreditCard,
  FileText,
  LayoutGrid,
  Layers,
  LogOut,
  MapPin,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { usePortalMe, useMemberSecurityStatus } from "@/api/portal";
import { usePortalSession } from "@/stores/portalSession";
import { LeafScopeContext } from "@/lib/leaf-scope";
import { cn } from "@/lib/cn";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { BenefitYearControl } from "./BenefitYearControl";
import { glassSurface } from "./leaf/Mount";
import { HeadRailProvider, useHeadRailWidth } from "./leaf/HeadRail";

/** Six destinations on desktop, five in the phone dock — "Home" is the dock's
 * first slot and Coverage is reached from the tiles that summarise it, so the
 * dock never needs an overflow menu.
 *
 * Labels are short because the desktop bar is one row: "My coverage" and
 * friends do not fit beside the mark, the year selector and three controls at
 * 1180px. */
const NAV: {
  label: string;
  short: string;
  to: string;
  icon: ComponentType<{ className?: string }>;
  /** In the phone dock. Coverage is not — it is one tap from the home tiles. */
  dock: boolean;
}[] = [
  { label: "Home", short: "Home", to: "/portal", icon: LayoutGrid, dock: true },
  {
    label: "Coverage",
    short: "Cover",
    to: "/portal/coverage",
    icon: Layers,
    dock: false,
  },
  {
    label: "Claims",
    short: "Claims",
    to: "/portal/claims",
    icon: FileText,
    dock: true,
  },
  { label: "Card", short: "Card", to: "/portal/card", icon: CreditCard, dock: true },
  {
    label: "Clinics",
    short: "Clinics",
    to: "/portal/clinics",
    icon: MapPin,
    dock: true,
  },
  {
    label: "Enrolment",
    short: "Enrol",
    to: "/portal/enrollment",
    icon: CalendarCheck,
    dock: true,
  },
];

const ICON_BUTTON =
  "leaf-focus inline-flex size-11 shrink-0 items-center justify-center rounded-pill " +
  "text-label transition-colors duration-200 ease-leaf hover:bg-shade hover:text-record";

export function PortalShell() {
  const { location } = useRouterState();
  const navigate = useNavigate();
  const member = usePortalSession((s) => s.member);
  const clearSession = usePortalSession((s) => s.clearSession);
  const { data: me } = usePortalMe();
  const { data: security } = useMemberSecurityStatus();

  // The heading row's centre slot. It only exists wide enough to seat a strip
  // beside the name and the year control; below that a route renders its own
  // furniture in place. See leaf/HeadRail.
  const wideEnoughForRail = useHeadRailWidth();
  const [rail, setRail] = useState<HTMLDivElement | null>(null);

  // Company turned 2FA on but this member hasn't finished enrolling.
  const mustEnrollMfa =
    !!security?.mfa_available &&
    security.mfa_status !== "confirmed" &&
    location.pathname !== "/portal/security";

  const signOut = () => {
    clearSession();
    void navigate({ to: "/portal/sign-in" });
  };

  // "/portal" would otherwise prefix-match every route and mark Home active
  // everywhere.
  const isActive = (to: string) =>
    to === "/portal"
      ? location.pathname === "/portal" || location.pathname === "/portal/"
      : location.pathname.startsWith(to);

  const who = member?.display_name || member?.email || "";
  const year = me?.policy_year;

  const accountControls = (
    <>
      <NotificationBell />
      <Link
        to="/portal/security"
        aria-label="Account security"
        className={cn(
          ICON_BUTTON,
          isActive("/portal/security") && "text-action-ink",
        )}
      >
        <ShieldCheck className="size-5" aria-hidden />
      </Link>
      <button type="button" onClick={signOut} aria-label="Sign out" className={ICON_BUTTON}>
        <LogOut className="size-5" aria-hidden />
      </button>
    </>
  );

  return (
    // The scope flag and the `leaf` class are two halves of one statement: the
    // class re-points the tokens, the flag tells shared components they are on
    // a touch surface (so their help stops hiding behind hover). Set together,
    // always.
    <LeafScopeContext.Provider value>
      {/* min-h-dvh, not min-h-screen: on mobile Safari `100vh` is the browser's
          *largest* viewport, so the last row of any page sat under the URL bar. */}
      <div className="leaf flex min-h-dvh flex-col">
        <header className="border-b border-hairline bg-bar">
          {/* ── Phone: name, scope, account. No mark, by request. ────────── */}
          <div className="mx-auto flex max-w-5xl items-center gap-2.5 px-4 py-2.5 sm:hidden">
            <h1 className="min-w-0 flex-1 truncate text-base font-bold tracking-title text-record">
              {who}
            </h1>
            {year && (
              <BenefitYearControl
                start={year.start_date}
                end={year.end_date}
                compact
              />
            )}
            <div className="flex shrink-0 items-center">{accountControls}</div>
          </div>

          {/* ── Desktop: one row. ────────────────────────────────────────── */}
          <div className="mx-auto hidden max-w-5xl items-center gap-0 px-6 py-3.5 sm:flex">
            {/* Used whole and uncropped. Its three-line wordmark needs this
                much height to stay legible, which is what decided a single tall
                row over two short ones. Served from a 50 KB derivative — the
                source asset is 792 KB and has no business in a header. */}
            <img
              src="/inspro-logo-header.png"
              alt="Inspro Insurance Brokers"
              width={162}
              height={52}
              className="h-13 w-auto shrink-0"
            />
            <span aria-hidden className="mx-5 h-8 w-px shrink-0 bg-hairline" />

            <nav aria-label="Portal sections" className="flex items-center gap-0.5">
              {NAV.map((item) => {
                const active = isActive(item.to);
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "leaf-focus inline-flex h-10 items-center gap-2 rounded-pill px-4 text-row",
                      "transition-colors duration-200 ease-leaf",
                      active
                        ? "bg-shade font-semibold text-record"
                        : "text-label hover:bg-shade hover:text-record",
                    )}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>

            {/* Only the account controls close the row. The benefit-year
                selector sits with the page heading instead: it is a scope
                control for the CONTENT, and keeping it here pushed the icons
                past the edge on a laptop. The enrolment deadline is likewise
                announced in the page (the home tile) rather than as a chip in
                the nav — time-sensitive signals live in the page. */}
            <div className="ml-auto flex shrink-0 items-center pl-6">
              {accountControls}
            </div>
          </div>
        </header>

        {/* `leaf-ground` carries the three colour blooms. They are the mechanism
            that makes the glass read as glass rather than as paler paint — see
            leaf.css. Without this class the whole material fails. */}
        <main className="leaf-ground flex-1">
          <div className="mx-auto w-full max-w-5xl px-4 py-5 pb-28 sm:px-6 sm:pb-10">
            {/* The page heading on desktop only; on a phone the bar above is
                already carrying it. `hidden` is display:none, so exactly one h1
                is ever in the accessibility tree.
                The year selector shares this row rather than stacking under the
                name — it scopes the content below it, and a period set beneath a
                name reads as a subtitle explaining the person. */}
            <div className="mb-5 hidden items-center gap-4 sm:flex">
              {/* Both flanks are `flex-1 basis-0`, so they resolve to equal
                  widths and whatever a route hangs in the rail is centred in
                  the row exactly — not merely balanced by eye. The name
                  truncates into its half rather than pushing the rail off
                  centre. */}
              <h1 className="min-w-0 flex-1 basis-0 truncate text-2xl font-bold tracking-title text-record">
                {who}
              </h1>
              {wideEnoughForRail && <div ref={setRail} className="shrink-0" />}
              <div className="flex flex-1 basis-0 justify-end">
                {year && (
                  <BenefitYearControl start={year.start_date} end={year.end_date} />
                )}
              </div>
            </div>

            {mustEnrollMfa && (
              <Link
                to="/portal/security"
                className={cn(
                  glassSurface,
                  "mb-4 flex items-start gap-3 rounded-tile p-4",
                  "leaf-focus transition-colors duration-200 ease-leaf hover:bg-glass-hover",
                )}
              >
                <ShieldAlert
                  className="mt-0.5 size-5 shrink-0 text-strike-pending"
                  aria-hidden
                />
                <span>
                  <span className="block text-row font-semibold text-record">
                    Set up two-step sign-in
                  </span>
                  <span className="block text-row text-label">
                    Your company asks everyone to add a second step when signing
                    in. It takes about a minute.
                  </span>
                </span>
              </Link>
            )}
            <HeadRailProvider value={rail}>
              <Outlet />
            </HeadRailProvider>
          </div>
        </main>

        {/* The dock. Every destination present at once, each a full 44×44
            target, and the enrollment deadline announced in the page as well as
            here — nothing a member needs on a deadline is ever reachable only by
            horizontal scroll. Floating glass rather than a bar, so the ground
            reads continuously beneath it. */}
        <nav
          aria-label="Portal sections"
          className={cn(
            glassSurface,
            "fixed inset-x-3 bottom-3 z-20 flex rounded-pill p-1.5 shadow-float sm:hidden",
            "mb-[env(safe-area-inset-bottom)]",
          )}
        >
          {NAV.filter((i) => i.dock).map((item) => {
            const active = isActive(item.to);
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "leaf-focus relative flex min-h-13 flex-1 flex-col items-center justify-center gap-1 rounded-pill px-1",
                  "transition-colors duration-200 ease-leaf",
                  active ? "bg-shade text-record" : "text-label",
                )}
              >
                {/* A dot means ONE thing in this bar: an enrolment window is
                    open and closes on a date. It deliberately does not also
                    mean "unread messages" — a mark that stands for two
                    unrelated facts explains neither, and on Home in particular
                    it read as an unexplained smudge. Unread is stated in WORDS
                    on the Messages tile ("2 unread"), which is on the screen a
                    member lands on. */}
                <span className="relative">
                  <Icon className="size-5" aria-hidden />
                  {item.to === "/portal/enrollment" && me?.enrollment_open && (
                    <span
                      className="absolute -right-1.5 -top-0.5 size-2 rounded-pill bg-strike-pending"
                      aria-hidden
                    />
                  )}
                </span>
                <span className="text-2xs font-semibold leading-none">
                  {item.short}
                </span>
                {item.to === "/portal/enrollment" && me?.enrollment_open && (
                  <span className="sr-only">(enrollment open)</span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>
    </LeafScopeContext.Provider>
  );
}
