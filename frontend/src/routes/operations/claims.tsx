import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Download, Loader2, Plus, RefreshCw, Tag } from "lucide-react";
import {
  DOC_TYPE_LABELS,
  RECEIVED_VIA_LABELS,
  downloadClaimDocument,
  useBrokerClaimDetail,
  useBrokerClaims,
  useDecideClaim,
  useRerunReview,
  useSetCaseType,
  type BrokerClaim,
  type CaseType,
} from "@/api/claims";
import { ConflictDetailError } from "@/api/client";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ClaimGracePeriodField } from "@/components/claims/ClaimGracePeriodField";
import { ClaimMessages } from "@/components/claims/ClaimMessages";
import { ClaimReviewPanel } from "@/components/claims/ClaimReviewPanel";
import { LogCaseForm } from "@/components/claims/LogCaseForm";
import { NativeSelect } from "@/components/ui/native-select";
import { DocTypeSettings } from "@/components/claims/DocTypeSettings";
import { ReviewRuleSettings } from "@/components/claims/review-rules/ReviewRuleSettings";
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
// components/portal/leaf/Strike).
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

// The case-type axis is coarser than the status rail and changes what the queue
// is ABOUT, so it reads as a select rather than a second chip rail — the status
// rail is already eight chips wide and scrolls on a narrow viewport.
const CASE_TYPE_FILTERS: { value: CaseType | ""; label: string }[] = [
  { value: "", label: "All cases" },
  { value: "claim", label: "Claims" },
  { value: "log", label: "LOG cases" },
];

function VerdictBadge({ claim }: { claim: BrokerClaim }) {
  const r = claim.ai_review;
  if (!r) return <span className="text-xs text-subtle">—</span>;
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
// Mirrors `models/claim.RELABELLABLE_STATUSES`: a case is reclassifiable for
// exactly as long as it is decidable. The server is the authority — this only
// decides whether the control is offered.
const RELABELLABLE = DECIDABLE;
// ai_review_pending is rerunnable (self-transition) so stuck reviews can be
// re-queued from the sheet.
const RERUNNABLE = new Set([
  "submitted",
  "ai_review_pending",
  "ai_verified",
  "ai_flagged",
]);

type DecisionAction = "approve" | "reject" | "needs_info";

/** One label/value pair in the claim detail sheet. The sheet used to run a 10px
 * tracked eyebrow for fields against a 12px one for sections — one tier now,
 * shared with every other panel in the app via `SectionLabel`. */
function DetailField({
  label,
  wide,
  children,
}: {
  label: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <SectionLabel as="dt">{label}</SectionLabel>
      <dd className="mt-0.5 text-sm text-foreground">{children}</dd>
    </div>
  );
}

/** A titled block of the sheet. Its heading shares the one label tier, so the
 * separation is carried by the rule and the space above it, not by a second
 * near-identical uppercase size. */
function DetailSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 border-t border-border pt-5">
      <SectionLabel as="h3">{title}</SectionLabel>
      {children}
    </section>
  );
}

