/** The employee view's Home tab: the member's own Home (`MemberHomeView`), fed
 * from the read-only broker preview mirrors instead of the member's token. Its
 * onward targets switch the frame's tabs (`onGo`) — a portal `<Link>` here
 * would walk the broker out of their own application. */
import {
  usePreviewCards,
  usePreviewClaims,
  usePreviewConversations,
  usePreviewCoverageOptions,
  usePreviewDependants,
  usePreviewEnrollment,
  usePreviewStatement,
  usePreviewUtilization,
  type PortalPreviewContext,
} from "@/api/portalPreview";
import { useBrokerCardArtwork } from "@/api/panelCards";
import type { HomeDest } from "@/components/portal/home/homeNav";
import { MemberHomeView } from "@/components/portal/home/MemberHomeView";

/** The name the member signs in under, as their own greeting reads it. With no
 *  portal account there is no signed-in name, so the roster name stands in. */
function previewDisplayName(ctx: PortalPreviewContext | undefined): string {
  if (!ctx) return "";
  if (ctx.member_account) return ctx.member_account.display_name || "";
  return ctx.employee.employee_name || "";
}

export function PortalFrameHome({
  employeeId,
  ctx,
  onGo,
}: {
  employeeId: string;
  ctx: PortalPreviewContext | undefined;
  onGo: (dest: HomeDest) => void;
}) {
  const cards = usePreviewCards(employeeId);
  const utilization = usePreviewUtilization(employeeId);
  const messages = usePreviewConversations(employeeId);
  const claims = usePreviewClaims(employeeId);
  const dependants = usePreviewDependants(employeeId);
  const statement = usePreviewStatement(employeeId);
  const options = usePreviewCoverageOptions(employeeId);
  const enrollment = usePreviewEnrollment(employeeId);

  return (
    <MemberHomeView
      nav={{ onGo }}
      source={{
        displayName: previewDisplayName(ctx),
        companyLabel: ctx?.company?.legal_name || ctx?.company?.name || "",
        policyYear: ctx?.policy_year,
        // Same served list the frame's tabs close on, so a leaver's preview
        // drops the same targets the member's Home does.
        capabilities: ctx?.access?.capabilities,
        enrollmentOpen: Boolean(ctx?.enrollment_open),
        cards,
        useCardArtwork: useBrokerCardArtwork,
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
