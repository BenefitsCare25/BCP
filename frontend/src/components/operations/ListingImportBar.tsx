import { useState, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { InfoHint } from "@/components/ui/tooltip";
import { ImportAction } from "./ImportAction";
import { ListingSyncSheet } from "./ListingSyncSheet";
import { EmployeeListingSheet, LISTING_NOT_COVERED } from "./EmployeeListingSheet";
import { useListingApply, useListingPreview } from "@/api/adc";
import {
  mappingFromPreview,
  useEmployeeListingApply,
  useEmployeeListingPreview,
  type ListingMapping,
  type ListingPreview,
} from "@/api/employeeListing";
import { errorCode, formatError } from "@/lib/errors";
import type { AdcPreview } from "@/types";

/**
 * The listing page's one header row: what is on file (left), and the one way to
 * change it (right) — download the listing, edit it, upload it back.
 *
 * This replaced two stacked upload cards plus a three-tile stat grid (~330px of
 * icon/heading/description scaffolding above the table), and then replaced the
 * second import job as well. There used to be a separate ADC template carrying
 * an `Action` column the broker marked Add/Change/Delete by hand — manual work
 * restating what a diff can compute. Uploading the listing now derives the
 * movements and shows them for confirmation (`ListingSyncSheet`).
 *
 * That also fixes what the old plain upload did: it resolved each person's
 * identity and then skipped them as a "duplicate", so a broker who filled in
 * salaries or insurer member IDs on the pre-filled template — which
 * `member_listing_template.py` explicitly describes as doubling as an update
 * template — got "0 added · 491 duplicates skipped" and lost every edit.
 *
 * One upload covers both sheets of the file (`Employees` / `Dependants`), so
 * the button is the same action on both tabs and nothing rides along unseen.
 */
interface Props {
  policyYearId: string;
  /** Left-hand readout — use `ListingCount`. */
  stats: ReactNode;
  /**
   * Whether anything is on file. Only affects emphasis: with an empty roster
   * the upload is the page's primary action. `undefined` while the count is
   * loading, so the fill doesn't swap a frame later.
   */
  hasRows?: boolean;
}

export function ListingImportBar({ policyYearId, stats, hasRows }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AdcPreview | null>(null);
  const [terminateMissing, setTerminateMissing] = useState(false);
  const [columnMapping, setColumnMapping] = useState<Record<
    string,
    string | null
  > | null>(null);
  const [mappingDirty, setMappingDirty] = useState(false);
  const previewMut = useListingPreview();
  const applyMut = useListingApply();
  // A company's own Employee Listing (per-product cover columns) is read by
  // the listing import; any other workbook falls back to the template sync.
  const elPreviewMut = useEmployeeListingPreview();
  const elApplyMut = useEmployeeListingApply();
  const [elPreview, setElPreview] = useState<ListingPreview | null>(null);
  const [elMapping, setElMapping] = useState<ListingMapping>({
    block_products: {},
    labels: {},
  });
  const [elDirty, setElDirty] = useState(false);

  function acceptElPreview(next: ListingPreview) {
    setElPreview(next);
    setElMapping(mappingFromPreview(next));
    setElDirty(false);
  }

  function closeEl() {
    setElPreview(null);
    setFile(null);
    setTerminateMissing(false);
    setElDirty(false);
  }

  function onElLabelChange(block: number, key: string, value: string) {
    setElMapping((current) => {
      const labels = { ...current.labels, [String(block)]: { ...current.labels[String(block)] } };
      labels[String(block)][key] =
        value === LISTING_NOT_COVERED ? { not_covered: true } : { category_id: value };
      return { ...current, labels };
    });
  }

  function onElBlockToggle(block: number, code: string, on: boolean) {
    setElMapping((current) => {
      const chosen = new Set(current.block_products[String(block)] ?? []);
      if (on) chosen.add(code);
      else chosen.delete(code);
      return {
        ...current,
        block_products: { ...current.block_products, [String(block)]: [...chosen].sort() },
      };
    });
    setElDirty(true);
  }

  function onElRecheck() {
    if (!file) return;
    elPreviewMut.mutate(
      { file, policyYearId, mapping: elMapping },
      { onSuccess: acceptElPreview, onError: (e) => toast.error(formatError(e)) },
    );
  }

  function onElApply() {
    if (!file || !elPreview) return;
    elApplyMut.mutate(
      {
        file,
        policyYearId,
        mapping: elMapping,
        // Never sent over unread rows: the sheet hides the tick, and a tick
        // from before a recheck must not reach the server, which refuses it.
        terminateMissing:
          terminateMissing && (elPreview.members.counts.dropped_rows ?? 0) === 0,
        missingDigest: elPreview.members.missing_digest ?? null,
      },
      {
        onSuccess: (r) => {
          const parts = [
            r.added ? `${r.added} added` : null,
            r.changed ? `${r.changed} changed` : null,
            r.deleted || r.missing_terminated
              ? `${r.deleted + r.missing_terminated} terminated`
              : null,
            `${r.assignments.toLocaleString()} listed covers`,
            r.joiner_rules_written ? `${r.joiner_rules_written} joiner rules` : null,
            r.underwriting_updated
              ? `${r.underwriting_updated} underwriting cases updated`
              : null,
          ].filter(Boolean);
          toast.success(`Applied — ${parts.join(", ")}`);
          if (r.flex_errors.length) toast.warning(r.flex_errors.join(" "));
          closeEl();
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  function close() {
    setPreview(null);
    setFile(null);
    // Reset the opt-in with the sheet. It must never survive into the NEXT
    // upload, whose missing set is a different group of people.
    setTerminateMissing(false);
    setColumnMapping(null);
    setMappingDirty(false);
  }

  function acceptPreview(next: AdcPreview) {
    const decisions: Record<string, string | null> = {};
    for (const column of next.roster_mapping?.columns ?? []) {
      if (column.status === "mapped" && column.attribute_id) {
        decisions[String(column.index)] = column.attribute_id;
      } else if (column.status === "ignored") {
        decisions[String(column.index)] = null;
      }
    }
    setColumnMapping(decisions);
    setMappingDirty(false);
    setPreview(next);
  }

  function onPick(picked: File) {
    setFile(picked);
    setTerminateMissing(false);
    elPreviewMut.mutate(
      { file: picked, policyYearId },
      {
        onSuccess: acceptElPreview,
        onError: (e) => {
          if (errorCode(e) !== "not_employee_listing") {
            toast.error(formatError(e));
            return;
          }
          previewMut.mutate(
            { file: picked, policyYearId },
            {
              onSuccess: acceptPreview,
              onError: (err) => toast.error(formatError(err)),
            },
          );
        },
      },
    );
  }

  function onMappingChange(index: number, attributeId: string | null) {
    setColumnMapping((current) => ({
      ...(current ?? {}),
      [String(index)]: attributeId,
    }));
    setMappingDirty(true);
  }

  function onIgnoreUnmapped(indexes: number[]) {
    setColumnMapping((current) => {
      const next = { ...(current ?? {}) };
      for (const index of indexes) next[String(index)] = null;
      return next;
    });
    setMappingDirty(true);
  }

  function onRecheckMapping() {
    if (!file || !columnMapping) return;
    previewMut.mutate(
      { file, policyYearId, employeeColumnMapping: columnMapping },
      {
        onSuccess: acceptPreview,
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  function onApply() {
    if (!file) return;
    applyMut.mutate(
      {
        file,
        policyYearId,
        terminateMissing:
          terminateMissing && (preview?.counts.dropped_rows ?? 0) === 0,
        missingDigest: preview?.missing_digest ?? null,
        mappingDigest: preview?.roster_mapping?.digest ?? null,
        employeeColumnMapping: columnMapping ?? undefined,
      },
      {
        onSuccess: (r) => {
          const parts = [
            r.added ? `${r.added} added` : null,
            r.changed ? `${r.changed} changed` : null,
            r.deleted || r.missing_terminated
              ? `${r.deleted + r.missing_terminated} terminated`
              : null,
          ].filter(Boolean);
          toast.success(
            parts.length ? `Applied — ${parts.join(", ")}` : "Nothing to apply",
          );
          if (r.flex_errors.length) toast.warning(r.flex_errors.join(" "));
          close();
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  return (
    <>
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-x-6 gap-y-4 p-4">
          <div className="min-w-0">{stats}</div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <ImportAction
              // The Full Employee Listing in the company's own layout doubles as
              // the editable listing: re-uploading it maps straight back, and
              // its masked NRICs are never imported over the real numbers.
              templatePath={`/policy-years/${policyYearId}/reports/workbooks/full-el?masked=true&employee_status=active`}
              templateFilename="employee-listing.xlsx"
              templateLabel="Download listing"
              uploadLabel="Upload listing"
              onPick={onPick}
              pending={previewMut.isPending || elPreviewMut.isPending}
              // Filled only on an empty roster, where uploading is the page's
              // one job. On a populated one the tab row already has a filled
              // "Run matching"; two primaries in one header compete.
              primary={hasRows === false}
            />

            <InfoHint side="bottom">
              <p className="mb-1.5">
                <strong>Download listing</strong> — the company&apos;s Employee
                Listing in its own layout, pre-filled with everyone active on
                file: employees followed by their dependants, and a column block
                per product. NRIC/FIN numbers are masked.
              </p>
              <p className="mb-1.5">
                <strong>Upload listing</strong> — the edited download or the
                client&apos;s own Employee Listing. Each listed category is mapped
                to its placement-slip category; mappings you confirmed before
                are reused. New rows are added, edited rows are updated, and a
                row whose leaving date has passed is terminated. You review all
                of it before anything is applied.
              </p>
              <p>
                Someone on file but missing from the upload is listed
                separately, and is never terminated unless you tick it.
              </p>
            </InfoHint>
          </div>
        </CardContent>
      </Card>

      <EmployeeListingSheet
        preview={elPreview}
        mapping={elMapping}
        onLabelChange={onElLabelChange}
        onBlockProductToggle={onElBlockToggle}
        dirty={elDirty}
        onRecheck={onElRecheck}
        checking={elPreviewMut.isPending}
        terminateMissing={terminateMissing}
        onTerminateMissingChange={setTerminateMissing}
        onClose={closeEl}
        onApply={onElApply}
        applying={elApplyMut.isPending}
      />

      <ListingSyncSheet
        preview={preview}
        onClose={close}
        onApply={onApply}
        applying={applyMut.isPending}
        terminateMissing={terminateMissing}
        onTerminateMissingChange={setTerminateMissing}
        columnMapping={columnMapping ?? {}}
        onColumnMappingChange={onMappingChange}
        onIgnoreUnmapped={onIgnoreUnmapped}
        mappingDirty={mappingDirty}
        onRecheckMapping={onRecheckMapping}
        checkingMapping={previewMut.isPending}
      />
    </>
  );
}

/**
 * The bar's left-hand readout: how many rows are on file, and one line of state
 * beneath it.
 *
 * Deliberately NOT three equal stat tiles. On a healthy listing two of those
 * three numbers are the same number and the third is zero, so they read as
 * decoration; the line below states the exception instead, and only when there
 * is one.
 */
export function ListingCount({
  value,
  noun,
  children,
}: {
  value: number;
  noun: string;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-lg font-semibold tabular-nums text-foreground">
          {value.toLocaleString()}
        </span>
        <span className="text-sm text-muted-foreground">{noun}</span>
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
        {children}
      </div>
    </div>
  );
}

/**
 * The one number on the readout worth acting on, as a filter link.
 *
 * The count itself stays in foreground ink and the amber lives in the icon:
 * `--color-warn` measures 3.19:1 on card, which clears 1.4.11 for a graphic but
 * fails 1.4.3 for 12px text.
 */
export function ListingExceptionLink({
  count,
  label,
  onClick,
}: {
  count: number;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-sm font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
    >
      <AlertTriangle className="size-3.5 text-warn" />
      <span className="tabular-nums">{count.toLocaleString()}</span> {label}
    </button>
  );
}
