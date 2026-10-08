import { useMemo } from "react";
import { useMe, type AccessibleClient } from "@/api/hooks";
import { usePlatformFirms } from "@/api/platform";

export interface CompanyGroup {
  firmId: string;
  firmName: string;
  clients: AccessibleClient[];
}

export interface CompanyGrouping {
  /** True when the caller's companies span more than one broker firm. */
  grouped: boolean;
  groups: CompanyGroup[];
  /** The firm a company belongs to, when it is known. */
  firmNameFor: (clientId: string | null | undefined) => string | null;
}

/** A platform admin's companies can belong to several broker firms (the owner
 *  firm plus any firm opened by an access grant), and two firms may each have a
 *  company with the same short name. Group them by firm so the switcher always
 *  says whose data is being opened.
 *
 *  Firm names come from the company row when the API sends one, then the
 *  platform firm list (fetched only in the platform console, only when a name
 *  is missing), then `/me.firm`. Everyone else has one firm and no grouping. */
export function useCompanyGroups(): CompanyGrouping {
  const { data: me } = useMe();
  const clients = useMemo(() => me?.accessible_clients ?? [], [me]);
  const firmIds = new Set(clients.map((c) => c.broker_firm_id).filter(Boolean));
  const multiFirm = firmIds.size > 1;
  const needsNames = multiFirm && clients.some((c) => c.broker_firm_id && !c.firm_name);
  const { data: firms } = usePlatformFirms(
    needsNames && me?.platform_console === true,
  );

  return useMemo(() => {
    const names = new Map<string, string>();
    if (me?.firm) names.set(me.firm.id, me.firm.name);
    for (const f of firms ?? []) names.set(f.id, f.name);
    for (const c of clients) {
      if (c.broker_firm_id && c.firm_name) names.set(c.broker_firm_id, c.firm_name);
    }
    const nameOf = (firmId: string | null | undefined) =>
      firmId ? (names.get(firmId) ?? null) : null;

    const byFirm = new Map<string, CompanyGroup>();
    for (const c of clients) {
      const firmId = c.broker_firm_id ?? "";
      const group = byFirm.get(firmId) ?? {
        firmId,
        firmName: nameOf(firmId) ?? "Other broker",
        clients: [],
      };
      group.clients.push(c);
      byFirm.set(firmId, group);
    }
    const groups = [...byFirm.values()].sort((a, b) =>
      a.firmName.localeCompare(b.firmName),
    );
    const clientFirm = new Map(clients.map((c) => [c.id, c.broker_firm_id]));
    return {
      grouped: groups.length > 1,
      groups,
      firmNameFor: (clientId) => (clientId ? nameOf(clientFirm.get(clientId)) : null),
    };
  }, [clients, firms, me?.firm]);
}
