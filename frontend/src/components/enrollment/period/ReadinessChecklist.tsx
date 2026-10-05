/** Advisory setup checks and affected employees; opening is the broker's choice. */
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import type { EnrollmentReadiness, EnrollmentReadinessIssue } from "@/api/enrollment";
import { useState } from "react";
import { ReadinessEmployees } from "./ReadinessEmployees";
import { Button } from "@/components/ui/button";

export function ReadinessChecklist({
  readiness,
  isLoading,
  windowId,
}: {
  readiness: EnrollmentReadiness | undefined;
  isLoading: boolean;
  windowId: string;
}) {
  if (isLoading || !readiness) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Checking readiness…
      </p>
    );
  }
  if (!readiness.issues.length) {
    return (
      <p className="flex items-center gap-2 text-sm text-good">
        <CheckCircle2 className="size-4" aria-hidden />
        No validation issues found.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <IssueList
        title={`${readiness.issues.length} validation warning${readiness.issues.length === 1 ? "" : "s"}`}
        issues={readiness.issues}
        windowId={windowId}
      />
      <p className="text-xs text-muted-foreground">These alerts do not prevent opening this period.</p>
    </div>
  );
}

function IssueList({
  title,
  issues,
  windowId,
}: {
  title: string;
  issues: EnrollmentReadinessIssue[];
  windowId: string;
}) {
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-warn">
        <AlertTriangle className="size-4" aria-hidden />
        {title}
      </p>
      <ul className="space-y-1.5 pl-5.5">
        {issues.map((issue) => (
          <Issue key={issue.code} issue={issue} windowId={windowId} />
        ))}
      </ul>
    </div>
  );
}

const EMPLOYEE_ISSUES = new Set(["employees_without_coverage", "employees_with_coverage_gaps", "unconfirmed_categories", "portal_access_incomplete", "flex_wallets_incomplete"]);

function Issue({ issue, windowId }: { issue: EnrollmentReadinessIssue; windowId: string }) {
  const [expanded, setExpanded] = useState(false);
  return <li className="text-sm text-foreground">
    <p>{issue.message}
      {typeof issue.count === "number" && <span className="text-muted-foreground"> · {issue.count.toLocaleString()} {issue.count_unit ?? "employees"}</span>}
      {typeof issue.employee_count === "number" && <span className="text-muted-foreground"> · {issue.employee_count.toLocaleString()} employees affected</span>}
      {issue.products?.length ? <span className="text-muted-foreground"> · {issue.products.join(", ")}</span> : null}
    </p>
    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-2">
      {EMPLOYEE_ISSUES.has(issue.code) && <Button variant="link" size="sm" className="h-auto p-0" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? "Hide affected employees" : "Show affected employees"}
      </Button>}
      {issue.code === "portal_access_incomplete" && <a href="#enrolment-portal-invitations" className="focus-ring text-sm underline underline-offset-2">Send portal invitations in bulk</a>}
    </div>
    {expanded && <ReadinessEmployees windowId={windowId} code={issue.code} />}
  </li>;
}
