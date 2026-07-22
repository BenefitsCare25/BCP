import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Building2, Home, Search } from "lucide-react";
import { useDashboardSummary, useMe } from "@/api/hooks";
import { Input } from "@/components/ui/input";
import { useSession } from "@/stores/session";

/**
 * The hard-gate prompt: a company page can't render until the user has
 * deliberately chosen which company they're acting on. This blocking overlay
 * lets them pick without leaving the page they navigated to — on select we set
 * the active client and the page behind renders for it (no redirect needed).
 *
 * Single-company users never see this (AppShell auto-enters their one company);
 * it only appears when there's a genuine choice and none has been made yet.
 */
export function CompanyPickerDialog() {
  const { data: me } = useMe();
  const { data: summary } = useDashboardSummary();
  const setActiveClient = useSession((s) => s.setActiveClient);
  const qc = useQueryClient();
  const [q, setQ] = useState("");

  const clients = me?.accessible_clients ?? [];
  const statsById = useMemo(() => {
    const map = new Map<string, { members: number; year: number | null }>();
    for (const c of summary?.companies ?? []) {
      map.set(c.id, {
        members: c.member_count,
        year: c.current_year?.year ?? null,
      });
    }
    return map;
  }, [summary]);

  const filtered = clients.filter((c) =>
    c.name.toLowerCase().includes(q.trim().toLowerCase()),
  );

  const pick = (id: string) => {
    setActiveClient(id);
    // Data behind the overlay is scoped to the (previously none/other) client —
    // drop caches so the now-visible page fetches against the chosen tenant.
    qc.invalidateQueries();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-lg">
        <div className="flex items-center gap-2">
          <Building2 className="size-5 text-primary" />
          <h2 className="text-base font-semibold text-foreground">
            Select a company
          </h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Choose which company you want to work on.
        </p>

        {clients.length > 6 && (
          <div className="relative mt-3">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search companies…"
              className="pl-8"
            />
          </div>
        )}

        <ul className="mt-3 max-h-[50vh] space-y-1 overflow-y-auto">
          {filtered.map((c) => {
            const stats = statsById.get(c.id);
            return (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => pick(c.id)}
                  className="group flex w-full items-center gap-3 rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors hover:border-border-strong hover:bg-sidebar-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <div className="flex size-8 items-center justify-center rounded-md bg-accent text-sm font-semibold text-accent-foreground">
                    {c.name.trim().charAt(0).toUpperCase() || "?"}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">
                      {c.name}
                    </div>
                    {stats && (
                      <div className="text-xs text-muted-foreground">
                        {stats.year ? `${stats.year} · ` : ""}
                        {stats.members} members
                      </div>
                    )}
                  </div>
                  <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                </button>
              </li>
            );
          })}
          {filtered.length === 0 && (
            <li className="px-1 py-3 text-sm text-muted-foreground">
              No companies match “{q}”.
            </li>
          )}
        </ul>

        <Link
          to="/home"
          className="mt-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <Home className="size-3.5" />
          Back to Home
        </Link>
      </div>
    </div>
  );
}
