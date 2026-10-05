import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useReadinessEmployees } from "@/api/enrollment";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { SETUP_PATH, setupSearch } from "@/lib/setupLink";
import { formatError } from "@/lib/errors";

export function ReadinessEmployees({ windowId, code }: { windowId: string; code: string }) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const query = useReadinessEmployees(windowId, code, useDebouncedValue(q, 250), page);
  return (
    <div className="mt-3 space-y-3 rounded-md border border-border bg-muted/30 p-3">
      <Input aria-label="Search affected employees" placeholder="Search staff ID or name…" value={q}
        onChange={(e) => { setQ(e.target.value); setPage(0); }} className="max-w-sm" />
      {query.isLoading ? <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden />Loading employees…</p> : query.isError ? (
        <div role="alert"><p>Could not load affected employees. {formatError(query.error)}</p>
          <Button size="sm" variant="outline" onClick={() => void query.refetch()}>Retry employee review</Button></div>
      ) : (
        <>
          <p className="text-xs text-muted-foreground" aria-live="polite">{query.data?.total.toLocaleString()} {query.data?.total === 1 ? "employee" : "employees"}{q.trim() ? (query.data?.total === 1 ? " matches your search" : " match your search") : " affected"}</p>
          {!query.data?.items.length ? <p className="text-muted-foreground">{q.trim() ? "No affected employees match this search." : "No employees are currently affected. Refresh the readiness check."}</p> : (
            <ul className="divide-y divide-border">
              {query.data.items.map((employee) => (
                <li key={employee.employee_id} className="space-y-1.5 py-3 first:pt-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <Link className="focus-ring font-medium underline underline-offset-2" to="/policy-admin/member-listing" search={{ tab: "employees", employee: employee.employee_id }}>
                      {employee.employee_name || employee.staff_id}
                    </Link>
                    <span className="text-xs text-muted-foreground">{employee.staff_id}{employee.employee_category && ` · ${employee.employee_category}`}{employee.grade && ` · ${employee.grade}`}</span>
                  </div>
                  <p className="text-xs">{employee.reason}{employee.products.length > 0 && ` ${employee.products.join(", ")}`}</p>
                  {employee.mappings.length > 0 && <ul className="flex flex-wrap gap-x-4 gap-y-2 text-xs">
                    {employee.mappings.map((mapping) => <li key={mapping.category_id}>
                      <Link className="focus-ring underline underline-offset-2" to={SETUP_PATH}
                        search={setupSearch(mapping.product_code, { categoryId: mapping.category_id })}>
                        Review {mapping.product_code} · {mapping.category_name}
                      </Link>
                    </li>)}
                  </ul>}
                  {code === "portal_access_incomplete" && <Link className="focus-ring inline-block text-xs underline underline-offset-2"
                    to="/policy-admin/member-coverage" search={{ employee: employee.employee_id, view: undefined }}>Review portal access</Link>}
                </li>
              ))}
            </ul>
          )}
          <PaginationControls page={page} pages={Math.ceil((query.data?.total ?? 0) / 50)} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}
