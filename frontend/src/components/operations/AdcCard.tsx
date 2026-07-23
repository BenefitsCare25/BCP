import { useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { FileUp, GitCompareArrows, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ReportDownloadButton } from "./ReportDownloadButton";
import { useAdcApply, useAdcPreview } from "@/api/adc";
import { formatError } from "@/lib/errors";
import type { AdcOp, AdcPreview } from "@/types";

interface Props {
  policyYearId: string;
}

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
};

function label(field: string): string {
  return FIELD_LABEL[field] ?? field.replace(/_/g, " ");
}

function OpRow({ op, children }: { op: AdcOp; children?: React.ReactNode }) {
  return (
    <div className="border-t border-border px-3 py-2 text-sm">
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

export function AdcCard({ policyYearId }: Props) {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AdcPreview | null>(null);
  const previewMut = useAdcPreview();
  const applyMut = useAdcApply();

  function onPick(picked: File | null) {
    if (fileInput.current) fileInput.current.value = "";
    if (!picked) return;
    setFile(picked);
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
      { file, policyYearId },
      {
        onSuccess: (r) => {
          toast.success(
            `Applied — ${r.added} added, ${r.changed} changed, ${r.deleted} terminated`,
          );
          if (r.flex_errors.length) {
            toast.warning(r.flex_errors.join(" "));
          }
          if (r.added || r.deleted) {
            toast.info(
              "Roster changed — save updated insurer listing versions.",
              {
                action: {
                  label: "Open Reports",
                  onClick: () =>
                    navigate({ to: "/reports", search: { tab: "pa" } }),
                },
              },
            );
          }
          setPreview(null);
          setFile(null);
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  const c = preview?.counts ?? {};
  const nothingToApply =
    (c.additions ?? 0) + (c.changes ?? 0) + (c.deletions ?? 0) === 0;

  return (
    <>
      <Card>
        <CardContent className="p-5 flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-4">
            <div className="size-11 rounded-lg bg-accent text-accent-foreground grid place-items-center">
              <GitCompareArrows className="size-5" />
            </div>
            <div>
              <div className="font-medium text-foreground">
                Bulk update roster (ADC)
              </div>
              <div className="text-sm text-muted-foreground">
                Download the prefilled template, mark Add / Change / Delete, then
                upload to preview and apply.
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <ReportDownloadButton
              path={`/policy-years/${policyYearId}/adc/template`}
              filename="adc-template.xlsx"
              label="Download template"
            />
            <input
              ref={fileInput}
              type="file"
              accept=".xls,.xlsx,.xlsm"
              aria-label="Upload ADC movement file"
              className="hidden"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
            />
            <Button
              onClick={() => fileInput.current?.click()}
              disabled={previewMut.isPending}
            >
              {previewMut.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <FileUp className="size-4" />
              )}
              Upload changes
            </Button>
          </div>
        </CardContent>
      </Card>

      <Sheet
        open={preview !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPreview(null);
            setFile(null);
          }
        }}
      >
        <SheetContent className="flex w-full flex-col sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>Review roster changes</SheetTitle>
          </SheetHeader>
          <SheetBody className="flex-1 space-y-4 overflow-y-auto">
            <div className="flex flex-wrap gap-2 text-sm">
              <Badge variant="good">{c.additions ?? 0} additions</Badge>
              <Badge variant="info">{c.changes ?? 0} changes</Badge>
              <Badge variant="warn">{c.deletions ?? 0} terminations</Badge>
              {(c.issues ?? 0) > 0 && (
                <Badge variant="error">{c.issues} issues</Badge>
              )}
            </div>

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
                          <span className="line-through">{d.old || "—"}</span>{" "}
                          → <span className="text-foreground">{d.new || "—"}</span>
                        </li>
                      ))}
                    </ul>
                  </OpRow>
                ))}
              </Section>
            )}

            {preview && preview.deletions.length > 0 && (
              <Section title="Terminations">
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
            <Button
              variant="outline"
              onClick={() => {
                setPreview(null);
                setFile(null);
              }}
            >
              Cancel
            </Button>
            <Button onClick={onApply} disabled={applyMut.isPending || nothingToApply}>
              {applyMut.isPending && <Loader2 className="size-4 animate-spin" />}
              Confirm &amp; apply
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border">
      <div className="bg-muted px-3 py-1.5 text-xs font-medium text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}
