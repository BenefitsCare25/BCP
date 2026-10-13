import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { ListingMapping, ListingPreview } from "@/api/employeeListing";
import { EntityLinks } from "./listing-review/EntityLinks";
import { fmtDay } from "@/lib/format";
import { count } from "./listing-review/format";
import { JoinerRules } from "./listing-review/JoinerRules";
import { MappingTable, NOT_COVERED, unmappedCount } from "./listing-review/MappingTable";
import { findingsFor, ReviewFindings } from "./listing-review/ReviewFindings";
import { ReviewSummary } from "./listing-review/ReviewSummary";

/**
 * Review a company's own Employee Listing before it is applied.
 *
 * The listing names each person's category per product in its own wording,
 * which rarely matches the placement slip ("2) All Professionals … - Plan 1
 * (1 Bed Restr)" vs the slip's "All Professionals, Executives & Management
 * Staff", plan 1). Every wording is mapped to a slip category — across all of
 * the product's policies, so a directors' policy shows beside the main one —
 * or marked not covered. Reviewed mappings are remembered for the company.
 *
 * Order follows the broker's questions: is this the right file and what will
 * it do (summary), what does each category mean (mapping), do the entities
 * line up, and what needs a second look.
 */
interface Props {
  preview: ListingPreview | null;
  mapping: ListingMapping;
  onLabelChange: (block: number, key: string, value: string) => void;
  onBlockProductToggle: (block: number, code: string, on: boolean) => void;
  onEntityLinkChange: (spelling: string, target: string | null) => void;
  dirty: boolean;
  onRecheck: () => void;
  checking: boolean;
  terminateMissing: boolean;
  onTerminateMissingChange: (value: boolean) => void;
  onClose: () => void;
  onApply: () => void;
  applying: boolean;
}

export function EmployeeListingSheet({
  preview,
  mapping,
  onLabelChange,
  onBlockProductToggle,
  onEntityLinkChange,
  dirty,
  onRecheck,
  checking,
  terminateMissing,
  onTerminateMissingChange,
  onClose,
  onApply,
  applying,
}: Props) {
  const unmapped = preview ? unmappedCount(preview, mapping) : 0;
  const blocked = dirty || unmapped > 0;
  const status = dirty
    ? "Recheck after changing a block's slip products."
    : unmapped > 0
      ? `Map ${count(unmapped, "category", "categories")} to apply.`
      : "Ready to apply.";

  return (
    <Sheet open={preview !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="flex w-full flex-col sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Review employee listing</SheetTitle>
          {preview && (
            <SheetDescription className="text-xs">
              Sheet {preview.layout.sheet} · headers on row {preview.layout.header_row}
              {preview.layout.reference_date &&
                ` · ages as at ${fmtDay(preview.layout.reference_date)}`}
              {preview.reused_profile && " · mappings you reviewed before are applied"}
            </SheetDescription>
          )}
        </SheetHeader>
        <SheetBody className="flex-1 space-y-5 overflow-y-auto">
          {preview && (
            <>
              <FileNotices preview={preview} />
              <ReviewSummary people={preview.people} members={preview.members} />
              <MappingTable
                preview={preview}
                mapping={mapping}
                onLabelChange={onLabelChange}
                onBlockProductToggle={onBlockProductToggle}
                disabled={dirty}
              />
              <EntityLinks
                entities={preview.entities}
                mapping={mapping}
                onLinkChange={onEntityLinkChange}
                disabled={dirty}
              />
              <ReviewFindings findings={findingsFor(preview)} />
              <MissingMembers
                preview={preview}
                terminateMissing={terminateMissing}
                onTerminateMissingChange={onTerminateMissingChange}
              />
              <JoinerRules products={preview.joiner_rules} />
            </>
          )}
        </SheetBody>
        <SheetFooter className="flex-col items-stretch sm:flex-row sm:items-center sm:justify-between">
          <p
            role="status"
            className={`text-xs ${blocked ? "font-medium text-foreground" : "text-muted-foreground"}`}
          >
            {status}
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            {dirty && (
              <Button variant="outline" onClick={onRecheck} disabled={checking}>
                {checking ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <RefreshCw className="size-4" />
                )}
                Recheck products
              </Button>
            )}
            <Button onClick={onApply} disabled={applying || blocked}>
              {applying && <Loader2 className="size-4 animate-spin" />}
              Apply listing
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/**
 * File-level problems that make the whole review suspect — the wrong year's
 * listing, or rows the reader could not turn into anyone. Shown first.
 */
function FileNotices({ preview }: { preview: ListingPreview }) {
  const unreadable = preview.members.counts.dropped_rows ?? 0;
  const warnings = preview.notices.filter((n) => n.tone === "warn").map((n) => n.message);
  if (unreadable > 0) {
    warnings.push(
      `${count(unreadable, "row")} could not be read and ${unreadable === 1 ? "is" : "are"} ` +
        "not included. An employee row needs a name and a Staff ID or NRIC; a dependant row " +
        "needs its employee's row above it.",
    );
  }
  if (warnings.length === 0) return null;
  return (
    <div role="alert" className="space-y-1.5 rounded-lg border border-warn/40 bg-warn-soft/40 px-3 py-2">
      {warnings.map((message) => (
        <p key={message} className="flex items-start gap-2 text-sm text-foreground">
          <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
          {message}
        </p>
      ))}
    </div>
  );
}

function MissingMembers({
  preview,
  terminateMissing,
  onTerminateMissingChange,
}: {
  preview: ListingPreview;
  terminateMissing: boolean;
  onTerminateMissingChange: (value: boolean) => void;
}) {
  const missing = preview.members.missing;
  const unreadable = preview.members.counts.dropped_rows ?? 0;
  if (missing.length === 0) return null;
  return (
    <section className="space-y-2 rounded-lg border border-border px-3 py-2.5">
      <p className="text-sm text-foreground">
        {count(missing.length, "person", "people")} on the roster{" "}
        {missing.length === 1 ? "is" : "are"} not in this listing.
      </p>
      {unreadable > 0 ? (
        // Absence is only evidence when the whole file was read; the server
        // refuses the opt-in too (termination_blocked_dropped_rows).
        <p className="rounded-md bg-muted px-2.5 py-2 text-xs text-foreground">
          Ending cover is unavailable while {count(unreadable, "row")} could not be read — one
          of them may be one of these people. Fix the file and upload it again.
        </p>
      ) : (
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <Checkbox
            checked={terminateMissing}
            onCheckedChange={(v) => onTerminateMissingChange(v === true)}
            className="mt-0.5"
          />
          <span className="text-foreground">
            Also end cover for {missing.length === 1 ? "this person" : "these people"}, effective
            today
          </span>
        </label>
      )}
    </section>
  );
}

export { NOT_COVERED as LISTING_NOT_COVERED };
