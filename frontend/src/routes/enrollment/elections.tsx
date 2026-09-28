/** Enrollment → Members: every member of a period, filterable by where they
 * stand, and one member's selection beside the list.
 *
 * URL carries `window`, `status` and `member`, so the Overview's "Review →"
 * links and the close dialog's member names land on exactly that view. */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { AlertTriangle, CheckCheck, Loader2, Users } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import {
  type EnrollmentStatus,
  type EnrollmentWindow,
  type WindowProgress,
  useConfirmSubmitted,
  useEnrollmentRoster,
  useEnrollmentWindows,
  useWindowProgress,
} from "@/api/enrollment";
import { EmployeePicker } from "@/components/operations/EmployeePicker";
import { MemberElectionPanel } from "@/components/enrollment/members/MemberElectionPanel";
import {
  PHASE_META,
  STATUS_META,
  STATUS_ORDER,
  deadlineSentence,
  phaseOf,
  useNow,
} from "@/components/enrollment/period/periodMeta";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { cn } from "@/lib/cn";

const PAGE = 50;

type Search = { window?: string; status?: string; member?: string };

export function EnrollmentElectionsPage({ readOnly = false }: { readOnly?: boolean }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const search = useSearch({ strict: false }) as Search;
  const navigate = useNavigate();
  const now = useNow();
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const liveWindows = useMemo(
    () => (windows ?? []).filter((w) => w.status === "open"),
    [windows],
  );
  const window = liveWindows.find((w) => w.id === search.window) ?? liveWindows[0];
  const status = STATUS_ORDER.includes(search.status as EnrollmentStatus)
    ? (search.status as EnrollmentStatus)
    : undefined;
  const progress = useWindowProgress(window?.id);

  const setSearch = (patch: Partial<Search>) =>
    void navigate({
      to: "/client-relations/enrollment",
      search: {
        tab: "members",
        window: window?.id,
        status,
        member: search.member,
        ...patch,
      },
      replace: true,
    });

  if (!policyYearId) {
    return <p className="text-sm text-muted-foreground">Select a benefit year first.</p>;
  }
  if (!window) return windows ? <NoOpenPeriod /> : null;
  const overdue = phaseOf(window, now) === "overdue";

  return (
    <div className="space-y-4">
      <MembersHeader
        window={window}
        liveWindows={liveWindows}
        progress={progress.data}
        readOnly={readOnly}
        now={now}
        onPickWindow={(id) => setSearch({ window: id, member: undefined })}
      />
      {overdue && (
        <p className="flex items-start gap-2 rounded-lg bg-warn-soft/60 px-4 py-2.5 text-sm text-foreground">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          The deadline has passed: selections can be confirmed but not changed. Extend
          the deadline on the Overview tab to make changes.
        </p>
      )}
      <StatusFilters
        progress={progress.data}
        status={status}
        onChange={(s) => setSearch({ status: s })}
      />
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <RosterColumn
          windowId={window.id}
          status={status}
          selected={search.member ?? null}
          onSelect={(id) => setSearch({ member: id })}
        />
        {search.member ? (
          <MemberElectionPanel
            key={search.member}
            enrollmentId={search.member}
            window={window}
            readOnly={readOnly}
          />
        ) : (
          <div className="rounded-xl border border-dashed border-border p-10 text-center">
            <Users className="mx-auto size-6 text-muted-foreground" aria-hidden />
            <p className="mt-2 text-sm text-muted-foreground">
              Pick a member to see and change their selection.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function NoOpenPeriod() {
  return (
    <section className="rounded-xl border border-dashed border-border-strong bg-card px-6 py-10 text-center">
      <Users className="mx-auto size-6 text-muted-foreground" aria-hidden />
      <p className="mt-2 text-sm text-foreground">No enrolment period is open.</p>
      <p className="text-sm text-muted-foreground">
        Members appear here once a period opens on the Overview tab.
      </p>
    </section>
  );
}

function MembersHeader({
  window,
  liveWindows,
  progress,
  readOnly,
  now,
  onPickWindow,
}: {
  window: EnrollmentWindow;
  liveWindows: EnrollmentWindow[];
  progress: WindowProgress | undefined;
  readOnly: boolean;
  now: number;
  onPickWindow: (id: string) => void;
}) {
  const confirmAll = useConfirmSubmitted();
  const phase = phaseOf(window, now);
  const submitted = progress?.submitted ?? 0;
  return (
    <div className="flex flex-wrap items-center gap-3">
      {liveWindows.length > 1 ? (
        <NativeSelect
          aria-label="Enrolment period"
          value={window.id}
          onChange={(e) => onPickWindow(e.target.value)}
          className="w-64"
        >
          {liveWindows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </NativeSelect>
      ) : (
        <h2 className="text-base font-semibold text-foreground">{window.name}</h2>
      )}
      <Badge variant={PHASE_META[phase].badge}>{PHASE_META[phase].label}</Badge>
      <span className="text-sm text-muted-foreground">{deadlineSentence(window, now)}</span>
      {!readOnly && submitted > 0 && (
        <Button
          size="sm"
          className="ml-auto"
          disabled={confirmAll.isPending}
          onClick={() =>
            confirmAll.mutate(window.id, {
              onSuccess: (r) =>
                r.failed.length
                  ? toast.warning(
                      `${r.confirmed} confirmed; ${r.failed.length} need review (filter: Submitted).`,
                    )
                  : toast.success(`${r.confirmed} selections confirmed.`),
            })
          }
        >
          {confirmAll.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <CheckCheck className="size-3.5" aria-hidden />
          )}
          Confirm all submitted ({submitted})
        </Button>
      )}
    </div>
  );
}

function StatusFilters({
  progress: p,
  status,
  onChange,
}: {
  progress: WindowProgress | undefined;
  status: EnrollmentStatus | undefined;
  onChange: (s: EnrollmentStatus | undefined) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
      <FilterChip selected={!status} onClick={() => onChange(undefined)}>
        Everyone <Count n={p?.total} />
      </FilterChip>
      {STATUS_ORDER.filter((s) => (p?.[s] ?? 0) > 0 || s === status).map((s) => (
        <FilterChip key={s} selected={status === s} onClick={() => onChange(s)}>
          <span className={cn("size-2 rounded-full", STATUS_META[s].fill)} aria-hidden />
          {STATUS_META[s].label} <Count n={p?.[s]} />
        </FilterChip>
      ))}
    </div>
  );
}

function RosterColumn({
  windowId,
  status,
  selected,
  onSelect,
}: {
  windowId: string;
  status: EnrollmentStatus | undefined;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  // A new filter or query starts from the first page.
  useEffect(() => setPage(0), [q, status, windowId]);
  const roster = useEnrollmentRoster(windowId, {
    q: q.trim() || undefined,
    status,
    offset: page * PAGE,
    limit: PAGE,
  });
  const total = roster.data?.total ?? 0;
  return (
    <div>
      <EmployeePicker
        items={(roster.data?.items ?? []).map((it) => ({
          id: it.id,
          name: it.employee_name ?? it.staff_id,
          subtitle: it.staff_id,
          trailing: <StatusTag status={it.status as EnrollmentStatus} />,
        }))}
        selectedId={selected}
        onSelect={onSelect}
        isLoading={roster.isLoading}
        query={q}
        onQueryChange={setQ}
        emptyText={status ? "Nobody has this status." : "No members match."}
        header={
          <p className="px-1 pb-2 text-2xs text-muted-foreground">
            {total.toLocaleString()} {status ? STATUS_META[status].label.toLowerCase() : "members"}
          </p>
        }
      />
      <PaginationControls
        page={page}
        pages={Math.max(1, Math.ceil(total / PAGE))}
        onPageChange={setPage}
      />
    </div>
  );
}

function StatusTag({ status }: { status: EnrollmentStatus }) {
  const meta = STATUS_META[status] ?? STATUS_META.not_started;
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 text-2xs", meta.text)}>
      <span className={cn("size-1.5 rounded-full", meta.fill)} aria-hidden />
      {meta.label}
    </span>
  );
}

function Count({ n }: { n: number | undefined }) {
  return (
    <span className="tabular-nums text-muted-foreground">
      {n === undefined ? "" : n.toLocaleString()}
    </span>
  );
}

function FilterChip({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        selected
          ? "border-border-strong bg-muted text-foreground"
          : "border-border bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
