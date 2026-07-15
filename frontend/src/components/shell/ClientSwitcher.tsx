import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Building2 } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";

export function ClientSwitcher() {
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);
  const setActiveClient = useSession((s) => s.setActiveClient);
  const qc = useQueryClient();

  const clients = me?.accessible_clients ?? [];
  const selected = activeClientId ?? me?.active_client_id ?? null;

  // Adopt the server-resolved active client when our stored selection is unset
  // OR stale (not in accessible_clients — e.g. carried over from a previous
  // user on this browser, or access revoked). Without this, a persisted but
  // inaccessible client id would keep being sent as the tenant header.
  useEffect(() => {
    if (!me) return;
    const accessible = new Set(me.accessible_clients.map((c) => c.id));
    const stale = activeClientId != null && !accessible.has(activeClientId);
    if ((activeClientId == null || stale) && me.active_client_id) {
      setActiveClient(me.active_client_id);
    }
  }, [me, activeClientId, setActiveClient]);

  // Nothing to switch between — hide the control for single-client users.
  if (clients.length <= 1) return null;

  const onChange = (id: string) => {
    if (id === selected) return;
    setActiveClient(id);
    // All cached data is scoped to the previous client — drop it so every
    // active query refetches against the newly selected tenant.
    qc.invalidateQueries();
  };

  return (
    <div className="flex items-center gap-2">
      <Building2 className="size-4 text-muted-foreground" />
      <Select value={selected ?? undefined} onValueChange={onChange}>
        <SelectTrigger className="h-8 min-w-[200px]">
          <SelectValue placeholder="Select client" />
        </SelectTrigger>
        <SelectContent>
          {clients.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
