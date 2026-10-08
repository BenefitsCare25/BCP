import type { DomainStatus, DomainSurface } from "@/api/platform";

export const SURFACE_OPTIONS: { value: DomainSurface; label: string }[] = [
  { value: "all", label: "Staff and client sites" },
  { value: "staff", label: "Staff site only" },
  { value: "client", label: "Employee and HR portals only" },
];

export function surfaceLabel(surface: DomainSurface): string {
  return SURFACE_OPTIONS.find((o) => o.value === surface)?.label ?? surface;
}

export const DOMAIN_STATUS: Record<
  DomainStatus,
  { label: string; variant: "good" | "warn" | "default" }
> = {
  pending: { label: "Pending", variant: "warn" },
  active: { label: "Active", variant: "good" },
  disabled: { label: "Disabled", variant: "default" },
};

/** A fully qualified hostname: two or more DNS labels, lowercase. */
const HOSTNAME = /^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/;

/** Lowercase, trimmed, without a scheme, path or trailing dot — what a person
 *  pastes from a browser bar becomes the bare hostname. */
export function normaliseHostname(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//, "")
    .replace(/[/?#].*$/, "")
    .replace(/\.$/, "");
}

/** Why a hostname cannot be used, or null when it can. */
export function hostnameProblem(hostname: string): string | null {
  if (!hostname) return "Enter a web address, for example portal.yourbroker.com.";
  if (hostname.includes(":")) return "Leave out the port number.";
  if (!HOSTNAME.test(hostname)) {
    return "Use a full web address such as portal.yourbroker.com — letters, numbers, hyphens and dots only.";
  }
  return null;
}

/** A DNS label, as used for a firm's alias. */
const DNS_LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;

export function aliasProblem(alias: string): string | null {
  if (!alias) return null;
  if (!DNS_LABEL.test(alias)) {
    return "Use lowercase letters, numbers and hyphens (not at the start or end), up to 63 characters.";
  }
  return null;
}
