import { useState } from "react";
import { Package } from "lucide-react";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useReportBundles } from "@/api/reports";

/** One-click download of a whole submission as a .zip.
 *
 *  The unit a broker actually works in is the submission, not the file: an
 *  insurer receives five documents and there is no point at which four of them
 *  is a meaningful thing to have sent. Each row here is that unit.
 *
 *  The bundle list and its insurers are SERVED (`GET .../reports/bundles`),
 *  never assembled here — the picker must offer exactly the insurers the
 *  download will accept, and deriving them client-side is how a picker comes to
 *  offer an insurer the server then 404s.
 */
export function ReportBundleCard({
  policyYearId,
  masked,
}: {
  policyYearId: string;
  masked: boolean;
}) {
  const { data: bundles = [] } = useReportBundles(policyYearId);
  // Per-bundle insurer choice. Keyed by bundle so two insurer-scoped bundles
  // can't share (and silently overwrite) one selection.
  const [insurers, setInsurers] = useState<Record<string, string>>({});

  if (!bundles.length) return null;

  return (
    <div className="space-y-2">
      {bundles.map((bundle) => {
        const chosen = insurers[bundle.key] ?? "";
        const blocked = bundle.requires_insurer && !chosen;
        const query = new URLSearchParams();
        if (!masked) query.set("masked", "false");
        if (bundle.requires_insurer && chosen) query.set("insurer", chosen);
        const qs = query.toString();
        return (
          <div
            key={bundle.key}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
          >
            <div className="flex min-w-0 items-start gap-3">
              <Package className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">
                  {bundle.label}
                </p>
                <p className="text-xs text-muted-foreground">
                  {bundle.description}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-2xs uppercase tracking-wide text-subtle">
                {bundle.file_count} files
              </span>
              {bundle.requires_insurer && (
                <Select
                  value={chosen}
                  onValueChange={(v) =>
                    setInsurers((prev) => ({ ...prev, [bundle.key]: v }))
                  }
                  disabled={!bundle.insurers.length}
                >
                  <SelectTrigger className="w-[170px]" aria-label="Insurer">
                    <SelectValue
                      placeholder={
                        bundle.insurers.length
                          ? "Select insurer"
                          : "No insurers configured"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {bundle.insurers.map((name) => (
                      <SelectItem key={name} value={name}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <ReportDownloadButton
                path={`/policy-years/${policyYearId}/reports/bundles/${bundle.key}${qs ? `?${qs}` : ""}`}
                filename={`${bundle.key}.zip`}
                label="Download set"
                size="sm"
                disabled={blocked}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
