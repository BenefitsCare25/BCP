import { Eye } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { FieldComparison } from "@/api/claims";

const STATUS: Record<
  FieldComparison["status"],
  { label: string; variant: "good" | "warn" | "error" | "outline" }
> = {
  MATCH: { label: "Match", variant: "good" },
  MISMATCH: { label: "Mismatch", variant: "error" },
  MISSING_IN_PDF: { label: "Not in documents", variant: "warn" },
  MISSING_ON_PAGE: { label: "Not on claim", variant: "outline" },
  UNCERTAIN: { label: "Uncertain", variant: "warn" },
};

const FIELD_LABELS: Record<string, string> = {
  amount_claimed: "Amount claimed",
  incurred_date: "Incurred date",
  provider_name: "Provider",
  invoice_number: "Invoice number",
  currency: "Currency",
  diagnosis: "Diagnosis",
};

export function FieldComparisonTable({
  comparisons,
  hideOmittedNotes = false,
}: {
  comparisons: FieldComparison[];
  hideOmittedNotes?: boolean;
}) {
  if (comparisons.length === 0) {
    return (
      <div className="text-sm text-muted-foreground p-4 text-center border border-dashed border-border rounded-md">
        No field comparisons recorded.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Field</TableHead>
          <TableHead>Claim value</TableHead>
          <TableHead>Document value</TableHead>
          <TableHead>Result</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {comparisons.map((c) => {
          const cfg = STATUS[c.status] ?? { label: c.status, variant: "outline" as const };
          const showNotes =
            c.notes &&
            !c.vision_verified &&
            !(
              hideOmittedNotes &&
              c.notes === "The AI response omitted this configured comparison."
            );
          return (
            <TableRow key={c.field_name}>
              <TableCell className="font-medium">
                {FIELD_LABELS[c.field_name] ?? c.field_name}
              </TableCell>
              <TableCell>{c.claim_value ?? "—"}</TableCell>
              <TableCell>{c.document_value ?? "—"}</TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <Badge variant={cfg.variant}>{cfg.label}</Badge>
                  {c.vision_verified && (
                    <span
                      className="inline-flex items-center gap-1 text-2xs text-muted-foreground"
                      title={c.notes ?? "Confirmed by vision re-check"}
                    >
                      <Eye className="size-3" /> vision
                    </span>
                  )}
                </div>
                {showNotes && (
                  <div className="text-2xs text-muted-foreground mt-1 max-w-56">
                    {c.notes}
                  </div>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
      </Table>
    </div>
  );
}
