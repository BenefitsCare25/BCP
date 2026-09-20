import { selectedClaimPeriod, setClaimPeriod } from "@/lib/claimPeriod";
import { queryClient } from "@/lib/queryClient";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Only an opaque claim UUID can be a mail destination, never an arbitrary URL. */
export function notificationClaimId(search = window.location.search): string | undefined {
  const value = new URLSearchParams(search).get("claim") ?? "";
  return uuid.test(value) ? value : undefined;
}

export function notificationClaimYear(search = window.location.search): string | undefined {
  const value = new URLSearchParams(search).get("claim_year") ?? "";
  return uuid.test(value) ? value : undefined;
}

/** Run only after authentication so the period belongs to the signed-in member. */
export function activateNotificationClaimContext(): void {
  const year = notificationClaimYear();
  if (year && year !== selectedClaimPeriod()) {
    setClaimPeriod(year, false);
    queryClient.removeQueries({ queryKey: ["portal"] });
  }
}
