import { Link } from "@tanstack/react-router";
import { ArrowRight, FilePlus2, Search } from "lucide-react";
import { useState } from "react";
import { useHrClaims } from "@/api/hrClaims";
import {
  ClaimStatus,
  formatClaimDate,
  formatClaimMoney,
} from "@/components/hr/claimPresentation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const PAGE_SIZE = 20;

export function HrClaimsPage() {
  useDocumentTitle("Employee claims");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const debouncedSearch = useDebouncedValue(search, 300);
  const claims = useHrClaims({
    query: debouncedSearch,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });
  const visible = claims.data?.items ?? [];
  const pages = Math.max(1, Math.ceil((claims.data?.total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Employee claims</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Submit a claim for one employee and follow its progress.
          </p>
        </div>
        <Button asChild className="h-11 shrink-0 sm:h-9">
          <Link to="/hr/claims/new">
            <FilePlus2 className="size-4" aria-hidden />
            New claim
          </Link>
        </Button>
      </div>

      {claims.isLoading ? (
        <div className="space-y-3" aria-label="Loading claims">
          {[0, 1, 2].map((key) => (
            <Skeleton key={key} className="h-24 rounded-xl" />
          ))}
        </div>
      ) : claims.isError ? (
        <Card className="p-5" role="alert">
          <p className="font-medium">Claims could not be loaded</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {formatError(claims.error)}
          </p>
          <Button
            variant="outline"
            className="mt-4 h-11 sm:h-9"
            onClick={() => void claims.refetch()}
          >
            Try again
          </Button>
        </Card>
      ) : claims.data?.total === 0 && !debouncedSearch.trim() ? (
        <Card className="flex flex-col items-start gap-3 p-6">
          <div className="rounded-lg bg-muted p-2.5">
            <FilePlus2 className="size-5 text-muted-foreground" aria-hidden />
          </div>
          <div>
            <h2 className="font-semibold">No delegated claims yet</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Start with the employee. Their live coverage determines the claim
              types and evidence you can submit.
            </p>
          </div>
          <Button asChild className="h-11 sm:h-9">
            <Link to="/hr/claims/new">Start a claim</Link>
          </Button>
        </Card>
      ) : (
        <section aria-labelledby="claim-ledger-heading" className="space-y-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <h2 id="claim-ledger-heading" className="text-sm font-semibold">
              {claims.data?.total} {claims.data?.total === 1 ? "claim" : "claims"}
            </h2>
            <label className="relative block w-full sm:max-w-xs">
              <span className="sr-only">Search claims</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
                placeholder="Employee, reference or provider"
                className="h-11 pl-9 sm:h-9"
              />
            </label>
          </div>

          {visible.length === 0 ? (
            <Card className="p-6 text-sm text-muted-foreground">
              No claims match “{search}”.
            </Card>
          ) : (
            <Card className="divide-y divide-border overflow-hidden">
              {visible.map((claim) => (
                <Link
                  key={claim.id}
                  to="/hr/claims/$claimId"
                  params={{ claimId: claim.id }}
                  className="group flex min-h-24 items-center gap-4 px-4 py-4 transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 sm:px-5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-medium">
                        {claim.employee_name ?? "Employee"}
                      </span>
                      <ClaimStatus status={claim.status} />
                    </div>
                    <p className="mt-1 truncate text-sm text-muted-foreground">
                      {claim.claim_type}
                      {claim.provider_name ? ` · ${claim.provider_name}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {claim.claim_ref ?? "Draft"} · Incurred {formatClaimDate(claim.incurred_date)}
                    </p>
                  </div>
                  <div className="hidden shrink-0 text-right sm:block">
                    <p className="font-medium tabular-nums">
                      {formatClaimMoney(claim.amount_claimed, claim.currency)}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatClaimDate(claim.submitted_at ?? claim.created_at)}
                    </p>
                  </div>
                  <ArrowRight
                    className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                    aria-hidden
                  />
                </Link>
              ))}
            </Card>
          )}
          <PaginationControls page={page} pages={pages} onPageChange={setPage} />
        </section>
      )}
    </div>
  );
}
