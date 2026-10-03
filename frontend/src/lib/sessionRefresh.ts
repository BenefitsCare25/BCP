/** Serialize rotating-cookie refreshes across tabs without sharing access tokens. */
export async function withSessionRefreshLock<T>(
  surface: "hr" | "portal",
  company: string,
  refresh: () => Promise<T>,
): Promise<T> {
  if (navigator.locks) {
    return navigator.locks.request(`inspro-refresh:${surface}:${company}`, refresh);
  }
  return refresh();
}
