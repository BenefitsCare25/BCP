/**
 * The "is everything about this covered?" list.
 *
 * A warning never blocks — brokers legitimately make changes that break cohort
 * rules (a slip typo, a negotiated exception). An UNACKNOWLEDGED one does: the
 * broker ticks each warn-level bucket once, and the acceptance is stored on the
 * batch so a year later the record says they were told.
 *
 * Info-level buckets are stated and take no tick. Mixing them into the same
 * checklist would train the eye to tick everything, which is exactly how a real
 * warning gets waved through.
 */
import { Info, TriangleAlert } from "lucide-react";
import type { BulkWarningBucket } from "@/api/enrollment";
import { cn } from "@/lib/cn";
import { SectionLabel } from "@/components/ui/section-label";

const TITLES: Record<string, string> = {
  outside_cohort: "Outside their cohort",
  open_enrollment: "In an open enrolment period",
  enrollment_confirmed: "Chosen in a confirmed selection",
  flex_overdraft: "Overdraws their flex wallet",
  dependant_ineligible: "Dependant outside the age window",
  unpriced: "No flex price",
  underwriting_triggered: "Needs underwriting",
};

export function WarningPanel({
  warnings,
  acknowledged,
  readOnly,
  onToggle,
}: {
  warnings: BulkWarningBucket[];
  acknowledged: string[];
  readOnly?: boolean;
  onToggle: (code: string, accepted: boolean) => void;
}) {
  if (!warnings.length) return null;
  const needsAck = warnings.filter((w) => w.requires_ack);
  const notes = warnings.filter((w) => !w.requires_ack);

  return (
    <div className="space-y-3">
      {needsAck.length > 0 && (
        <div>
          <SectionLabel>
            Confirm before applying ({needsAck.filter((w) => acknowledged.includes(w.code)).length}
            /{needsAck.length})
          </SectionLabel>
          <ul className="mt-2 space-y-2">
            {needsAck.map((w) => {
              const accepted = acknowledged.includes(w.code);
              return (
                <li key={w.code}>
                  <label
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-md border border-border p-3",
                      accepted ? "bg-muted/40" : "bg-background",
                      readOnly && "cursor-default opacity-70",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 shrink-0 accent-[var(--color-primary)]"
                      checked={accepted}
                      disabled={readOnly}
                      onChange={(e) => onToggle(w.code, e.target.checked)}
                    />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                        <TriangleAlert className="size-4 shrink-0 text-warn" />
                        {TITLES[w.code] ?? w.code}
                        <span className="text-muted-foreground">
                          · {w.count} {w.count === 1 ? "member" : "members"}
                        </span>
                      </span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {w.message}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {notes.length > 0 && (
        <ul className="space-y-1.5">
          {notes.map((w) => (
            <li key={w.code} className="flex items-start gap-2 text-xs text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              <span>
                <span className="font-medium text-foreground">
                  {TITLES[w.code] ?? w.code}
                </span>{" "}
                · {w.count} {w.count === 1 ? "member" : "members"} — {w.message}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Warn-level codes still outstanding — what keeps Apply disabled. */
export function outstandingWarnings(
  warnings: BulkWarningBucket[],
  acknowledged: string[],
): string[] {
  return warnings
    .filter((w) => w.requires_ack && !acknowledged.includes(w.code))
    .map((w) => w.code);
}
