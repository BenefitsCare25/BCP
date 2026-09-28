/** Can this draft open? Shown BEFORE the broker presses Open — the same checks
 * the server runs at the open boundary, so the answer here is the answer there.
 * Blockers stop the period opening; warnings are the broker's call. */
import { AlertTriangle, CheckCircle2, CircleSlash, Loader2 } from "lucide-react";
import type { EnrollmentReadiness, EnrollmentReadinessIssue } from "@/api/enrollment";
import { cn } from "@/lib/cn";

export function ReadinessChecklist({
  readiness,
  isLoading,
}: {
  readiness: EnrollmentReadiness | undefined;
  isLoading: boolean;
}) {
  if (isLoading || !readiness) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Checking readiness…
      </p>
    );
  }
  const blockers = readiness.issues.filter((i) => i.severity !== "warning");
  const warnings = readiness.issues.filter((i) => i.severity === "warning");
  if (!readiness.issues.length) {
    return (
      <p className="flex items-center gap-2 text-sm text-good">
        <CheckCircle2 className="size-4" aria-hidden />
        Ready to open — every check passes.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {blockers.length > 0 && (
        <IssueList
          title={`${blockers.length} ${blockers.length === 1 ? "thing blocks" : "things block"} opening`}
          tone="error"
          issues={blockers}
        />
      )}
      {warnings.length > 0 && (
        <IssueList
          title={`${warnings.length} to be aware of`}
          tone="warn"
          issues={warnings}
        />
      )}
    </div>
  );
}

function IssueList({
  title,
  tone,
  issues,
}: {
  title: string;
  tone: "error" | "warn";
  issues: EnrollmentReadinessIssue[];
}) {
  const Icon = tone === "error" ? CircleSlash : AlertTriangle;
  return (
    <div>
      <p
        className={cn(
          "mb-1.5 flex items-center gap-1.5 text-sm font-medium",
          tone === "error" ? "text-error" : "text-warn",
        )}
      >
        <Icon className="size-4" aria-hidden />
        {title}
      </p>
      <ul className="space-y-1.5 pl-5.5">
        {issues.map((issue) => (
          <li key={issue.code} className="text-sm text-foreground">
            {issue.message}
            {typeof issue.count === "number" && (
              <span className="text-muted-foreground">
                {" "}
                · {issue.count.toLocaleString()} affected
              </span>
            )}
            {issue.products?.length ? (
              <span className="text-muted-foreground"> · {issue.products.join(", ")}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
