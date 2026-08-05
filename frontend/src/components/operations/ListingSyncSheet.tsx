import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AdcOp, AdcPreview } from "@/types";

/**
 * What an uploaded member listing would do, before it does it.
 *
 * The listing is diffed against the roster and every row lands in one of four
 * buckets. Three of them apply on confirm; `missing` does not.
 *
 * **Missing is a separate bucket from Terminations, and that is the whole
 * point.** A termination in the file is EVIDENCE — the row carries a past
 * leaving date. Absence is an INFERENCE, and a partial export (new joiners
 * only, one entity, a filtered HR extract) is indistinguishable from a full
 * census that legitimately dropped people. So absence is opt-in, off by
 * default, and the higher the share of the roster it would end, the louder
 * this says so.
 */
const FIELD_LABEL: Record<string, string> = {
  employee_name: "Name",
  category: "Category",
  salary: "Salary",
  marital_status: "Marital status",
  pass: "Pass",
  job_grade: "Job grade",
  date_of_birth: "Date of birth",
  relationship: "Relationship",
  dependant_name: "Dependant name",
  last_day_of_service: "Last day of service",
  termination_date: "Termination date",
  insurer_member_ids: "Insurer member IDs",
};

function label(field: string): string {
  return FIELD_LABEL[field] ?? field.replace(/_/g, " ");
}

/** Above this share of the roster, absence almost certainly means the broker
 *  uploaded part of the roster rather than a census that lost people. */
const PARTIAL_FILE_RATIO = 0.3;

interface Props {
  preview: AdcPreview | null;
  onClose: () => void;
  onApply: () => void;
  applying: boolean;
  terminateMissing: boolean;
  onTerminateMissingChange: (value: boolean) => void;
}

