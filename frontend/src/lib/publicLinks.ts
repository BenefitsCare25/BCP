/** No-login pages opened from a QR or emailed link: the shared employee
 *  enrolment form and the HR download page. They carry their capability in
 *  the URL fragment, need no session, and use the portal look and languages. */
export const PUBLIC_LINK_PATHS = ["/enrol", "/hr-download"] as const;

export function isPublicLinkPath(path: string): boolean {
  return PUBLIC_LINK_PATHS.some(prefix => path === prefix || path.startsWith(`${prefix}/`));
}
