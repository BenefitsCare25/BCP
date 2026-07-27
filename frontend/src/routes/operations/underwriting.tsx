import { useMemo, useState } from "react";
import { ArrowLeft, Loader2, RefreshCw, ShieldQuestion } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageGuide } from "@/components/ui/page-guide";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SkeletonTable } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DECISION_LABELS,
  REVIEW_STATUS_LABELS,
  useDecideUnderwriting,
  useRefreshUnderwriting,
  useUnderwritingQueue,
  useUpdateReview,
  type UnderwritingCaseLine,
  type UnderwritingReview,
} from "@/api/underwriting";
import { useSession } from "@/stores/session";
import { formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";

const OPEN_STATUSES = new Set([
  "pending_requirements",
  "pending_employee",
  "pending_insurer",
  "pending_hr",
]);

interface LineEdit {
  status: UnderwritingCaseLine["status"];
  accepted: string;
  remarks: string;
}

function lineEdit(line: UnderwritingCaseLine): LineEdit {
  return {
    status: line.status,
    accepted: String(line.accepted_si),
    remarks: line.remarks ?? "",
  };
}

function ProductDecisionBlock({
  index,
  line,
  edit,
  onChange,
}: {
  index: number;
  line: UnderwritingCaseLine;
  edit: LineEdit;
  onChange: (next: LineEdit) => void;
}) {
  return (
    <div className="grid gap-x-6 gap-y-1.5 border-t border-border py-4 sm:grid-cols-[140px_1fr]">
      <div className="text-sm text-muted-foreground">Product #{index + 1}</div>
      <div className="space-y-1.5">
        <dl className="grid grid-cols-[170px_1fr] gap-y-0.5 text-sm">
          <dt className="text-muted-foreground">Product</dt>
          <dd className="font-medium text-foreground">
            {line.product_code} — {line.product_name}
          </dd>
          <dt className="text-muted-foreground">Requested amount</dt>
          <dd>{fmtCurrency(line.requested_si)}</dd>
          <dt className="text-muted-foreground">Guaranteed amount</dt>
          <dd>{fmtCurrency(line.guaranteed_si)}</dd>
          <dt className="text-muted-foreground">Underwritten amount</dt>
          <dd>
            {line.pending_si > 0 ? (
              <span className="text-amber-500">
                {fmtCurrency(line.pending_si)}
              </span>
            ) : (
              fmtCurrency(Math.max(line.requested_si - line.guaranteed_si, 0))
            )}
          </dd>
        </dl>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 pt-1.5">
          <div className="flex items-center gap-2">
            <Label className="text-xs text-muted-foreground">Decision</Label>
            <Select
              value={edit.status}
              onValueChange={(v) => {
                const status = v as UnderwritingCaseLine["status"];
                // Approving means the insurer took the request as asked, so
                // move the amount off the guaranteed floor to the full
                // requested sum (edit down for a partial approval). Leaving it
                // at the floor would record "approved" while quietly dropping
                // the approved excess from cover.
                const atFloor =
                  Number(edit.accepted.trim()) === line.guaranteed_si;
                const approving =
                  status === "approved_standard" ||
                  status === "approved_substandard";
                onChange({
                  ...edit,
                  status,
                  accepted:
                    approving && atFloor
                      ? String(line.requested_si)
                      : edit.accepted,
                });
              }}
            >
              <SelectTrigger className="h-8 w-[220px] text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(DECISION_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Label className="text-xs text-muted-foreground">
              Accepted amount
            </Label>
            <Input
              value={edit.accepted}
              onChange={(e) => onChange({ ...edit, accepted: e.target.value })}
              className="h-8 w-32 text-right text-sm"
              aria-label={`${line.product_code} accepted amount`}
            />
          </div>
          {line.decided_on && (
            <span className="text-xs text-muted-foreground">
              Decided {line.decided_on}
            </span>
          )}
        </div>
        <textarea
          rows={2}
          className="mt-1.5 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring"
          placeholder="Remarks (e.g. Accepted on 6 May 2026)"
          value={edit.remarks}
          maxLength={1024}
          onChange={(e) => onChange({ ...edit, remarks: e.target.value })}
          aria-label={`${line.product_code} remarks`}
        />
      </div>
    </div>
  );
}

function ReviewDetail({
  review,
  policyYearId,
  onBack,
}: {
  review: UnderwritingReview;
  policyYearId: string;
  onBack: () => void;
}) {
  const updateReview = useUpdateReview(policyYearId);
  const decide = useDecideUnderwriting(policyYearId);
  const [status, setStatus] = useState(review.status);
  const [requirements, setRequirements] = useState(review.requirements ?? "");
  const [edits, setEdits] = useState<Record<string, LineEdit>>(() =>
    Object.fromEntries(review.cases.map((c) => [c.id, lineEdit(c)])),
  );

  const headerDirty =
    status !== review.status || requirements !== (review.requirements ?? "");
  const dirtyLines = review.cases.filter((c) => {
    const edit = edits[c.id];
    if (!edit) return false;
    return (
      edit.status !== c.status ||
      Number(edit.accepted.trim()) !== c.accepted_si ||
      edit.remarks !== (c.remarks ?? "")
    );
  });
  // A blank amount must NOT parse to 0 (Number("") === 0) and silently record
  // a zero acceptance — treat empty/non-numeric as invalid so Save disables.
  const linesValid = dirtyLines.every((c) => {
    const edit = edits[c.id];
    const trimmed = edit.accepted.trim();
    const parsed = Number(trimmed);
    return trimmed !== "" && Number.isFinite(parsed) && parsed >= 0;
  });
  const dirty = headerDirty || dirtyLines.length > 0;
  const busy = updateReview.isPending || decide.isPending;

  const save = async () => {
    try {
      if (headerDirty) {
        await updateReview.mutateAsync({
          reviewId: review.id,
          status,
          requirements: requirements.trim() || null,
        });
      }
      for (const line of dirtyLines) {
        const edit = edits[line.id];
        await decide.mutateAsync({
          caseId: line.id,
          status: edit.status,
          accepted_si: Number(edit.accepted.trim()),
          remarks: edit.remarks.trim() || null,
        });
      }
      toast.success("Underwriting case saved");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const discard = () => {
    setStatus(review.status);
    setRequirements(review.requirements ?? "");
    setEdits(Object.fromEntries(review.cases.map((c) => [c.id, lineEdit(c)])));
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="size-4" /> Back to queue
          </Button>
          <Badge variant={OPEN_STATUSES.has(review.status) ? "default" : "outline"}>
            {REVIEW_STATUS_LABELS[review.status]}
          </Badge>
        </div>
        <CardTitle className="text-base">
          Underwriting — {review.insurer || "No insurer assigned"}
        </CardTitle>
        <CardDescription>
          Case-level status and requirements, with the insurer's decision per
          product. Accepted amounts are capped at the requested sum.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[170px_1fr] gap-y-1 text-sm">
          <dt className="text-muted-foreground">Member</dt>
          <dd className="font-medium text-foreground">
            {review.subject_name || "—"}
            {review.staff_id && (
              <span className="ml-2 text-xs text-muted-foreground">
                {review.staff_id}
              </span>
            )}
          </dd>
          <dt className="text-muted-foreground">Relationship</dt>
          <dd>{review.relationship}</dd>
          <dt className="text-muted-foreground">Identification No.</dt>
          <dd>{review.identification_no || "—"}</dd>
        </dl>

        <div className="mt-4 flex items-center gap-2">
          <Label className="w-[162px] shrink-0 text-xs text-muted-foreground">
            Status
          </Label>
          <Select
            value={status}
            onValueChange={(v) => setStatus(v as UnderwritingReview["status"])}
          >
            <SelectTrigger className="h-9 w-full max-w-md text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(REVIEW_STATUS_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="mt-3 flex items-start gap-2">
          <Label className="w-[162px] shrink-0 pt-2 text-xs text-muted-foreground">
            Requirements
          </Label>
          <textarea
            rows={3}
            className="w-full max-w-2xl resize-y rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring"
            placeholder="Medical / evidence requirements the insurer asked for"
            value={requirements}
            maxLength={2000}
            onChange={(e) => setRequirements(e.target.value)}
            aria-label="Underwriting requirements"
          />
        </div>

        <div className="mt-4">
          {review.cases.map((line, i) => (
            <ProductDecisionBlock
              key={line.id}
              index={i}
              line={line}
              edit={edits[line.id] ?? lineEdit(line)}
              onChange={(next) =>
                setEdits((prev) => ({ ...prev, [line.id]: next }))
              }
            />
          ))}
        </div>

        <div className="mt-4 flex items-center gap-2 border-t border-border pt-4">
          <Button disabled={!dirty || !linesValid || busy} onClick={save}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : "Save changes"}
          </Button>
          <Button variant="outline" disabled={!dirty || busy} onClick={discard}>
            Discard changes
          </Button>
          {!linesValid && (
            <p className="text-xs text-error">
              Accepted amounts must be numbers (0 or more).
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function UnderwritingPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data, isLoading } = useUnderwritingQueue(policyYearId);
  const refresh = useRefreshUnderwriting(policyYearId ?? "");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected = useMemo(
    () => data?.items.find((r) => r.id === selectedId) ?? null,
    [data, selectedId],
  );

  const runRefresh = () => {
    refresh.mutate(undefined, {
      onSuccess: (r) =>
        toast.success(
          `Underwriting synced — ${r.opened} opened, ${r.updated} updated, ${r.removed} removed`,
        ),
      onError: (e) => toast.error(formatError(e)),
    });
  };

  return (
    <div className="space-y-5">
      {selected && policyYearId ? (
        <ReviewDetail
          // Key on the id ALONE. Keying on the payload remounts mid-save: each
          // line's mutation invalidates the queue, so saving line 1 would reset
          // the form and wipe the still-unsaved edits on lines 2+.
          key={selected.id}
          review={selected}
          policyYearId={policyYearId}
          onBack={() => setSelectedId(null)}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="grid grid-cols-3 gap-3">
              <StatTile label="Cases" value={data?.total ?? 0} />
              <StatTile label="Open" value={data?.open ?? 0} />
              <StatTile
                label="Pending U/W"
                value={fmtCurrency(data?.pending_amount ?? 0)}
              />
            </div>
            <Button
              variant="outline"
              onClick={runRefresh}
              disabled={refresh.isPending || !policyYearId}
            >
              {refresh.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <RefreshCw className="size-4" />
              )}
              Sync with coverage
            </Button>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldQuestion className="size-4 text-muted-foreground" />
                Underwriting queue
              </CardTitle>
              <CardDescription>
                One case per member per insurer. Select a case to record
                requirements, workflow status and decisions.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <SkeletonTable rows={4} />
              ) : !data?.items.length ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No underwriting cases. Set a free cover limit or NEL age on a
                  product, then “Sync with coverage”.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Member</TableHead>
                      <TableHead>Insurer</TableHead>
                      <TableHead>Products</TableHead>
                      <TableHead className="text-right">Requested</TableHead>
                      <TableHead className="text-right">Pending U/W</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((review) => {
                      const pending = review.cases.reduce(
                        (sum, c) => sum + c.pending_si,
                        0,
                      );
                      const requested = review.cases.reduce(
                        (sum, c) => sum + c.requested_si,
                        0,
                      );
                      return (
                        <TableRow
                          key={review.id}
                          className="cursor-pointer"
                          tabIndex={0}
                          role="button"
                          aria-label={`Open underwriting case for ${review.subject_name ?? "member"}`}
                          onClick={() => setSelectedId(review.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setSelectedId(review.id);
                            }
                          }}
                        >
                          <TableCell>
                            <div className="font-medium text-foreground">
                              {review.subject_name || "—"}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              {review.staff_id}
                              {review.subject_type === "dependant" &&
                                ` · ${review.relationship}`}
                            </div>
                          </TableCell>
                          <TableCell>{review.insurer || "—"}</TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            {review.cases.map((c) => c.product_code).join(", ")}
                          </TableCell>
                          <TableCell className="text-right">
                            {fmtCurrency(requested)}
                          </TableCell>
                          <TableCell className="text-right">
                            {pending > 0 ? (
                              <span className="text-amber-500">
                                {fmtCurrency(pending)}
                              </span>
                            ) : (
                              "—"
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                OPEN_STATUSES.has(review.status)
                                  ? "default"
                                  : "outline"
                              }
                            >
                              {REVIEW_STATUS_LABELS[review.status]}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}

      <PageGuide
        purpose="Members (and covered dependants) above a product's Non-Evidence Limit — the free cover limit, or the NEL age — need the insurer's medical underwriting. One case per member per insurer tracks the requirements, workflow status and the per-product decisions. Insurer listings report the excess as Pending U/W until decided."
        connections={[
          {
            label: "Non-Evidence Limits",
            description:
              "FCL + NEL age per product on the Configuration page (auto-filled from the placement slip).",
          },
          {
            label: "Enrollment & roster",
            description:
              "Cases open automatically when matching, a roster change or a confirmed election pushes someone over a limit.",
          },
          {
            label: "Reports",
            description:
              "Last Accepted / Pending U/W columns on the insurer employee and dependant listings read these cases.",
          },
        ]}
      />
    </div>
  );
}
