/** Insured-entity reconciliation — the entity gate's failure mode made visible.
 *
 * A category naming legal entities only matches employees whose roster Entity
 * column is one of them. Both sides are free text, so a spelling mismatch
 * silently produces "unmatched" employees with no stated cause. This panel
 * names the cause and offers the one-click fix.
 *
 * Rendered on the Employee Coverage (matching) page beside the match tiles;
 * hidden when everything reconciles, following OrphanOverridesPanel.
 *
 * Deliberately a WARNING, not a gate: leaving Insured blank is legitimate (it
 * means "every entity"), and single-entity clients would trip a hard block
 * constantly. Compare flex tiers, which DO block on confirm, because there
 * every employee must end up with a wallet.
 */
import { useState } from "react";
import { AlertTriangle, Link2, Plus } from "lucide-react";
import { toast } from "sonner";
import { useEntityVocab } from "@/api/hooks";
import { useCreateEntityAlias } from "@/api/entityAliases";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatError } from "@/lib/errors";

export function EntityReconciliationPanel({
  policyYearId,
}: {
  policyYearId: string;
}) {
  const { data } = useEntityVocab(policyYearId);
  const createAlias = useCreateEntityAlias();
  const [linking, setLinking] = useState<string | null>(null);

  const unreconciled = data?.known ?? [];
  // A roster entity no category names is only worth mentioning when some
  // category DOES restrict by entity — otherwise every category is a wildcard
  // and nothing is being excluded.
  const anyRestricted = (data?.roster ?? []).some((r) => r.claimed);
  const unclaimed = anyRestricted
    ? (data?.roster ?? []).filter((r) => !r.claimed)
    : [];

  if (unreconciled.length === 0 && unclaimed.length === 0) return null;

  const link = async (alias: string, canonical: string) => {
    setLinking(alias);
    try {
      await createAlias.mutateAsync({ alias, canonical });
      toast.success(`${alias} now matches ${canonical} — re-run matching`);
    } catch (err) {
      toast.error(formatError(err));
    } finally {
      setLinking(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-1.5">
          <AlertTriangle className="size-4 text-amber-500" />
          <CardTitle>Insured entities need reconciling</CardTitle>
        </div>
        <CardDescription>
          A category restricted to a legal entity only matches employees whose
          roster Entity column says the same company.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {unreconciled.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Named in your configuration but matching{" "}
              <span className="font-medium text-foreground">no employee</span> on
              the roster:
            </p>
            {unreconciled.map((e) => (
              <div
                key={e.value}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-2.5"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <Badge variant="outline" className="shrink-0">
                    {e.value}
                  </Badge>
                  {e.suggestion && (
                    <span className="truncate text-xs text-muted-foreground">
                      likely the roster's{" "}
                      <span className="text-foreground">{e.suggestion}</span>
                    </span>
                  )}
                </div>
                {e.suggestion ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={linking === e.value}
                    onClick={() => void link(e.value, e.suggestion!)}
                  >
                    <Link2 className="size-3.5" />
                    {linking === e.value ? "Linking…" : "Same entity"}
                  </Button>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    No close roster match — fix the spelling or add an alias
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {unclaimed.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">
              On the roster but named by no category — these employees can only
              match categories that cover every entity:
            </p>
            <div className="flex flex-wrap gap-1.5">
              {unclaimed.map((e) => (
                <Badge key={e.value} variant="outline" className="gap-1">
                  {e.value}
                  <span className="text-muted-foreground">· {e.count}</span>
                </Badge>
              ))}
            </div>
          </div>
        )}

        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Plus className="size-3" />
          Linking two spellings creates an alias — neither name is rewritten, so
          the exported placement slip keeps the registered name. Re-run matching
          to apply.
        </p>
      </CardContent>
    </Card>
  );
}
