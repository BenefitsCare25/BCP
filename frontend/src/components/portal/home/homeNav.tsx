import type { ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useCompany } from "../useCompany";

export type HomeCoverageTab = "benefits" | "usage" | "dependants";

/** Where Home sends the member. Named destinations rather than routes, because
 *  the broker's preview frame reaches the same places by switching ITS tabs. */
export type HomeDest =
  | { to: "coverage"; tab: HomeCoverageTab; product?: string }
  | { to: "claims" }
  | { to: "claim"; claimId: string }
  | { to: "new-claim" }
  | { to: "messages" }
  | { to: "question"; enquiryId: string }
  | { to: "card" }
  | { to: "clinics" }
  | { to: "enrollment" };

/** How Home moves on. Without `onGo` every target is a real portal route. The
 *  broker preview passes `onGo`: a `<Link>` there would walk the broker out of
 *  their own application and into the member portal, so each target becomes a
 *  button that switches the frame's tab instead. */
export interface HomeNav {
  onGo?: (dest: HomeDest) => void;
}

export const LIVE_HOME_NAV: HomeNav = {};

type RowDest = Extract<HomeDest, { to: "claim" | "question" }>;

/** The imperative twin of `HomeLink`, for targets that are buttons on both
 *  surfaces (the Messages rows). */
export function useHomeGo(nav: HomeNav): (dest: RowDest) => void {
  const company = useCompany();
  const navigate = useNavigate();
  return (dest) => {
    if (nav.onGo) {
      nav.onGo(dest);
      return;
    }
    void navigate(
      dest.to === "question"
        ? { to: "/portal/$company/questions/$enquiryId", params: { company, enquiryId: dest.enquiryId } }
        : { to: "/portal/$company/claims/$claimId", params: { company, claimId: dest.claimId } },
    );
  };
}

/** One onward target on Home: the member's route, or the preview's button. The
 *  live branch renders exactly the `<Link>` each call site used to spell out. */
export function HomeLink({
  nav,
  dest,
  className,
  children,
}: {
  nav: HomeNav;
  dest: HomeDest;
  className?: string;
  children: ReactNode;
}) {
  const company = useCompany();
  const { onGo } = nav;
  if (onGo) {
    return (
      <button type="button" className={className} onClick={() => onGo(dest)}>
        {children}
      </button>
    );
  }
  switch (dest.to) {
    case "coverage":
      return (
        <Link
          to="/portal/$company/coverage"
          params={{ company }}
          search={dest.product ? { tab: dest.tab, p: dest.product } : { tab: dest.tab }}
          className={className}
        >
          {children}
        </Link>
      );
    case "claims":
      return <Link to="/portal/$company/claims" params={{ company }} className={className}>{children}</Link>;
    case "claim":
      return (
        <Link to="/portal/$company/claims/$claimId" params={{ company, claimId: dest.claimId }} className={className}>
          {children}
        </Link>
      );
    case "new-claim":
      return <Link to="/portal/$company/claims/new" params={{ company }} className={className}>{children}</Link>;
    case "messages":
      return <Link to="/portal/$company/messages" params={{ company }} className={className}>{children}</Link>;
    case "question":
      return (
        <Link to="/portal/$company/questions/$enquiryId" params={{ company, enquiryId: dest.enquiryId }} className={className}>
          {children}
        </Link>
      );
    case "card":
      return <Link to="/portal/$company/card" params={{ company }} className={className}>{children}</Link>;
    case "clinics":
      return <Link to="/portal/$company/clinics" params={{ company }} className={className}>{children}</Link>;
    case "enrollment":
      return <Link to="/portal/$company/enrollment" params={{ company }} className={className}>{children}</Link>;
  }
}
