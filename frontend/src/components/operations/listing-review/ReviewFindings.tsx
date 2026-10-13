import { AlertTriangle, Info, XCircle } from "lucide-react";
import type { ListingPreview } from "@/api/employeeListing";

/**
 * Everything worth a look before applying, in one list ordered by weight:
 * rows that will be skipped, then rows applied but worth checking, then notes.
 * Each names its rows, never the people in them.
 */
type Tone = "error" | "warn" | "info";

export interface Finding {
  key: string;
  tone: Tone;
  message: string;
  rows: number[];
  total: number;
}

const ROW_PREFIX = /^Row \d+(?: [A-Z]{1,3})?:\s*/;
const SHOWN_ROWS = 12;

function grouped(
  items: { row: number | null; key: string; message: string }[],
  tone: Tone,
  prefix: string,
): Finding[] {
  const groups = new Map<string, Finding>();
  for (const item of items) {
    const message = item.message.replace(ROW_PREFIX, "");
    const key = `${prefix}:${item.key}:${message}`;
    const group = groups.get(key) ?? { key, tone, message, rows: [], total: 0 };
    group.total += 1;
    if (item.row != null) group.rows.push(item.row);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function findingsFor(preview: ListingPreview): Finding[] {
  const members = preview.members;
  const skipped = grouped(
    members.issues.map((i) => ({ row: i.row, key: i.record_type, message: i.message })),
    "error",
    "skipped",
  ).map((f) => ({ ...f, message: `Not imported — ${f.message}` }));
  const reader = grouped(
    preview.issues.map((i) => ({ row: i.row, key: i.code, message: i.message })),
    "warn",
    "reader",
  );
  const warnings = grouped(
    members.warnings.map((w) => ({
      row: w.row,
      key: w.record_type,
      // Masked IDs differ per row; group on the wording around them.
      message: `${w.record_type === "dependant" ? "Dependant" : "Employee"} ${w.message
        .replace(/\s*\b[A-Z]\*+\d*[A-Z]\b/g, "")
        .replace(/^Identification/, "identification")}`,
    })),
    "warn",
    "warning",
  );
  const checks: Finding[] = preview.checks.map((c) => ({
    key: `check:${c.code}`,
    tone: "warn",
    message: c.message,
    rows: c.rows,
    total: c.count,
  }));
  const notes: Finding[] = preview.notices
    .filter((n) => n.tone === "info")
    .map((n) => ({ key: `note:${n.code}`, tone: "info", message: n.message, rows: [], total: 0 }));
  return [...skipped, ...reader, ...warnings, ...checks, ...notes];
}

const ICON: Record<Tone, typeof AlertTriangle> = {
  error: XCircle,
  warn: AlertTriangle,
  info: Info,
};
const ICON_TONE: Record<Tone, string> = {
  error: "text-error",
  warn: "text-warn",
  info: "text-muted-foreground",
};

export function ReviewFindings({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return null;
  const attention = findings.filter((f) => f.tone !== "info").length;
  return (
    <section aria-labelledby="listing-findings" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="listing-findings" className="text-sm font-semibold text-foreground">
          Check before applying
        </h3>
        {attention > 0 && (
          <span className="text-xs text-muted-foreground">
            {attention.toLocaleString()} to review
          </span>
        )}
      </div>
      <ul className="overflow-hidden rounded-lg border border-border">
        {findings.map((finding) => {
          const Icon = ICON[finding.tone];
          return (
            <li key={finding.key} className="flex gap-2 border-t border-border px-3 py-2 first:border-t-0">
              <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${ICON_TONE[finding.tone]}`} />
              <div className="min-w-0">
                <p className="text-sm text-foreground">
                  {finding.total > 0 && (
                    <span className="font-medium tabular-nums">{finding.total.toLocaleString()} · </span>
                  )}
                  {finding.message}
                </p>
                {finding.rows.length > 0 && <RowList rows={finding.rows} total={finding.total} />}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function RowList({ rows, total }: { rows: number[]; total: number }) {
  const shown = rows.slice(0, SHOWN_ROWS);
  const more = Math.max(total, rows.length) - shown.length;
  return (
    <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">
      {rows.length === 1 ? "Row" : "Rows"} {shown.join(", ")}
      {more > 0 && ` and ${more.toLocaleString()} more`}
    </p>
  );
}
