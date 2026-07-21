import { useEffect, useMemo, useState } from "react";
import { Download, Loader2, RefreshCw } from "lucide-react";
import {
  downloadClaimDocument,
  useBrokerClaimDetail,
  useBrokerClaims,
  useDecideClaim,
  useRerunReview,
  type BrokerClaim,
} from "@/api/claims";
import { usePolicyYears, useUpdatePolicyYear } from "@/api/hooks";
import { ConflictDetailError } from "@/api/client";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PaginationControls } from "@/components/ui/pagination-controls";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { SkeletonTable } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ClaimReviewPanel } from "@/components/claims/ClaimReviewPanel";
import { InfoHint } from "@/components/ui/tooltip";
import { PageGuide } from "@/components/ui/page-guide";
import { formatError } from "@/lib/errors";
import { fmtDate } from "@/lib/format";
import { toast } from "sonner";

const PAGE_SIZE = 50;

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "ai_flagged", label: "Flagged" },
  { value: "ai_verified", label: "Verified" },
  { value: "submitted", label: "Manual" },
  { value: "ai_review_pending", label: "Running" },
  { value: "needs_info", label: "Needs info" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
] as const;

// Brokers see the real machine statuses (members get the sanitized labels in
// components/portal/ClaimStatusBadge).
const BROKER_STATUS: Record<
  string,
  { label: string; variant: "good" | "warn" | "error" | "outline" | "info" | "default" }
> = {
  draft: { label: "Draft", variant: "outline" },
  submitted: { label: "Manual review", variant: "info" },
  ai_review_pending: { label: "AI running", variant: "default" },
  ai_verified: { label: "AI verified", variant: "good" },
  ai_flagged: { label: "AI flagged", variant: "error" },
  needs_info: { label: "Needs info", variant: "warn" },
  approved: { label: "Approved", variant: "good" },
  rejected: { label: "Rejected", variant: "error" },
};

function StatusBadge({ status }: { status: string }) {
  const cfg = BROKER_STATUS[status] ?? { label: status, variant: "outline" as const };
  return <Badge variant={cfg.variant}>{cfg.label}</Badge>;
}

function VerdictBadge({ claim }: { claim: BrokerClaim }) {
  const r = claim.ai_review;
  if (!r) return <span className="text-muted-foreground/60 text-xs">—</span>;
  if (r.status === "pending") {
    return <Badge variant="outline">running…</Badge>;
  }
  if (r.status === "error") {
    return <Badge variant="warn">review failed</Badge>;
  }
  return r.verdict === "clean" ? (
    <Badge variant="good">clean</Badge>
  ) : (
    <Badge variant="error">flagged</Badge>
  );
}

const DECIDABLE = new Set([
  "submitted",
  "ai_review_pending",
  "ai_verified",
  "ai_flagged",
  "needs_info",
]);
// ai_review_pending is rerunnable (self-transition) so stuck reviews can be
// re-queued from the sheet.
const RERUNNABLE = new Set([
  "submitted",
  "ai_review_pending",
  "ai_verified",
  "ai_flagged",
]);

type DecisionAction = "approve" | "reject" | "needs_info";

// Claim-submission grace period, bound to the current benefit year — the year
// claims submit against. Edit buffer is committed on blur; blank clears the
// deadline. (Lives here rather than on the Configuration page since it governs
// claims behaviour.)
function ClaimGracePeriodField() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const update = useUpdatePolicyYear();
  const year = years.find((y) => y.id === policyYearId) ?? null;
  const [draft, setDraft] = useState<string | null>(null);

  if (!year) return null;

  const commit = async () => {
    if (draft === null) return;
    const trimmed = draft.trim();
    // Number() (not parseInt) so "30.5"/"30x" are rejected rather than silently
    // truncated to 30. Keep the draft on a validation error so the typed value
    // isn't lost — the broker can correct it.
    const next = trimmed === "" ? null : Number(trimmed);
    if (next !== null && (!Number.isInteger(next) || next < 0)) {
      toast.error("Grace period must be a whole number of days (or blank).");
      return;
    }
    if (next === year.claim_grace_period_days) {
      setDraft(null);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: year.id,
        payload: { claim_grace_period_days: next },
      });
      toast.success("Claim grace period updated");
      // Only drop the buffer on success (reflects the server value); on failure
      // keep it so the broker can retry without retyping.
      setDraft(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <div className="flex flex-col gap-1.5 sm:max-w-md">
      <div className="flex items-center gap-1">
        <Label htmlFor="claim-grace">Claim submission grace period (days)</Label>
        <InfoHint>
          Days after the current benefit year's coverage period ends during
          which members may still submit claims. Leave blank for no submission
          deadline.
        </InfoHint>
      </div>
      <Input
        id="claim-grace"
        type="number"
        min={0}
        placeholder="No deadline"
        className="h-9 w-40"
        value={draft ?? (year.claim_grace_period_days?.toString() ?? "")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    </div>
  );
}

