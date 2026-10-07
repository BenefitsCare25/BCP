import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import type { CoverageLimitAlert, LimitKind } from "@/types";
import { LIMIT_KIND, LIMIT_ORDER } from "./limitKinds";

/** One person crossing one limit, across every product it applies to. */
interface PersonRow {
  key: string;
  alert: CoverageLimitAlert;
  products: string[];
}

const ROWS_SHOWN = 8;

function rowsByKind(alerts: CoverageLimitAlert[]): Map<LimitKind, PersonRow[]> {
  const out = new Map<LimitKind, PersonRow[]>();
  const byKey = new Map<string, PersonRow>();
  for (const alert of alerts) {
    // Same person, same limit, same wording → one row with several products.
    const key = [alert.kind, alert.employee_id, alert.dependant_id ?? "", alert.message].join("|");
    const row = byKey.get(key);
    if (row) {
      if (!row.products.includes(alert.product_code)) row.products.push(alert.product_code);
      continue;
    }
    const next: PersonRow = { key, alert, products: [alert.product_code] };
    byKey.set(key, next);
    out.set(alert.kind, [...(out.get(alert.kind) ?? []), next]);
  }
  return out;
}

/**
 * Limit crossings grouped by kind, one row per person with the products the
 * limit applies to. Shared by product setup (one product) and the bell's
 * review list, so both read the same way.
 */
export function LimitAlertList({
  alerts,
  showCoverageLinks = true,
}: {
  alerts: CoverageLimitAlert[];
  /** Each row opens the member's coverage. */
  showCoverageLinks?: boolean;
}) {
  const groups = rowsByKind(alerts);
  return (
    <div className="grid gap-3">
      {LIMIT_ORDER.filter((kind) => groups.has(kind)).map((kind) => (
        <KindGroup
          key={kind}
          kind={kind}
          rows={groups.get(kind) ?? []}
          showCoverageLinks={showCoverageLinks}
        />
      ))}
    </div>
  );
}

function KindGroup({
  kind,
  rows,
  showCoverageLinks,
}: {
  kind: LimitKind;
  rows: PersonRow[];
  showCoverageLinks: boolean;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, ROWS_SHOWN);
  return (
    <div>
      <h4 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
        {LIMIT_KIND[kind].title}
        <span className="ml-1.5 tabular-nums normal-case tracking-normal">· {rows.length}</span>
      </h4>
      <ul className="grid gap-0.5">
        {shown.map(({ key, alert, products }) => (
          <li
            key={key}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 rounded-md px-2 py-1.5 hover:bg-muted/40"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-sm font-medium text-foreground">
                  {alert.employee_name || alert.staff_id}
                </span>
                <span className="font-mono text-2xs text-muted-foreground">{alert.staff_id}</span>
                {alert.dependant_name && (
                  <span className="text-xs text-muted-foreground">
                    · {alert.dependant_name}
                  </span>
                )}
                {products.map((code) => (
                  <span
                    key={code}
                    className="rounded bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground"
                  >
                    {code}
                  </span>
                ))}
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">{alert.message}</p>
            </div>
            {showCoverageLinks && (
              <Link
                to="/policy-admin/member-coverage"
                search={{ employee: alert.employee_id, view: "broker" }}
                aria-label={`Open ${alert.employee_name || alert.staff_id}'s coverage`}
                className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                Coverage <ArrowRight aria-hidden className="size-3.5" />
              </Link>
            )}
          </li>
        ))}
      </ul>
      {rows.length > ROWS_SHOWN && (
        <button
          type="button"
          onClick={() => setAll((value) => !value)}
          className="mt-1 rounded px-2 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          {all ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}
