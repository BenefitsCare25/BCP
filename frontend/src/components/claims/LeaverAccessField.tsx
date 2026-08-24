import { PolicyYearDaysField } from "./PolicyYearDaysField";

/** How long a leaver keeps the portal after their last day of service.
 *
 * A DIFFERENT bound from the grace period beside it, and the pair is easy to
 * confuse: grace is a property of the YEAR (how late any claim may be sent in),
 * this is a property of the MEMBER (how long after their own last day they can
 * still reach the portal at all). A submit must satisfy both — which is exactly
 * why they look alike on screen and share `PolicyYearDaysField`.
 *
 * Blank is not "unlimited" — it is the system default. There is deliberately no
 * unlimited value: unlimited is the defect this exists to close
 * (`docs/LEAVER_ACCESS_PLAN.md`). */
export function LeaverAccessField() {
  return (
    <PolicyYearDaysField
      id="leaver-access"
      field="leaver_access_days"
      label="Leaver portal access (days)"
      placeholder="60 (default)"
      noYearPrompt="Select a benefit year to set how long leavers keep portal access."
      invalidMessage="Leaver access must be a whole number from 0 to 3,650 days (or blank)."
      savedMessage="Leaver portal access updated"
      hint={
        <>
          Days after a member&rsquo;s last day of service that they keep portal
          access — enough to send in claims for treatment they had while
          covered. Their panel card, clinic list and enrolment close on the last
          day itself. Blank uses the default (60 days); 0 ends access on the
          last day.
        </>
      }
    />
  );
}
