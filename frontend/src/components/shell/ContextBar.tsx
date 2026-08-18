import { Link, useRouterState } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, ChevronLeft, Globe } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { isCompanyPath } from "./nav";

/**
 * The scope bar under the TopBar. Company-scoped pages show which company is
 * being acted on (with a quick-switch) plus a way back to Home; firm-wide pages
 * show a "Firm-wide" banner and NO company control — so a shared-library edit
 * can never masquerade as a per-company one.
 */
export function ContextBar() {
  const router = useRouterState();
  const path = router.location.pathname;
  if (path === "/home") return null; // Home renders its own header
  if (!isCompanyPath(path)) return <FirmBanner />;
  return <CompanyContext />;
}

function FirmBanner() {
  const { data: me } = useMe();
  const count = me?.accessible_clients.length ?? 0;
  return (
    <div className="h-10 shrink-0 border-b border-border bg-warn-soft/40 px-6 flex items-center gap-2 text-xs text-foreground/70">
      <Globe className="size-3.5 text-warn" />
      <span className="font-medium text-foreground/80">Firm-wide setting</span>
      <span className="text-muted-foreground">
        · applies to all {count || ""} companies
      </span>
    </div>
  );
}

function CompanyContext() {
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);
  const setActiveClient = useSession((s) => s.setActiveClient);
  const qc = useQueryClient();

  const clients = me?.accessible_clients ?? [];
  // Under the hard gate, "chosen" == activeClientId only — no server-default
  // fallback (that would show a company the user never picked). The stale/unset
  // self-heal + single-company auto-enter live in AppShell.useActiveClientSync.
  const selected = activeClientId;

  const onChange = (id: string) => {
    if (id === selected) return;
    setActiveClient(id);
    // Cached data is scoped to the previous company — EVICT it (don't
    // invalidate). invalidateQueries() would synchronously refetch the
    // still-mounted, previous-tenant-keyed queries, whose queryFn closes over
    // the old policy-year id but reads the just-switched tenant header at fetch
    // time → a cross-tenant request that 404s ("Policy year not found").
    // removeQueries drops the entries with no in-place refetch; the new render
    // re-keys every query to the new client and fetches fresh.
    qc.removeQueries();
  };

  const activeName =
    clients.find((c) => c.id === selected)?.name ?? "Select company";

  return (
    <div className="h-10 shrink-0 border-b border-border bg-card px-6 flex items-center gap-3 text-sm">
      <Link
        to="/home"
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronLeft className="size-3.5" />
        All companies
      </Link>
      <div className="h-4 w-px bg-border" />
      <Building2 className="size-4 text-primary" />
      {clients.length > 1 ? (
        <Select value={selected ?? undefined} onValueChange={onChange}>
          <SelectTrigger
            aria-label="Select company"
            className="h-7 min-w-[180px] border-0 bg-transparent px-1 font-medium shadow-none focus:ring-0"
          >
            <SelectValue placeholder="Select company" />
          </SelectTrigger>
          <SelectContent>
            {clients.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <span className="font-medium text-foreground">{activeName}</span>
      )}
    </div>
  );
}
