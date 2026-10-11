import {
  useCoverageOptions, usePortalCardArtwork, usePortalCards, usePortalClaims, usePortalDependants,
  usePortalEnrollment, usePortalMe, usePortalStatement, usePortalUtilization,
} from "@/api/portal";
import { usePortalConversations } from "@/api/portalMessages";
import { usePortalSession } from "@/stores/portalSession";
import { LIVE_HOME_NAV } from "./homeNav";
import { MemberHomeView } from "./MemberHomeView";

/** The member's own Home: their portal queries in, `MemberHomeView` out. The
 *  broker's employee view renders the same view from its preview mirrors. */
export function MemberHome() {
  const member = usePortalSession((state) => state.member);
  const me = usePortalMe();
  const cards = usePortalCards();
  const utilization = usePortalUtilization();
  const messages = usePortalConversations();
  const claims = usePortalClaims();
  const dependants = usePortalDependants();
  const statement = usePortalStatement();
  const options = useCoverageOptions();
  const enrollment = usePortalEnrollment();

  return (
    <MemberHomeView
      nav={LIVE_HOME_NAV}
      source={{
        displayName: member?.display_name || "",
        companyLabel: me.data?.company?.legal_name || me.data?.company?.name || "",
        policyYear: me.data?.policy_year,
        capabilities: me.data?.access.capabilities,
        enrollmentOpen: Boolean(me.data?.enrollment_open),
        cards,
        useCardArtwork: usePortalCardArtwork,
        utilization,
        messages,
        claims,
        dependants,
        statement,
        options,
        enrollment,
      }}
    />
  );
}
