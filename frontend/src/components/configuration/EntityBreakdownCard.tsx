/** Legal-entity breakdown of the uploaded roster.
 *
 * Reads the same vocabulary the Insured picker uses, so the headcounts here are
 * exactly what the matching gate sees — an entity listed here with N employees
 * is an entity a category can be restricted to and match N people.
 *
 * Hidden when the roster carries no Entity column at all (single-entity
 * clients), since then there is nothing to break down.
 */
import { Building2 } from "lucide-react";
import { useEntityVocab } from "@/api/hooks";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function EntityBreakdownCard({
  policyYearId,
}: {
  policyYearId: string;
}) {
  const { data } = useEntityVocab(policyYearId);
  const entities = data?.roster ?? [];
  if (entities.length === 0) return null;

  const total = entities.reduce((sum, e) => sum + e.count, 0);
  // Employees whose Entity cell is blank — they match every category, so they
  // are not an entity, but the numbers have to add up.
  const unassigned = (data?.employees_total ?? 0) - total;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-1.5">
          <Building2 className="size-4 text-muted-foreground" />
          <CardTitle>
            Legal entities · {entities.length.toLocaleString()}
          </CardTitle>
        </div>
        <CardDescription>
          {total.toLocaleString()} of{" "}
          {(data?.employees_total ?? 0).toLocaleString()} employees carry an
          entity on the roster
          {unassigned > 0 ? ` · ${unassigned.toLocaleString()} blank` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-1.5">
          {entities.map((e) => {
            const share = total > 0 ? (e.count / total) * 100 : 0;
            return (
              <div key={e.value} className="flex items-center gap-3">
                <span className="w-64 shrink-0 truncate text-sm text-foreground">
                  {e.value}
                </span>
                <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-foreground/30"
                    style={{ width: `${share}%` }}
                  />
                </div>
                <span className="w-28 shrink-0 text-right text-sm tabular-nums text-muted-foreground">
                  {e.count.toLocaleString()}
                  <span className="ml-1 text-xs">({share.toFixed(1)}%)</span>
                </span>
              </div>
            );
          })}
          {unassigned > 0 && (
            <div className="flex items-center gap-3 pt-1">
              <span className="w-64 shrink-0 truncate text-sm text-muted-foreground">
                No entity on the roster
              </span>
              <div className="h-1.5 min-w-0 flex-1" />
              <span className="w-28 shrink-0 text-right text-sm tabular-nums text-muted-foreground">
                {unassigned.toLocaleString()}
              </span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
