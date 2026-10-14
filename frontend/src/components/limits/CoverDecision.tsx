import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useClearDependantCoverDecision,
  useSetDependantCoverDecision,
} from "@/api/coverageLimits";
import { formatError } from "@/lib/errors";
import type { CoverageLimitAlert } from "@/types";

const OTHER = "Other";
// Why an insurer keeps a dependant past the slip's age limit.
const KEEP_REASONS = [
  "Full-time student",
  "Disabled or dependent child",
  "Insurer agreed in writing",
  OTHER,
];

/**
 * The broker's call on one dependant above an age limit, for one product:
 * keep them covered (saying why) or exclude them from that product. Nothing
 * changes until one is chosen, and either can be undone.
 */
export function CoverDecision({
  alert,
  policyYearId,
}: {
  alert: CoverageLimitAlert;
  policyYearId: string;
}) {
  const save = useSetDependantCoverDecision(policyYearId);
  const clear = useClearDependantCoverDecision(policyYearId);
  const [keeping, setKeeping] = useState(false);
  const [reason, setReason] = useState(KEEP_REASONS[0]);
  const [other, setOther] = useState("");
  const dependantId = alert.dependant_id;
  if (!dependantId) return null;
  const code = alert.product_code;
  const who = alert.dependant_name ?? "Dependant";
  const busy = save.isPending || clear.isPending;

  const decide = (decision: "keep" | "exclude", why?: string) =>
    save.mutate(
      { dependantId, productCode: code, decision, reason: why },
      {
        onSuccess: () => {
          setKeeping(false);
          toast.success(
            decision === "keep" ? `${who} stays covered under ${code}` : `${who} excluded from ${code}`,
          );
        },
        onError: (error) => toast.error(formatError(error)),
      },
    );
  const undo = () =>
    clear.mutate(
      { dependantId, productCode: code },
      {
        onSuccess: () => toast.success(`${who}'s ${code} decision undone`),
        onError: (error) => toast.error(formatError(error)),
      },
    );

  if (alert.decision) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={alert.decision === "keep" ? "good" : "default"}>
          {alert.decision === "keep"
            ? `${code}: kept covered${alert.decision_reason ? ` · ${alert.decision_reason}` : ""}`
            : `${code}: excluded`}
        </Badge>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={undo}>
          Undo
        </Button>
      </div>
    );
  }

  if (keeping) {
    const why = reason === OTHER ? other.trim() : reason;
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-2xs text-muted-foreground">{code}</span>
        <Select value={reason} onValueChange={setReason}>
          <SelectTrigger className="h-8 w-56 text-xs" aria-label={`Why ${who} stays covered under ${code}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KEEP_REASONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {reason === OTHER && (
          <Input
            value={other}
            maxLength={255}
            onChange={(event) => setOther(event.target.value)}
            placeholder="Reason"
            aria-label="Other reason"
            className="h-8 w-56 text-xs"
          />
        )}
        <Button type="button" size="sm" disabled={busy || !why} onClick={() => decide("keep", why)}>
          Keep covered
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setKeeping(false)}>
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-2xs text-muted-foreground">{code}</span>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setKeeping(true)}>
        Keep covered…
      </Button>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => decide("exclude")}>
        Exclude from {code}
      </Button>
    </div>
  );
}
