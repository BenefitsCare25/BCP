/** Today's ISO calendar date in the application's Singapore business zone. */
export function singaporeTodayISO(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Singapore",
  }).format(now);
}
