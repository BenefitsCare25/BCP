import { useState, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { InfoHint } from "@/components/ui/tooltip";
import { ImportAction } from "./ImportAction";
import { ListingSyncSheet } from "./ListingSyncSheet";
import { useListingApply, useListingPreview } from "@/api/adc";
import { formatError } from "@/lib/errors";
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
  const previewMut = useListingPreview();
  const applyMut = useListingApply();

  function close() {
    setPreview(null);
    setFile(null);
    // Reset the opt-in with the sheet. It must never survive into the NEXT
    // upload, whose missing set is a different group of people.
    setTerminateMissing(false);
  }

  function onPick(picked: File) {
    setFile(picked);
    setTerminateMissing(false);
    previewMut.mutate(
      { file: picked, policyYearId },
      {
        onSuccess: (p) => setPreview(p),
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
        terminateMissing,
        missingDigest: preview?.missing_digest ?? null,
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
              templatePath={`/policy-years/${policyYearId}/reports/member-listing-template`}
              templateFilename="member-listing-template.xlsx"
              templateLabel="Download template"
              uploadLabel="Upload listing"
              onPick={onPick}
              pending={previewMut.isPending}
              // Filled only on an empty roster, where uploading is the page's
              // one job. On a populated one the tab row already has a filled
              // "Run matching"; two primaries in one header compete.
              primary={hasRows === false}
            />

            <InfoHint side="bottom">
              <p className="mb-1.5">
                <strong>Download template</strong> — the full member listing
                (staff ID, name, NRIC/FIN, DOB, category, bank details, insurer
                member IDs), pre-filled with everyone already on file. It has an{" "}
                <em>Employees</em> and a <em>Dependants</em> sheet.
              </p>
              <p className="mb-1.5">
                <strong>Upload listing</strong> — edit that file and send it
                back. New rows are added, edited rows are updated, and a row
                whose leaving date has passed is terminated. You review all of
                it before anything is applied.
              </p>
              <p>
                Someone on file but missing from the upload is listed
                separately, and is never terminated unless you tick it.
              </p>
            </InfoHint>
          </div>
        </CardContent>
      </Card>

      <ListingSyncSheet
        preview={preview}
        onClose={close}
        onApply={onApply}
        applying={applyMut.isPending}
        terminateMissing={terminateMissing}
        onTerminateMissingChange={setTerminateMissing}
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