function QueueTab({ initialClaimId }: { initialClaimId?: string }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const [status, setStatus] = useState<string>("");
  const [caseType, setCaseType] = useState<CaseType | "">("");
  const [page, setPage] = useState(0);
  // Deep link (`?claim=`) from the employee-level LOG card. Read once as the
  // initial value: syncing selection back into the URL on every row click would
  // churn history for no benefit.
  const [selectedId, setSelectedId] = useState<string | null>(
    initialClaimId ?? null,
  );
  const [decision, setDecision] = useState<DecisionAction | null>(null);
  const [note, setNote] = useState("");
  const [approvedAmount, setApprovedAmount] = useState("");
  // Set from a 409 limit_exceeded — the dialog re-arms as "Approve anyway".
  const [limitWarning, setLimitWarning] = useState<string | null>(null);
  const [logFormOpen, setLogFormOpen] = useState(false);
  // Target of the reclassify dialog; null = closed.
  const [relabelTo, setRelabelTo] = useState<CaseType | null>(null);
  const [relabelReason, setRelabelReason] = useState("");

  const decide = useDecideClaim();
  const rerun = useRerunReview();
  const setCaseTypeMutation = useSetCaseType();
  const { data, isLoading } = useBrokerClaims(
    policyYearId ?? undefined,
    status,
    page * PAGE_SIZE,
    PAGE_SIZE,
    caseType,
  );

  // Detail fetch rides alongside the list item for the fields the list omits
  // (the remaining benefit limit for this claim's bucket).
  const detail = useBrokerClaimDetail(selectedId);
  const remainingLimit = detail.data?.remaining_limit ?? null;

  // Prefer the list row (already rendered, no wait), but FALL BACK to the
  // detail fetch: a deep-linked case — or one that just left the filtered list
  // because it was decided or reclassified — is not on the current page, and
  // without the fallback the sheet would open over nothing.
  const selected = useMemo(() => {
    const fromList = data?.items.find((c) => c.id === selectedId) ?? null;
    if (fromList) return fromList;
    return detail.data?.id === selectedId ? detail.data : null;
  }, [data, selectedId, detail.data]);

  useEffect(() => {
    setPage(0);
  }, [status, caseType]);

  useEffect(() => {
    setNote("");
    setApprovedAmount("");
    setLimitWarning(null);
  }, [selectedId, decision]);

  useEffect(() => {
    setRelabelReason("");
  }, [relabelTo, selectedId]);

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
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-4">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
            <div className="min-w-0 space-y-1">
              <CardTitle>Claims review queue</CardTitle>
              <CardDescription>
                {total.toLocaleString()} case{total === 1 ? "" : "s"}
                {caseType
                  ? ` · ${CASE_TYPE_FILTERS.find((f) => f.value === caseType)?.label}`
                  : ""}
                {status ? ` · ${STATUS_FILTERS.find((f) => f.value === status)?.label}` : ""}
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <NativeSelect
                aria-label="Case type"
                className="h-8"
                value={caseType}
                onChange={(e) => setCaseType(e.target.value as CaseType | "")}
              >
                {CASE_TYPE_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </NativeSelect>
              {/* Eight filters overflow a narrow viewport — scroll the rail
                  rather than reflowing it into a second ragged line. */}
              <div className="-mx-1 max-w-full overflow-x-auto px-1 py-0.5">
                <Segmented
                  value={status}
                  onChange={setStatus}
                  options={STATUS_FILTERS.map((f) => ({ value: f.value, label: f.label }))}
                />
              </div>
              <Button size="sm" onClick={() => setLogFormOpen(true)}>
                <Plus className="size-4" />
                New LOG case
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SkeletonTable rows={6} columns={7} />
          ) : total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border rounded-md">
              {caseType === "log"
                ? "No LOG cases recorded yet. Use “New LOG case” when a request arrives by email."
                : `No claims${status ? " with this status" : " submitted yet"}.`}
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
                        {/* A member waiting on a reply is a reason to open this
                            row, so it has to be visible in the queue rather
                            than only inside the sheet. */}
                        {c.unread_member_messages > 0 && (
                          <Badge variant="warn" className="ml-2 align-middle">
                            {c.unread_member_messages} new
                          </Badge>
                        )}
                        <div className="text-2xs text-muted-foreground font-normal">
                          {c.staff_id}
                        </div>
                      </TableCell>
                      <TableCell>
                        <span className="inline-flex flex-wrap items-center gap-1.5">
                          {c.claim_type}
                          {/* Visible in the default "All cases" view, so the
                              category reads without switching the filter. */}
                          {c.case_type === "log" && (
                            <Badge variant="info">LOG</Badge>
                          )}
                        </span>
                        <div className="text-2xs text-muted-foreground">
                          {c.claim_kind === "flex"
                            ? `Flex · ${c.flex_category_name}`
                            : c.product_code}
                        </div>
                      </TableCell>
                      {/* Money and dates are single values: wrapping them mid-
                          figure ("SGD / 165.83") made the column unscannable.
                          tabular-nums keeps the digits column-aligned. */}
                      <TableCell className="whitespace-nowrap tabular-nums">
                        {c.currency} {c.amount_claimed.toFixed(2)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums text-muted-foreground">
                        {fmtDate(c.incurred_date)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums text-muted-foreground">
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
              {/* pr-10 keeps the title clear of the overlaid close control. */}
              <SheetHeader className="gap-3 pr-10">
                <SheetTitle>{selected.claim_type}</SheetTitle>
                {/* Status, verdict and the re-run action identify the claim, so
                    they belong beside its name rather than as the first item of
                    the scrolling body. */}
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={selected.status} />
                  {selected.case_type === "log" && <Badge variant="info">LOG</Badge>}
                  <VerdictBadge claim={selected} />
                  {/* The pipeline compares a claim form against its documents,
                      so a case with none can only fail and bounce back to
                      manual review. Offering the control there spends an AI
                      call to produce "review failed". */}
                  {RERUNNABLE.has(selected.status) &&
                    selected.documents.length > 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="ml-auto"
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
              </SheetHeader>
              <SheetBody className="space-y-5">
                <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                  <DetailField label="Amount claimed">
                    <span className="font-medium tabular-nums">
                      {selected.currency} {selected.amount_claimed.toFixed(2)}
                    </span>
                  </DetailField>
                  {remainingLimit != null && (
                    <DetailField label="Remaining limit">
                      <span
                        className={
                          selected.amount_claimed > remainingLimit
                            ? "font-medium tabular-nums text-warn"
                            : "tabular-nums"
                        }
                      >
                        {selected.currency} {remainingLimit.toFixed(2)}
                      </span>
                    </DetailField>
                  )}
                  <DetailField label="Member">
                    {selected.employee_name ?? "—"}{" "}
                    <span className="text-muted-foreground">
                      ({selected.staff_id})
                    </span>
                  </DetailField>
                  {selected.dependant_name && (
                    <DetailField label="Claimant">
                      {selected.dependant_name}{" "}
                      <span className="text-muted-foreground">(dependant)</span>
                    </DetailField>
                  )}
                  <DetailField label="Coverage">
                    {selected.claim_kind === "flex"
                      ? `Flex · ${selected.flex_category_name}`
                      : `${selected.product_code}${
                          selected.sub_type
                            ? ` · ${selected.sub_type}`
                            : selected.benefit_key
                              ? ` · ${selected.benefit_key}`
                              : ""
                        }`}
                  </DetailField>
                  <DetailField label="Incurred">
                    <span className="tabular-nums">
                      {fmtDate(selected.incurred_date)}
                    </span>
                  </DetailField>
                  <DetailField label="Provider">
                    {selected.provider_name ?? "—"}
                  </DetailField>
                  {/* Pre-/post-hospitalisation consults carry the treating
                      doctor — the link back to the admission being claimed
                      against. Absent on every other claim type. */}
                  {selected.doctor_name && (
                    <DetailField label="Doctor seen">
                      {selected.doctor_name}
                    </DetailField>
                  )}
                  {selected.diagnosis && (
                    <DetailField label="Diagnosis / description" wide>
                      {selected.diagnosis}
                    </DetailField>
                  )}
                  {selected.claim_kind === "insured" &&
                    (selected.referral_document ||
                      selected.referral_not_applicable) && (
                      <DetailField label="Referral letter" wide>
                        {selected.referral_document ? (
                          <button
                            type="button"
                            className="rounded text-left underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
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
                      </DetailField>
                    )}
                  {selected.amount_approved != null && (
                    <DetailField label="Approved amount">
                      <span className="font-medium tabular-nums">
                        {selected.currency} {selected.amount_approved.toFixed(2)}
                      </span>
                    </DetailField>
                  )}
                  {selected.decision_notes && (
                    <DetailField label="Decision note" wide>
                      {selected.decision_notes}
                    </DetailField>
                  )}
                </dl>

                <DetailSection title="Documents">
                  {selected.documents.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No documents.</p>
                  ) : (
                    <ul className="space-y-2">
                      {selected.documents.map((d) => (
                        <li
                          key={d.id}
                          className="flex items-center justify-between gap-3 rounded-md border border-border bg-card py-2 pl-3 pr-2 text-sm"
                        >
                          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                            <span className="truncate font-medium">
                              {d.file_name}
                            </span>
                            {d.doc_type && DOC_TYPE_LABELS[d.doc_type] && (
                              <Badge variant="outline">
                                {DOC_TYPE_LABELS[d.doc_type]}
                              </Badge>
                            )}
                            <span className="text-xs tabular-nums text-muted-foreground">
                              {(d.size_bytes / 1024).toFixed(0)} KB
                            </span>
                          </span>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="shrink-0"
                            aria-label={`Download ${d.file_name}`}
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
                </DetailSection>

                {/* Above the AI review, deliberately: a member waiting on an
                    answer is a person, and burying their question under the
                    fraud evidence is how it goes unanswered for a week. */}
                <DetailSection title="Messages">
                  <ClaimMessages claimId={selected.id} />
                </DetailSection>

                <DetailSection title="AI review">
                  <ClaimReviewPanel
                    claimId={selected.id}
                    claimStatus={selected.status}
                  />
                </DetailSection>

                {/* Classification sits last, and the control inside it is
                    secondary: reclassifying is a rare correction, and a rare
                    correction that looks like a primary action gets pressed by
                    accident. It keeps company with the provenance it explains. */}
                <DetailSection title="Classification">
                  <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                    <DetailField label="Case type">
                      {selected.case_type === "log" ? "LOG case" : "Claim"}
                      <span className="text-muted-foreground">
                        {selected.origin === "broker"
                          ? " · recorded here"
                          : " · submitted by the member"}
                      </span>
                    </DetailField>
                    {selected.received_via && (
                      <DetailField label="Received via">
                        {RECEIVED_VIA_LABELS[selected.received_via] ??
                          selected.received_via}
                        {selected.received_on
                          ? ` · ${fmtDate(selected.received_on)}`
                          : ""}
                      </DetailField>
                    )}
                    {selected.requested_by && (
                      <DetailField label="Requested by" wide>
                        {selected.requested_by}
                      </DetailField>
                    )}
                  </dl>
                  {RELABELLABLE.has(selected.status) ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setRelabelTo(selected.case_type === "log" ? "claim" : "log")
                      }
                    >
                      <Tag className="size-4" />
                      {selected.case_type === "log"
                        ? "Change to an ordinary claim"
                        : "Change to a LOG case"}
                    </Button>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      The outcome is recorded, so the case type is fixed.
                    </p>
                  )}
                </DetailSection>
              </SheetBody>

              {/* Pinned: deciding is why this sheet is open, but the buttons sat
                  under the full AI review — every decision meant scrolling past
                  the evidence to reach them. */}
              {DECIDABLE.has(selected.status) && (
                <SheetFooter className="justify-start">
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
                </SheetFooter>
              )}
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
        // Only rejection is destructive; a red warning triangle over "Approve
        // this claim?" argues against the action it is confirming.
        tone={decision === "reject" ? "danger" : "info"}
        description={
          <div className="space-y-4">
            {limitWarning && (
              <p className="rounded-md border border-warn/40 bg-warn-soft px-3 py-2.5 text-warn">
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
                <label className="block space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">
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
                    className="tabular-nums"
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
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-muted-foreground">
                Note {decision === "needs_info" ? "(shown to the member)" : "(optional)"}
              </span>
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={
                  decision === "needs_info" ? "e.g. Send the itemized bill" : "Optional note"
                }
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

      <LogCaseForm
        open={logFormOpen}
        onOpenChange={setLogFormOpen}
        onCreated={(claimId) => {
          // Show it where it landed rather than leaving the assessor to find
          // it: the new case is a LOG case, and the queue may be filtered to
          // claims only.
          setCaseType("log");
          setStatus("");
          setPage(0);
          setSelectedId(claimId);
        }}
      />

      <AlertDialog
        open={relabelTo !== null}
        onOpenChange={(o) => {
          if (!o) setRelabelTo(null);
        }}
        title={
          relabelTo === "log"
            ? "Record this as a LOG case?"
            : "Change this back to an ordinary claim?"
        }
        tone="info"
        description={
          <div className="space-y-4">
            <p>
              It keeps its status, documents, messages and amounts — only the
              category changes.
              {selected?.origin === "portal"
                ? " The member submitted this one, so it stays visible to them either way."
                : ""}
            </p>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-muted-foreground">
                Reason (recorded on the case)
              </span>
              <Input
                value={relabelReason}
                onChange={(e) => setRelabelReason(e.target.value)}
                placeholder={
                  relabelTo === "log"
                    ? "e.g. Hospital sent this as a LOG request by email"
                    : "e.g. Logged in error — it's an ordinary reimbursement"
                }
              />
            </label>
          </div>
        }
        confirmLabel={relabelTo === "log" ? "Record as LOG case" : "Change to claim"}
        loading={setCaseTypeMutation.isPending}
        onConfirm={async () => {
          if (!selected || !relabelTo) return;
          const reason = relabelReason.trim();
          if (!reason) {
            toast.error("Give a reason so the record says why");
            return;
          }
          try {
            await setCaseTypeMutation.mutateAsync({
              claimId: selected.id,
              caseType: relabelTo,
              reason,
            });
            toast.success(
              relabelTo === "log" ? "Recorded as a LOG case" : "Changed to a claim",
            );
            setRelabelTo(null);
            // With a case-type filter active the case leaves the filtered list,
            // which would strand the sheet open over nothing.
            if (caseType && caseType !== relabelTo) setSelectedId(null);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
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

// The Claims page: the review queue plus everything that governs it — the
// per-claim-type AI review rule setup (AI extraction) and the company claim
// settings (grace period + document vocabulary, moved here from Company
// settings so the whole claims surface lives in one place).
const CLAIMS_TABS = ["queue", "ai-extraction", "settings"] as const;
type ClaimsTab = (typeof CLAIMS_TABS)[number];
const isClaimsTab = (v: string | undefined): v is ClaimsTab =>
  CLAIMS_TABS.includes(v as ClaimsTab);

export function ClaimsQueuePage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string; claim?: string };
  const tab: ClaimsTab = isClaimsTab(search.tab) ? search.tab : "queue";

  return (
    <Tabs
      value={tab}
      onValueChange={(v) =>
        navigate({ to: "/claims/review", search: { tab: v } })
      }
    >
      <TabsList>
        <TabsTrigger value="queue">Queue</TabsTrigger>
        <TabsTrigger value="ai-extraction">AI extraction</TabsTrigger>
        <TabsTrigger value="settings">Settings</TabsTrigger>
      </TabsList>

      <TabsContent value="queue">
        <QueueTab initialClaimId={search.claim} />
      </TabsContent>

      <TabsContent value="ai-extraction">
        <ReviewRuleSettings />
      </TabsContent>

      <TabsContent value="settings" className="space-y-5">
        <Card>
          <CardHeader className="pb-4">
            <CardTitle>Claim submission</CardTitle>
            <CardDescription className="max-w-prose">
              Governs when members may submit claims for the current benefit
              year.
            </CardDescription>
          </CardHeader>
          <CardContent className="pb-6">
            <ClaimGracePeriodField />
          </CardContent>
        </Card>
        <DocTypeSettings />
      </TabsContent>
    </Tabs>
  );
}
