import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { type FirmActivity, fmtCount, signInTotal } from "@/api/platformActivity";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtDateTime, parseServerDate } from "@/lib/format";

type SortKey =
  | "companies"
  | "members_active"
  | "staff_active"
  | "hr_users"
  | "sign_ins"
  | "active_sessions"
  | "claims_submitted"
  | "claims_open"
  | "enrolments_submitted"
  | "ai_tokens"
  | "last_activity_at";

interface Column {
  key: SortKey;
  label: string;
  value: (firm: FirmActivity) => number | null;
}

const COLUMNS: Column[] = [
  { key: "companies", label: "Companies", value: (f) => f.companies },
  { key: "members_active", label: "Members", value: (f) => f.members_active },
  { key: "staff_active", label: "Staff", value: (f) => f.staff_active },
  { key: "hr_users", label: "HR users", value: (f) => f.hr_users },
  { key: "sign_ins", label: "Sign-ins", value: (f) => signInTotal(f.sign_ins) },
  { key: "active_sessions", label: "Live sessions", value: (f) => f.active_sessions },
  { key: "claims_submitted", label: "Claims submitted", value: (f) => f.claims_submitted },
  { key: "claims_open", label: "Open claims", value: (f) => f.claims_open },
  { key: "enrolments_submitted", label: "Enrolments", value: (f) => f.enrolments_submitted },
  { key: "ai_tokens", label: "AI tokens", value: (f) => f.ai_tokens },
];

const LAST_ACTIVITY: Column = {
  key: "last_activity_at",
  label: "Last activity",
  value: (f) => (f.last_activity_at ? parseServerDate(f.last_activity_at).getTime() : null),
};

interface Sort {
  key: SortKey;
  descending: boolean;
}

/** Sort by one column; firms without a value always go last. */
function sortFirms(firms: FirmActivity[], sort: Sort): FirmActivity[] {
  const column = sort.key === LAST_ACTIVITY.key
    ? LAST_ACTIVITY
    : COLUMNS.find((c) => c.key === sort.key) ?? LAST_ACTIVITY;
  return [...firms].sort((a, b) => {
    const left = column.value(a);
    const right = column.value(b);
    if (left === null || right === null) {
      return left === right ? a.name.localeCompare(b.name) : left === null ? 1 : -1;
    }
    const order = sort.descending ? right - left : left - right;
    return order !== 0 ? order : a.name.localeCompare(b.name);
  });
}

function SortHeader({
  column,
  sort,
  onSort,
  align = "right",
}: {
  column: Column;
  sort: Sort;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === column.key;
  const Icon = !active ? ArrowUpDown : sort.descending ? ArrowDown : ArrowUp;
  return (
    <TableHead
      aria-sort={active ? (sort.descending ? "descending" : "ascending") : "none"}
      className={align === "right" ? "text-right" : undefined}
    >
      <button
        type="button"
        onClick={() => onSort(column.key)}
        className="inline-flex items-center gap-1 whitespace-nowrap rounded-sm hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {column.label}
        <Icon
          className={active ? "size-3.5 text-foreground" : "size-3.5 opacity-50"}
          aria-hidden="true"
        />
      </button>
    </TableHead>
  );
}

function FirmCell({ firm }: { firm: FirmActivity }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Link
        to="/platform/firms/$firmId"
        params={{ firmId: firm.firm_id }}
        className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {firm.name}
      </Link>
      {firm.is_platform_owner && <Badge variant="info">Platform owner</Badge>}
      {firm.status === "suspended" && <Badge variant="error">Suspended</Badge>}
    </div>
  );
}

/** Every broker's activity counters, sortable by any numeric column. */
export function ActivityTable({ firms, days }: { firms: FirmActivity[]; days: number }) {
  const [sort, setSort] = useState<Sort>({ key: "last_activity_at", descending: true });
  const rows = useMemo(() => sortFirms(firms, sort), [firms, sort]);
  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, descending: !s.descending } : { key, descending: true }));

  return (
    <Table>
      <caption className="sr-only">
        Broker activity over the last {days} days. Column headers sort the table.
      </caption>
      <TableHeader>
        <TableRow>
          <TableHead>Broker</TableHead>
          {COLUMNS.map((column) => (
            <SortHeader key={column.key} column={column} sort={sort} onSort={onSort} />
          ))}
          <SortHeader column={LAST_ACTIVITY} sort={sort} onSort={onSort} align="left" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((firm) => (
          <TableRow key={firm.firm_id}>
            <TableCell>
              <FirmCell firm={firm} />
            </TableCell>
            {COLUMNS.map((column) => (
              <TableCell key={column.key} className="text-right tabular-nums">
                {fmtCount(column.value(firm))}
                {column.key === "staff_active" && firm.staff_invited > 0 && (
                  <span className="block text-2xs text-muted-foreground">
                    +{firm.staff_invited} invited
                  </span>
                )}
              </TableCell>
            ))}
            <TableCell className="whitespace-nowrap text-muted-foreground">
              {firm.last_activity_at ? fmtDateTime(firm.last_activity_at) : "None yet"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