export function ClaimsQueuePage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const [status, setStatus] = useState<string>("");
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [decision, setDecision] = useState<DecisionAction | null>(null);
  const [note, setNote] = useState("");
  const [approvedAmount, setApprovedAmount] = useState("");
  // Set from a 409 limit_exceeded — the dialog re-arms as "Approve anyway".
  const [limitWarning, setLimitWarning] = useState<string | null>(null);

  const decide = useDecideClaim();
  const rerun = useRerunReview();
  const { data, isLoading } = useBrokerClaims(
    policyYearId ?? undefined,
    status,
    page * PAGE_SIZE,
    PAGE_SIZE,
  );

  const selected = useMemo(
    () => data?.items.find((c) => c.id === selectedId) ?? null,
    [data, selectedId],
  );
  // Detail fetch rides alongside the list item for the fields the list omits
  // (the remaining benefit limit for this claim's bucket).
  const detail = useBrokerClaimDetail(selectedId);
  const remainingLimit = detail.data?.remaining_limit ?? null;

  useEffect(() => {
    setPage(0);
  }, [status]);

  useEffect(() => {
    setNote("");
    setApprovedAmount("");
    setLimitWarning(null);
  }, [selectedId, decision]);

  if (!policyYearId) return null;
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);

  const confirmDecision = async () => {
    if (!selected || !decision) return;
    try {
      const amount = approvedAmount.trim() ? Number(approvedAmount) : undefined;
      if (decision === "approve" && amount !== undefined && (!isFinite(amount) || amount <= 0)) {
        toast.error("Approved amount must be a positive number");
        return;
      }
      await decide.mutateAsync({
        claimId: selected.id,
        action: decision,
        note: note.trim() || undefined,
        approvedAmount: decision === "approve" ? amount : undefined,
        acknowledge: limitWarning !== null,
      });
      toast.success(
        decision === "approve"
          ? "Claim approved"
          : decision === "reject"
            ? "Claim rejected"
            : "Returned to the member for more information",
      );
      setDecision(null);
      // With a status filter active the decided claim leaves the filtered
      // list, which would strand the sheet open over nothing — close it.
      if (status) setSelectedId(null);
    } catch (err) {
      if (
        err instanceof ConflictDetailError &&
        err.detail.code === "limit_exceeded"
      ) {
        // Keep the dialog open; confirming again acknowledges the overrun.
        setLimitWarning(err.message);
        return;
      }
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-4 max-w-7xl">
      <Card>
        <CardHeader className="space-y-4">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <CardTitle>Claims review queue</CardTitle>
              <CardDescription>
                {total.toLocaleString()} claim{total === 1 ? "" : "s"}
                {status ? ` · ${STATUS_FILTERS.find((f) => f.value === status)?.label}` : ""}
              </CardDescription>
            </div>
            <Segmented
              value={status}
              onChange={setStatus}
              options={STATUS_FILTERS.map((f) => ({ value: f.value, label: f.label }))}
            />
          </div>
          <div className="border-t border-border pt-4">
            <ClaimGracePeriodField />
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SkeletonTable rows={6} columns={7} />
          ) : total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border rounded-md">
              No claims{status ? " with this status" : " submitted yet"}.
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Member</TableHead>
                    <TableHead>Claim</TableHead>
                    <TableHead>Amount</TableHead>
                    <TableHead>Incurred</TableHead>
                    <TableHead>Submitted</TableHead>
                    <TableHead>
                      <span className="inline-flex items-center gap-1">
                        Status
                        <InfoHint>
                          Manual review = no AI verdict yet; AI running = pipeline
                          in progress; AI verified / AI flagged = pipeline done;
                          Needs info = returned to the member; then Approved or
                          Rejected once you decide.
                        </InfoHint>
                      </span>
                    </TableHead>
                    <TableHead>
                      <span className="inline-flex items-center gap-1">
                        AI verdict
                        <InfoHint>
                          The pipeline's recommendation: clean (no discrepancies)
                          or flagged (a check needs your attention). Advisory — you
                          always make the final call.
                        </InfoHint>
                      </span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.items.map((c) => (
                    <TableRow
                      key={c.id}
                      className="cursor-pointer"
                      onClick={() => setSelectedId(c.id)}
                    >
                      <TableCell className="font-medium">
                        {c.employee_name ?? "—"}
                        <div className="text-[11px] text-muted-foreground font-normal">
                          {c.staff_id}
                        </div>
                      </TableCell>
                      <TableCell>
                        {c.claim_type}
                        <div className="text-[11px] text-muted-foreground">
                          {c.claim_kind === "flex"
                            ? `Flex · ${c.flex_category_name}`
                            : c.product_code}
                        </div>
                      </TableCell>
                      <TableCell>
                        {c.currency} {c.amount_claimed.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {fmtDate(c.incurred_date)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {fmtDate(c.submitted_at)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={c.status} />
                      </TableCell>
                      <TableCell>
                        <VerdictBadge claim={c} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              </div>
              <PaginationControls page={page} pages={pages} onPageChange={setPage} />
            </>
          )}
        </CardContent>
      </Card>

      <Sheet
        open={!!selectedId}
        onOpenChange={(o) => {
          if (!o) setSelectedId(null);
        }}
      >
        <SheetContent className="sm:max-w-2xl">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>
                  {selected.claim_type} · {selected.currency}{" "}
                  {selected.amount_claimed.toFixed(2)}
                </SheetTitle>
              </SheetHeader>
              <SheetBody className="space-y-4">
                <div className="flex items-center gap-2 flex-wrap">
                  <StatusBadge status={selected.status} />
                  <VerdictBadge claim={selected} />
                  {RERUNNABLE.has(selected.status) && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={rerun.isPending}
                      onClick={async () => {
                        try {
                          await rerun.mutateAsync(selected.id);
                          toast.success("AI review re-queued");
                        } catch (err) {
                          toast.error(formatError(err));
                        }
                      }}
                    >
                      {rerun.isPending ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <RefreshCw className="size-4" />
                      )}
                      Re-run AI review
                    </Button>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Member
                    </div>
                    {selected.employee_name ?? "—"}{" "}
                    <span className="text-muted-foreground">({selected.staff_id})</span>
                  </div>
                  {selected.dependant_name && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Claimant
                      </div>
                      {selected.dependant_name}{" "}
                      <span className="text-muted-foreground">(dependant)</span>
                    </div>
                  )}
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Coverage
                    </div>
                    {selected.claim_kind === "flex"
                      ? `Flex · ${selected.flex_category_name}`
                      : `${selected.product_code}${
                          selected.sub_type
                            ? ` · ${selected.sub_type}`
                            : selected.benefit_key
                              ? ` · ${selected.benefit_key}`
                              : ""
                        }`}
                  </div>
                  {remainingLimit != null && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Remaining limit
                      </div>
                      <span
                        className={
                          selected.amount_claimed > remainingLimit
                            ? "text-warn"
                            : undefined
                        }
                      >
                        {selected.currency} {remainingLimit.toFixed(2)}
                      </span>
                    </div>
                  )}
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Incurred
                    </div>
                    {fmtDate(selected.incurred_date)}
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Provider
                    </div>
                    {selected.provider_name ?? "—"}
                  </div>
                  {selected.diagnosis && (
                    <div className="col-span-2">
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Diagnosis / description
                      </div>
                      {selected.diagnosis}
                    </div>
                  )}
                  {selected.claim_kind === "insured" &&
                    (selected.referral_document ||
                      selected.referral_not_applicable) && (
                      <div className="col-span-2">
                        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          Referral letter
                        </div>
                        {selected.referral_document ? (
                          <button
                            type="button"
                            className="text-left underline-offset-2 hover:underline"
                            onClick={async () => {
                              try {
                                await downloadClaimDocument(
                                  selected.id,
                                  selected.referral_document!,
                                );
                              } catch (err) {
                                toast.error(formatError(err));
                              }
                            }}
                          >
                            {selected.referral_document.file_name}
                          </button>
                        ) : (
                          "Declared not applicable by the member"
                        )}
                      </div>
                    )}
                  {selected.decision_notes && (
                    <div className="col-span-2">
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Decision note
                      </div>
                      {selected.decision_notes}
                    </div>
                  )}
                  {selected.amount_approved != null && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Approved amount
                      </div>
                      {selected.currency} {selected.amount_approved.toFixed(2)}
                    </div>
                  )}
                </div>

                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                    Documents
                  </div>
                  {selected.documents.length === 0 ? (
                    <div className="text-sm text-muted-foreground">No documents.</div>
                  ) : (
                    <ul className="space-y-1.5">
                      {selected.documents.map((d) => (
                        <li
                          key={d.id}
                          className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm"
                        >
                          <span className="truncate">
                            {d.file_name}
                            <span className="text-[11px] text-muted-foreground ml-2">
                              {(d.size_bytes / 1024).toFixed(0)} KB
                            </span>
                          </span>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={async () => {
                              try {
                                await downloadClaimDocument(selected.id, d);
                              } catch (err) {
                                toast.error(formatError(err));
                              }
                            }}
                          >
                            <Download className="size-4" />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                    AI review
                  </div>
                  <ClaimReviewPanel
                    claimId={selected.id}
                    claimStatus={selected.status}
                  />
                </div>

                {DECIDABLE.has(selected.status) && (
                  <div className="flex gap-2 border-t border-border pt-4">
                    <Button onClick={() => setDecision("approve")}>Approve</Button>
                    <Button
                      variant="outline"
                      className="text-error hover:text-error"
                      onClick={() => setDecision("reject")}
                    >
                      Reject
                    </Button>
                    <Button variant="outline" onClick={() => setDecision("needs_info")}>
                      Request more info
                    </Button>
                  </div>
                )}
              </SheetBody>
            </>
          )}
        </SheetContent>
      </Sheet>

      <AlertDialog
        open={decision !== null}
        onOpenChange={(o) => {
          if (!o) setDecision(null);
        }}
        title={
          decision === "approve"
            ? "Approve this claim?"
            : decision === "reject"
              ? "Reject this claim?"
              : "Request more information?"
        }
        confirmVariant={decision === "reject" ? "destructive" : "default"}
        description={
          <div className="space-y-3">
            {limitWarning && (
              <p className="rounded-md border border-warn/40 bg-warn-soft px-2.5 py-2 text-warn">
                {limitWarning} Confirm again to approve anyway, or lower the
                amount.
              </p>
            )}
            {decision === "approve" && selected && (
              <>
                <p>
                  The member will see the claim as approved. Leave the amount blank to
                  approve the full {selected.currency}{" "}
                  {selected.amount_claimed.toFixed(2)}.
                </p>
                <label className="block">
                  <span className="text-xs text-muted-foreground">
                    Approved amount (optional)
                  </span>
                  <Input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={approvedAmount}
                    onChange={(e) => {
                      setApprovedAmount(e.target.value);
                      setLimitWarning(null); // new amount → re-check the limit
                    }}
                    placeholder={selected.amount_claimed.toFixed(2)}
                    className="mt-1 h-8"
                  />
                </label>
              </>
            )}
            {decision === "reject" && (
              <p>This is final — the member cannot resubmit a rejected claim.</p>
            )}
            {decision === "needs_info" && (
              <p>
                The claim reopens for the member to add documents and resubmit. Explain
                what's missing in the note.
              </p>
            )}
            <label className="block">
              <span className="text-xs text-muted-foreground">
                Note {decision === "needs_info" ? "(shown to the member)" : "(optional)"}
              </span>
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={
                  decision === "needs_info" ? "e.g. Send the itemized bill" : "Optional note"
                }
                className="mt-1 h-8"
              />
            </label>
          </div>
        }
        confirmLabel={
          decision === "approve"
            ? limitWarning
              ? "Approve anyway"
              : "Approve"
            : decision === "reject"
              ? "Reject claim"
              : "Send back"
        }
        loading={decide.isPending}
        onConfirm={confirmDecision}
      />

      <PageGuide
        purpose="Review member-submitted claims. Each submission runs through an AI pipeline (document extraction → field comparison → rule checks → selective vision verification) that orders this queue; the broker always makes the final decision."
        connections={[
          { label: "← Employee portal", description: "Members submit claims with receipts from /portal/claims" },
          { label: "← Benefit statement", description: "Claims validate against the member's resolved coverage" },
          { label: "→ AI Provider", description: "Extraction and review calls are budgeted and logged per client" },
        ]}
      />
    </div>
  );
}
