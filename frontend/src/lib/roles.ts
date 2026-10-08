/** Broker portal roles and what each may do — one place, so a role cannot be
 * granted by one gate and forgotten by the next.
 *
 * - `system_admin`: the platform's master admin. Belongs to no broker firm.
 * - `firm_admin`: the top role of a broker firm — its users, invitations, web
 *   addresses and every control that removes or resets saved data.
 * - `broker_admin`: manages a firm's companies and settings.
 * - `broker_viewer`: read-only.
 * - `client_admin` / `client_hr`: a company's HR logins, provisioned per
 *   company under Company settings → Authentication. */

export const ROLE_LABELS: Readonly<Record<string, string>> = {
  system_admin: "System admin",
  firm_admin: "Firm admin",
  broker_admin: "Broker admin",
  broker_viewer: "Broker viewer",
  client_admin: "HR Administrator",
  client_hr: "HR Officer",
};

/** A person-readable role name; an unknown role falls back to its code. */
export function roleLabel(role: string | null | undefined): string {
  if (!role) return "";
  return ROLE_LABELS[role] ?? role;
}

type Role = string | null | undefined;

export function isSystemAdminRole(role: Role): boolean {
  return role === "system_admin";
}

/** Firm-owner actions: users and invitations, web addresses, and removing,
 *  clearing, unlinking or resetting saved data. */
export function isFirmOwnerRole(role: Role): boolean {
  return role === "firm_admin" || role === "system_admin";
}

/** Administering a firm's companies and settings. */
export function isBrokerAdminRole(role: Role): boolean {
  return role === "broker_admin" || isFirmOwnerRole(role);
}

/** Every broker staff role, read-only viewers included. */
export function isBrokerStaffRole(role: Role): boolean {
  return role === "broker_viewer" || isBrokerAdminRole(role);
}

/** HR logins live under Company settings → Authentication, never the firm-wide
 *  Users card. */
export const CLIENT_ROLES: ReadonlySet<string> = new Set(["client_admin", "client_hr"]);

/** Staff roles the caller may grant from the Users card. Only a system admin
 *  can make another system admin. */
export function assignableStaffRoles(callerRole: Role): string[] {
  const roles = ["firm_admin", "broker_admin", "broker_viewer"];
  return isSystemAdminRole(callerRole) ? [...roles, "system_admin"] : roles;
}