export function ListingSyncSheet({
  preview,
  onClose,
  onApply,
  applying,
  terminateMissing,
  onTerminateMissingChange,
}: Props) {
  const c = preview?.counts ?? {};
  const missing = preview?.missing ?? [];
  const rosterTotal = c.roster_total ?? 0;
  // Guard the divide: with no roster to compare against, absence means nothing
  // and must not read as "100% of your members are leaving".
  const missingRatio = rosterTotal > 0 ? missing.length / rosterTotal : 0;
  const looksPartial = missing.length > 0 && missingRatio >= PARTIAL_FILE_RATIO;
  const unreadable = c.dropped_rows ?? 0;

  const applies =
    (c.additions ?? 0) +
    (c.changes ?? 0) +
    (c.deletions ?? 0) +
    (terminateMissing && unreadable === 0 ? missing.length : 0);

  return (
    <Sheet open={preview !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="flex w-full flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Review listing changes</SheetTitle>
        </SheetHeader>
        <SheetBody className="flex-1 space-y-4 overflow-y-auto">
          <div className="flex flex-wrap gap-2 text-sm">
            <Badge variant="good">{c.additions ?? 0} additions</Badge>
            <Badge variant="info">{c.changes ?? 0} changes</Badge>
            <Badge variant="warn">{c.deletions ?? 0} terminations</Badge>
            {missing.length > 0 && (
              <Badge variant="default">{missing.length} not in this file</Badge>
            )}
            {(c.unchanged ?? 0) > 0 && (
              <Badge variant="default">{c.unchanged} unchanged</Badge>
            )}
            {(c.already_terminated ?? 0) > 0 && (
              // A rehire row — someone in the file whose identity belongs to an
              // already-terminated record. An upload never reinstates and never
              // duplicates, so it lands in no bucket; without this badge it
              // leaves no trace at all.
              <Badge variant="default" title="Already terminated — an upload never reinstates someone. Reinstate them on their record first.">
                {c.already_terminated} already terminated
              </Badge>
            )}
            {(c.issues ?? 0) > 0 && (
              <Badge variant="error">{c.issues} issues</Badge>
            )}
          </div>

          {(c.dropped_rows ?? 0) > 0 && (
            // Rows the parser couldn't read at all — no Staff ID on an employee
            // row, no dependant column on a dependant one. They never reach the
            // diff, so without this the file would import 8 of 10 rows and
            // report success.
            <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn-soft/40 p-2.5">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
              <p className="text-xs text-foreground">
                <strong>
                  {c.dropped_rows} row{c.dropped_rows === 1 ? "" : "s"} could not
                  be read
                </strong>{" "}
                and {c.dropped_rows === 1 ? "is" : "are"} not included below. An
                employee row needs a Staff ID; a dependant row needs a Dependant
                Name, ID or relationship.
              </p>
            </div>
          )}

          {preview &&
            (c.additions ?? 0) + (c.changes ?? 0) + (c.deletions ?? 0) === 0 &&
            missing.length === 0 &&
            // "Matches exactly" must not print above an unreadable-rows warning
            // or an issues badge — the file plainly did NOT match; parts of it
            // were never read.
            (c.dropped_rows ?? 0) === 0 &&
            (c.issues ?? 0) === 0 && (
              <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                This file matches the roster exactly — nothing to apply.
              </div>
            )}

          {preview && preview.additions.length > 0 && (
            <Section title="Additions">
              {preview.additions.map((op, i) => (
                <OpRow key={`a${i}`} op={op}>
                  {op.nric_masked && (
                    <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {op.nric_masked}
                    </div>
                  )}
                </OpRow>
              ))}
            </Section>
          )}

          {preview && preview.changes.length > 0 && (
            <Section title="Changes">
              {preview.changes.map((op, i) => (
                <OpRow key={`c${i}`} op={op}>
                  <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                    {op.field_diffs.map((d, j) => (
                      <li key={j}>
                        {label(d.field)}:{" "}
                        <span className="line-through">{d.old || "—"}</span> →{" "}
                        <span className="text-foreground">{d.new || "—"}</span>
                      </li>
                    ))}
                  </ul>
                </OpRow>
              ))}
            </Section>
          )}

          {preview && preview.deletions.length > 0 && (
            <Section
              title="Terminations"
              note="Stated in the file — these rows carry a leaving date that has passed."
            >
              {preview.deletions.map((op, i) => (
                <OpRow key={`d${i}`} op={op}>
                  {op.effective && (
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      Effective {op.effective}
                    </div>
                  )}
                </OpRow>
              ))}
            </Section>
          )}

          {missing.length > 0 && (
            <div className="rounded-lg border border-border">
              <div className="bg-muted px-3 py-1.5 text-xs font-medium text-muted-foreground">
                Not in this file
              </div>
              <div className="space-y-2 px-3 py-2.5">
                <p className="text-xs text-muted-foreground">
                  {missing.length.toLocaleString()} of{" "}
                  {rosterTotal.toLocaleString()} on the roster{" "}
                  {missing.length === 1 ? "is" : "are"} not named anywhere in
                  this upload. That means they left — or that this file only
                  covers part of the roster. Nothing happens to them unless you
                  say so.
                </p>
                {looksPartial && (
                  <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn-soft/40 p-2.5">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
                    <p className="text-xs text-foreground">
                      This file is missing{" "}
                      <strong>
                        {Math.round(missingRatio * 100)}% of the roster
                      </strong>
                      . That is usually a partial export, not{" "}
                      {missing.length.toLocaleString()} people leaving at once.
                      Check the file before ticking below.
                    </p>
                  </div>
                )}
                {unreadable > 0 ? (
                  // Absence is only evidence when the whole file was read. With
                  // rows the parser could not identify, someone "missing" may
                  // simply be sitting in one of them — so the opt-in is off the
                  // table until the file is fixed.
                  <p className="rounded-md border border-border bg-muted px-2.5 py-2 text-xs text-foreground">
                    Terminating is unavailable while {unreadable} row
                    {unreadable === 1 ? "" : "s"} could not be read — one of them
                    may be a person listed above. Fix the file and upload again.
                  </p>
                ) : (
                  <label className="flex cursor-pointer items-start gap-2 text-sm">
                    <Checkbox
                      checked={terminateMissing}
                      onCheckedChange={(v) =>
                        onTerminateMissingChange(v === true)
                      }
                      className="mt-0.5"
                    />
                    <span className="text-foreground">
                      Also terminate these {missing.length.toLocaleString()}{" "}
                      {missing.length === 1 ? "person" : "people"}, effective
                      today
                    </span>
                  </label>
                )}
              </div>
              {missing.map((op, i) => (
                <OpRow key={`m${i}`} op={op} muted={!terminateMissing} />
              ))}
            </div>
          )}

          {preview && preview.issues.length > 0 && (
            <Section title="Issues (skipped)">
              {preview.issues.map((issue, i) => (
                <div
                  key={`i${i}`}
                  className="border-t border-border px-3 py-2 text-sm text-error"
                >
                  Row {issue.row} ({issue.record_type}): {issue.message}
                </div>
              ))}
            </Section>
          )}
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={onApply} disabled={applying || applies === 0}>
            {applying && <Loader2 className="size-4 animate-spin" />}
            Apply {applies.toLocaleString()}{" "}
            {applies === 1 ? "change" : "changes"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function OpRow({
  op,
  children,
  muted,
}: {
  op: AdcOp;
  children?: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <div
      className={
        "border-t border-border px-3 py-2 text-sm" + (muted ? " opacity-60" : "")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-foreground">
          {op.name || op.staff_id || `Row ${op.row}`}
          {op.staff_id && op.name ? (
            <span className="text-muted-foreground"> · {op.staff_id}</span>
          ) : null}
        </span>
        <Badge variant="default" className="shrink-0 capitalize">
          {op.record_type}
        </Badge>
      </div>
      {children}
    </div>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border">
      <div className="bg-muted px-3 py-1.5">
        <div className="text-xs font-medium text-muted-foreground">{title}</div>
        {note && <div className="mt-0.5 text-2xs text-subtle">{note}</div>}
      </div>
      {children}
    </div>
  );
}
