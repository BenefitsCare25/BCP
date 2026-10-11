import type { ArtworkState, CardFace, MemberCards } from "@/api/panelCards";
import type { CoverageOptions, PortalClaim, PortalEnrollmentData } from "@/api/portal";
import type { ConversationList } from "@/api/portalMessages";
import type { BenefitStatement, Dependant, Utilization } from "@/types";

/** One query as Home reads it: the structural part of a TanStack result, so the
 *  member portal and the broker preview each hand over their own client's
 *  queries without Home knowing which one fetched them. */
export interface HomeQuery<T> {
  data?: T;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

/** Everything the member's Home renders, independent of who fetched it. The
 *  live portal fills it from `api/portal`; the broker's employee view fills it
 *  from the read-only `/employees/{id}/portal-preview/*` mirrors. */
export interface MemberHomeSource {
  /** The signed-in member's display name; the greeting takes its familiar form. */
  displayName: string;
  /** The employer as the member recognises it: registered name, then handle. */
  companyLabel: string;
  policyYear: { year: number; start_date: string; end_date: string } | null | undefined;
  /** The SERVED capability list. Undefined until it resolves, which shows
   *  everything rather than blinking targets away (see `holds`). */
  capabilities: readonly string[] | undefined;
  enrollmentOpen: boolean;
  cards: HomeQuery<MemberCards>;
  /** Card artwork rides an Authorization header, so each surface fetches it
   *  with its own client. Called as a hook. */
  useCardArtwork: (cardId: string | null, face: CardFace, enabled?: boolean) => ArtworkState;
  utilization: HomeQuery<Utilization>;
  messages: HomeQuery<ConversationList>;
  claims: HomeQuery<{ items: PortalClaim[] }>;
  dependants: HomeQuery<Dependant[]>;
  statement: HomeQuery<BenefitStatement>;
  options: HomeQuery<CoverageOptions>;
  enrollment: HomeQuery<PortalEnrollmentData>;
}
