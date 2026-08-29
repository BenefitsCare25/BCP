import { PolicyYearDaysField } from "./PolicyYearDaysField";

/** Claim-submission grace period, bound to the current benefit year — the year
 * claims submit against. Blank clears the deadline entirely.
 *
 * Anchored on the PERIOD's end, never on a leaver's own last day: how long a
 * claim may be sent in for is a property of the year, and anchoring it on the
 * member would apply the leaver bound twice (see `LeaverAccessField`, which
 * owns that other bound and shares this control's shape). */
export function ClaimGracePeriodField() {
  return (
    <PolicyYearDaysField
      id="claim-grace"
      field="claim_grace_period_days"
      label="Claim submission grace period (days)"
      placeholder="No deadline"
      noYearPrompt="Select a benefit year to set a claim submission deadline."
      invalidMessage="Grace period must be a whole number from 0 to 3,650 days (or blank)."
      savedMessage="Claim grace period updated"
      explicitEdit
      hint={
        <>
          Days after the current benefit year&rsquo;s coverage period ends
          during which members may still submit claims. Leave blank for no
          submission deadline.
        </>
      }
    />
  );
}
