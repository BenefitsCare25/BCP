import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Building2, Home, Search } from "lucide-react";
import { useDashboardSummary, useMe, type AccessibleClient } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SectionLabel } from "@/components/ui/section-label";
import { useClientSelectionNotice } from "@/stores/clientSelection";
import { useSession } from "@/stores/session";
import { useCompanyGroups } from "./companyGroups";

/** Keyboard-reachable controls inside `root`, in tab order (the picker sets no
 *  positive tabindex, so document order is tab order). */
function tabbables(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => el.getClientRects().length > 0);
}

/**
 * The hard-gate prompt: a company page can't render until the user has
 * deliberately chosen which company they're acting on. This blocking overlay
 * lets them pick without leaving the page they navigated to — on select we set
 * the active client and the page behind renders for it (no redirect needed).
 *
 * Single-company users never see this (AppShell auto-enters their one company);
 * it only appears when there's a genuine choice and none has been made yet.
 *
 * After a write is refused for its company selection it leads with why, and on
 * pages that need no company it can be dismissed (`dismissible`).
 *
 * A modal dialog: focus moves in when it appears and cannot leave, Escape
 * closes it only when it is dismissible, and focus returns to where it was
 * once the shell removes it. `aria-modal` marks the page behind as inert for
 * assistive technology without stripping it from the accessibility tree, as
 * the Radix modal's `aria-hidden` would — the browser suite waits on the top
 * bar's company control while this gate is open.
 */
export function CompanyPickerDialog({ dismissible = false }: { dismissible?: boolean }) {
  const { data: me } = useMe();
  const { data: summary } = useDashboardSummary();
  const setActiveClient = useSession((s) => s.setActiveClient);
  const notice = useClientSelectionNotice((s) => s.notice);
  const clearNotice = useClientSelectionNotice((s) => s.clear);
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // Read during the first render, before anything inside (the search field's
  // autoFocus) takes focus: the shell opens this without a trigger, so this is
  // where focus goes back to when the picker is removed.
  const [returnFocus] = useState(() =>
    document.activeElement instanceof HTMLElement &&
    document.activeElement !== document.body
      ? document.activeElement
      : null,
  );

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusInside = () => (tabbables(dialog)[0] ?? dialog).focus();
    if (!dialog.contains(document.activeElement)) focusInside();
    // Nothing behind the overlay can be used while it is open, so focus that
    // reaches it anyway (a programmatic focus) is brought back.
    const onFocusIn = (event: FocusEvent) => {
      if (event.target instanceof Node && !dialog.contains(event.target)) focusInside();
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, [returnFocus]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      // The hard gate cannot be waved away: the page behind has nothing to
      // render until a company is chosen.
      if (dismissible) {
        event.stopPropagation();
        clearNotice();
      }
      return;
    }
    const dialog = dialogRef.current;
    if (event.key !== "Tab" || !dialog) return;
    const items = tabbables(dialog);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

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

  // Across several broker firms the list is grouped by firm, and a search
  // also matches the firm's name.
  const { grouped, groups } = useCompanyGroups();
  const query = q.trim().toLowerCase();
  const visibleGroups = groups
    .map((g) => ({
      ...g,
      clients: g.clients.filter(
        (c) =>
          c.name.toLowerCase().includes(query) ||
          g.firmName.toLowerCase().includes(query),
      ),
    }))
    .filter((g) => g.clients.length > 0);
  const filtered = grouped
    ? visibleGroups.flatMap((g) => g.clients)
    : clients.filter((c) => c.name.toLowerCase().includes(query));

  const pick = (id: string) => {
    setActiveClient(id);
    clearNotice();
    // Data behind the overlay is scoped to the (previously none/other) client —
    // EVICT it so the now-visible page fetches against the chosen tenant. Not
    // invalidateQueries(): that would refetch previous-tenant-keyed queries
    // in-place with the just-switched header → a cross-tenant 404.
    qc.removeQueries();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      // A click on the backdrop would otherwise drop focus to the page body,
      // outside the dialog.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) event.preventDefault();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-lg outline-none"
      >
        <div className="flex items-center gap-2">
          <Building2 className="size-5 text-primary" aria-hidden="true" />
          <h2 id={titleId} className="text-base font-semibold text-foreground">
            Select a company
          </h2>
        </div>
        <p id={descriptionId} className="mt-1 text-sm text-muted-foreground">
          Choose which company you want to work on.
        </p>
        {notice && (
          <p
            role="alert"
            className="mt-3 rounded-md border border-border bg-warn-soft px-3 py-2 text-sm text-warn"
          >
            {notice}
          </p>
        )}

        {clients.length > 6 && (
          <div className="relative mt-3">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search companies…"
              aria-label="Search companies"
              className="pl-8"
            />
          </div>
        )}

        {grouped ? (
          <div className="mt-3 max-h-[50vh] space-y-3 overflow-y-auto">
            {visibleGroups.map((g) => {
              const headingId = `${titleId}-firm-${g.firmId}`;
              return (
                <section key={g.firmId} aria-labelledby={headingId}>
                  <SectionLabel as="h3" id={headingId} className="px-1 pb-1">
                    {g.firmName}
                  </SectionLabel>
                  <ul className="space-y-1">
                    {g.clients.map((c) => (
                      <CompanyOption
                        key={c.id}
                        client={c}
                        stats={statsById.get(c.id)}
                        onPick={pick}
                      />
                    ))}
                  </ul>
                </section>
              );
            })}
            {filtered.length === 0 && (
              <p className="px-1 py-3 text-sm text-muted-foreground">
                No companies match “{q}”.
              </p>
            )}
          </div>
        ) : (
          <ul className="mt-3 max-h-[50vh] space-y-1 overflow-y-auto">
            {filtered.map((c) => (
              <CompanyOption
                key={c.id}
                client={c}
                stats={statsById.get(c.id)}
                onPick={pick}
              />
            ))}
            {filtered.length === 0 && (
              <li className="px-1 py-3 text-sm text-muted-foreground">
                No companies match “{q}”.
              </li>
          )}
        </ul>
        )}

        <div className="mt-4 flex items-center justify-between gap-2">
          <Link
            to="/home"
            onClick={clearNotice}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <Home className="size-3.5" aria-hidden="true" />
            Back to Home
          </Link>
          {dismissible && (
            <Button variant="ghost" size="sm" onClick={clearNotice}>
              Not now
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function CompanyOption({
  client,
  stats,
  onPick,
}: {
  client: AccessibleClient;
  stats: { members: number; year: number | null } | undefined;
  onPick: (id: string) => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(client.id)}
        className="group flex w-full items-center gap-3 rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors hover:border-border-strong hover:bg-sidebar-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      >
        <div
          className="flex size-8 items-center justify-center rounded-md bg-accent text-sm font-semibold text-accent-foreground"
          aria-hidden="true"
        >
          {client.name.trim().charAt(0).toUpperCase() || "?"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-foreground">
            {client.name}
          </div>
          {stats && (
            <div className="text-xs text-muted-foreground">
              {stats.year ? `${stats.year} · ` : ""}
              {stats.members} members
            </div>
          )}
        </div>
        <ArrowRight
          className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
          aria-hidden="true"
        />
      </button>
    </li>
  );
}
