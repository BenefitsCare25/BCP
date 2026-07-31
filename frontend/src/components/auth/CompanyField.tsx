/** Company selector for single-host deployments.
 *
 * With per-tenant subdomains the hostname already says which company a sign-in
 * is for. On one shared hostname it doesn't, so the user must tell us — unless
 * they arrived via an invite link carrying `?company=<slug>`, which
 * `captureTenantSlugFromUrl` already stored (then this renders nothing).
 *
 * Renders `null` in subdomain mode, so both sign-in pages can mount it
 * unconditionally.
 */
import { Building2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { needsTenantSelection, rememberTenantSlug } from "@/lib/tenant";

type Props = {
  id: string;
  value: string;
  onChange: (value: string) => void;
};

/** Whether the sign-in form must collect a company before it can submit. */
export function useCompanyRequired(): boolean {
  // Read once per render — storage only changes via this field or the entry
  // link, both of which re-render the form.
  return needsTenantSelection();
}

/** Persist the typed slug so the API client picks it up. Returns false when
 *  the value isn't a usable slug, letting the caller show a message. */
export function commitCompany(value: string): boolean {
  return rememberTenantSlug(value);
}

export function CompanyField({ id, value, onChange }: Props) {
  if (!needsTenantSelection()) return null;
  return (
    <div className="space-y-1.5">
      <Label
        htmlFor={id}
        className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
      >
        Company
      </Label>
      <div className="relative">
        <Building2 className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
        <Input
          id={id}
          type="text"
          autoComplete="organization"
          spellCheck={false}
          placeholder="your-company"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-12 pl-11"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        The company code from your invitation email.
      </p>
    </div>
  );
}
