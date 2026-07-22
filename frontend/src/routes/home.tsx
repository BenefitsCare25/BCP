import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  CalendarClock,
  Loader2,
  Plus,
  ReceiptText,
  Search,
  Users,
} from "lucide-react";
import {
  type CompanySummary,
  useCreateClient,
  useDashboardSummary,
  useMe,
} from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kpi } from "@/components/ui/kpi";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { toast } from "sonner";

export function HomePage() {
  const { data, isLoading, isError } = useDashboardSummary();
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const [q, setQ] = useState("");

  const companies = data?.companies ?? [];
  const filtered = useMemo(
    () =>
      companies.filter((c) =>
        c.name.toLowerCase().includes(q.trim().toLowerCase()),
      ),
    [companies, q],
  );

  const attention = useMemo(() => buildAttention(companies), [companies]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading your companies…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="p-8 text-sm text-muted-foreground">
        Couldn’t load the dashboard just now.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Your companies</h1>
        <p className="text-sm text-muted-foreground">
          {data.firm.company_count} companies · pick one to manage its benefits,
          members and claims.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Companies" value={data.firm.company_count} icon={Building2} />
        <Kpi label="Members" value={data.firm.member_count} icon={Users} />
        <Kpi
          label="Claims to review"
          value={data.firm.claims_to_review}
          icon={ReceiptText}
          tone={data.firm.claims_to_review > 0 ? "warn" : "default"}
        />
        <Kpi
          label="Windows open"
          value={data.firm.windows_open}
          icon={CalendarClock}
        />
      </div>

      {attention.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-foreground">
            Needs attention
          </h2>
          <ul className="space-y-1.5">
            {attention.map((a) => (
              <li
                key={a.key}
                className="flex items-center gap-2 text-sm text-foreground/80"
              >
                <span
                  className={cn(
                    "inline-block size-1.5 rounded-full",
                    a.tone === "warn" ? "bg-warn" : "bg-error",
                  )}
                />
                <span className="font-medium text-foreground">{a.company}</span>
                <span className="text-muted-foreground">· {a.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-between gap-3">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search companies…"
            className="pl-8"
          />
        </div>
        {canAdmin && <AddCompany />}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((c) => (
          <CompanyCard key={c.id} company={c} />
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            No companies match “{q}”.
          </div>
        )}
      </div>
    </div>
  );
}

type Attention = {
  key: string;
  company: string;
  message: string;
  tone: "warn" | "error";
};

function buildAttention(companies: CompanySummary[]): Attention[] {
  const out: Attention[] = [];
  for (const c of companies) {
    if (c.claims_to_review > 0) {
      out.push({
        key: `${c.id}-claims`,
        company: c.name,
        message: `${c.claims_to_review} claim${c.claims_to_review === 1 ? "" : "s"} to review`,
        tone: "warn",
      });
    }
    if (!c.current_year) {
      out.push({
        key: `${c.id}-year`,
        company: c.name,
        message: "no current benefit year set",
        tone: "error",
      });
    }
  }
  return out.slice(0, 6);
}

function CompanyCard({ company }: { company: CompanySummary }) {
  const navigate = useNavigate();
  const setActiveClient = useSession((s) => s.setActiveClient);
  const qc = useQueryClient();

  const enter = () => {
    setActiveClient(company.id);
    qc.invalidateQueries();
    navigate({ to: "/dashboard" });
  };

  const initial = company.name.trim().charAt(0).toUpperCase() || "?";

  return (
    <button
      type="button"
      onClick={enter}
      className="group flex flex-col rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-border-strong hover:bg-sidebar-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
    >
      <div className="flex items-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-md bg-accent text-sm font-semibold text-accent-foreground">
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-foreground">
            {company.name}
          </div>
          <div className="text-xs text-muted-foreground">
            {company.current_year
              ? `${company.current_year.year} · Current`
              : "No current year"}
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-4 text-sm">
        <span className="flex items-center gap-1 text-muted-foreground">
          <Users className="size-3.5" />
          <span className="tabular-nums text-foreground">
            {company.member_count}
          </span>
          members
        </span>
      </div>

      <div className="mt-3 flex min-h-[24px] flex-wrap items-center gap-1.5">
        {company.claims_to_review > 0 && (
          <Badge variant="warn">{company.claims_to_review} claims</Badge>
        )}
        {company.enrollment_open && <Badge variant="info">Enrollment open</Badge>}
        {!company.current_year && <Badge variant="error">No year</Badge>}
        {company.claims_to_review === 0 &&
          !company.enrollment_open &&
          company.current_year && <Badge variant="good">All clear</Badge>}
      </div>

      <div className="mt-3 flex items-center gap-1 text-sm font-medium text-primary">
        Open
        <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
      </div>
    </button>
  );
}

function AddCompany() {
  const create = useCreateClient();
  const [name, setName] = useState("");
  const [open, setOpen] = useState(false);

  const onCreate = async () => {
    if (!name.trim()) return;
    try {
      await create.mutateAsync(name.trim());
      toast.success("Company created");
      setName("");
      setOpen(false);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  if (!open) {
    return (
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Plus className="size-4" /> Add company
      </Button>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <Input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onCreate()}
        placeholder="Company name"
        className="w-56"
      />
      <Button onClick={onCreate} disabled={create.isPending || !name.trim()}>
        {create.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          "Create"
        )}
      </Button>
      <Button variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </div>
  );
}
