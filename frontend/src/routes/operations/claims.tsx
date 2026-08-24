import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Loader2, Plus, RefreshCw, Tag, X } from "lucide-react";
import {
  DOC_TYPE_LABELS,
  RECEIVED_VIA_LABELS,
  downloadClaimDocument,
  useBrokerClaimDetail,
  useBrokerClaims,
  useDecideClaim,
  useRecordClaimPayment,
  useSendToInsurer,
  useRefreshClaimConversion,
  useRerunReview,
  useSetCaseType,
  type BrokerClaim,
  type CaseType,
} from "@/api/claims";
import { ConflictDetailError } from "@/api/client";
import { useMe } from "@/api/hooks";
import { ConversionLine, policyAmount } from "@/components/claims/ConversionLine";
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
import {
  PageTabsBar,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ClaimGracePeriodField } from "@/components/claims/ClaimGracePeriodField";
import { LeaverAccessField } from "@/components/claims/LeaverAccessField";
import { ClaimMessages } from "@/components/claims/ClaimMessages";
import {
  ConversationQueue,
  useAwaitingReplyCount,
} from "@/components/claims/ConversationQueue";
import { ClaimReviewPanel } from "@/components/claims/ClaimReviewPanel";
import { ClaimAmendPanel } from "@/components/claims/ClaimAmendPanel";
import { ClaimAssessmentPanel } from "@/components/claims/ClaimAssessmentPanel";
import {
  ClaimSettlementFacts,
  hasSettlement,
} from "@/components/claims/ClaimSettlementFacts";
import { LogCaseForm } from "@/components/claims/LogCaseForm";
import { NativeSelect } from "@/components/ui/native-select";
import { ClaimDocumentSettings } from "@/components/claims/ClaimDocumentSettings";
import { ReviewRuleSettings } from "@/components/claims/review-rules/ReviewRuleSettings";
import { ImportRulesDialog } from "@/components/claims/review-rules/ImportRulesDialog";
import { InfoHint } from "@/components/ui/tooltip";
import { PageGuide } from "@/components/ui/page-guide";
import { formatError } from "@/lib/errors";
import { fmtDate } from "@/lib/format";
import { toast } from "sonner";

const PAGE_SIZE = 10;

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "ai_flagged", label: "Flagged" },
  { value: "needs_info", label: "Needs info" },
  { value: "approved", label: "Approved" },
  // The settlement leg. Without these two the filter rail would silently stop
  // at "Approved" while the claims themselves moved past it — visible only
  // under "All", which is the one filter nobody works a queue from.
  { value: "sent_to_insurer", label: "With insurer" },
  { value: "paid", label: "Paid" },
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
  // "Approved" and "Paid" are weeks apart and only one of them means the member
  // has their money — the badge has to tell them apart.
  sent_to_insurer: { label: "With insurer", variant: "info" },
  paid: { label: "Paid", variant: "good" },
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
  if (["queued", "running", "retry_wait"].includes(r.status)) {
    return <Badge variant="outline">running…</Badge>;
  }
  if (r.status === "error") {
    return <Badge variant="warn">review failed</Badge>;
  }
  if (r.status === "cancelled") {
    return <Badge variant="outline">cancelled</Badge>;
  }
  return r.verdict === "clean" ? (
    <Badge variant="good">clean</Badge>
  ) : (
    <Badge variant="error">flagged</Badge>
  );
}

function can(claim: BrokerClaim, action: string): boolean {
  return claim.allowed_actions.includes(action);
}
// Mirrors `models/claim.RELABELLABLE_STATUSES`: a case is reclassifiable for
// exactly as long as it is decidable. The server is the authority — this only
// decides whether the control is offered.
// The settlement leg. A claim we approved is not finished — it still has to go
// to the insurer and be paid. Each set is exactly the statuses the server
// accepts the corresponding transition from (`models/claim.VALID_TRANSITIONS`);
// the server is the authority, this only decides whether to offer the control.

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

function QueueTab({
  initialClaimId,
  employeeId,
}: {
  initialClaimId?: string;
  /** `?employee=` — one member's claims (the flex panel's pending link). Unlike
   *  `?claim=` this is NOT read once into state: it is a filter the queue is
   *  under for as long as the URL says so, and it has to be visible and
   *  clearable, because a queue silently showing a subset is worse than one
   *  showing everything. */
  employeeId?: string;
}) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: me } = useMe();
  const readOnly = me?.role === "broker_viewer";
  const navigate = useNavigate();
  const [status, setStatus] = useState<string>("");
  const [caseType, setCaseType] = useState<CaseType | "">("");
  const [searchText, setSearchText] = useState("");
  const search = useDeferredValue(searchText);
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
  // The policy-currency value of a foreign claim, keyed in when no reference
  // rate could be fetched. Distinct from `approvedAmount`: this is what the
  // BILL is worth, that is what we agree to PAY, and a partial approval must
  // not silently restate the claim's value.
  const [convertedAmount, setConvertedAmount] = useState("");
  // Set when approval exceeds either the claim value or remaining limit.
  const [limitWarning, setLimitWarning] = useState<string | null>(null);
  const [logFormOpen, setLogFormOpen] = useState(false);
  // Settlement dialog: "send" to the insurer, or "pay" from their advice.
  const [settling, setSettling] = useState<"send" | "pay" | null>(null);
  const [paidOn, setPaidOn] = useState("");
  const [paidAmount, setPaidAmount] = useState("");
  const [paymentWarning, setPaymentWarning] = useState<string | null>(null);
  // Target of the reclassify dialog; null = closed.
  const [relabelTo, setRelabelTo] = useState<CaseType | null>(null);
  const [relabelReason, setRelabelReason] = useState("");
  const previousPolicyYearId = useRef(policyYearId);
  useEffect(() => {
    if (previousPolicyYearId.current === policyYearId) return;
    previousPolicyYearId.current = policyYearId;
    setSelectedId(null);
    setDecision(null);
    setLogFormOpen(false);
    setSettling(null);
    setRelabelTo(null);
    setPage(0);
  }, [policyYearId]);

  const qc = useQueryClient();
  const decide = useDecideClaim();
  const sendToInsurer = useSendToInsurer();
  const recordPayment = useRecordClaimPayment();
  const rerun = useRerunReview();
  const refreshFx = useRefreshClaimConversion();
  const setCaseTypeMutation = useSetCaseType();
  const { data, isLoading, isError, error, refetch } = useBrokerClaims(
    policyYearId ?? undefined,
    status,
    page * PAGE_SIZE,
    PAGE_SIZE,
    caseType,
    employeeId,
    search,
  );
  // The filtered-to member's name, taken from the rows themselves rather than
  // fetched: every row in a filtered response is theirs, so the first one names
  // them, and a member with no claims left needs no chip.
  const filteredTo = employeeId ? data?.items[0]?.employee_name : null;

  // Detail fetch rides alongside the list item for the fields the list omits
  // (the remaining benefit limit for this claim's bucket).
  const detail = useBrokerClaimDetail(selectedId);
  const remainingLimit = detail.data?.remaining_limit ?? null;

  // Prefer the list row (already rendered, no wait), but FALL BACK to the
  // detail fetch: a deep-linked case — or one that just left the filtered list
  // because it was decided or reclassified — is not on the current page, and
  // without the fallback the sheet would open over nothing.
  const selected = useMemo(() => {
    if (detail.data?.id === selectedId) return detail.data;
    const fromList = data?.items.find((c) => c.id === selectedId) ?? null;
    if (fromList) return fromList;
    return null;
  }, [data, selectedId, detail.data]);

  // What the claim is worth in the currency every limit is stated in. NULL on a
  // foreign claim nobody has converted yet — and that is the point: there is no
  // number to compare, so the UI must ask for one rather than reach for the
  // foreign figure sitting next to it.
  const claimedInPolicyCurrency = selected ? policyAmount(selected) : null;
  const needsConversion = selected?.fx_state === "unavailable";

  useEffect(() => {
    setPage(0);
  }, [status, caseType, search]);

  useEffect(() => {
    setNote("");
    setApprovedAmount("");
    setConvertedAmount("");
    setLimitWarning(null);
  }, [selectedId, decision]);

  useEffect(() => {
    setRelabelReason("");
  }, [relabelTo, selectedId]);

  useEffect(() => {
    setPaidOn("");
    setPaidAmount("");
    setPaymentWarning(null);
  }, [settling, selectedId]);

  const confirmSettlement = async () => {
    if (!selected || !settling) return;
    try {
      if (settling === "send") {
        await sendToInsurer.mutateAsync({ claimId: selected.id });
        toast.success("Sent to the insurer");
      } else {
        if (!paidOn) {
          toast.error("Enter the payment date from the insurer's advice");
          return;
        }
        // Blank = what we approved. An explicit 0 is a real advice (fully
        // offset against an excess), so it must not be coerced to "unset".
        const raw = paidAmount.trim();
        const amount = raw === "" ? undefined : Number(raw);
        if (amount !== undefined && (!isFinite(amount) || amount < 0)) {
          toast.error("Paid amount must be zero or more");
          return;
        }
        await recordPayment.mutateAsync({
          claimId: selected.id,
          paidOn,
          amount,
          acknowledgeOverpayment: paymentWarning !== null,
        });
        toast.success("Payment recorded");
      }
      setSettling(null);
      // With a status filter active the claim leaves the filtered list, which
      // would strand the sheet open over nothing — same rule as a decision.
      if (status) setSelectedId(null);
    } catch (err) {
      if (
        err instanceof ConflictDetailError &&
        err.detail.code === "payment_exceeds_approval"
      ) {
        setPaymentWarning(err.message);
        return;
      }
      toast.error(formatError(err));
    }
  };

  if (!policyYearId) return null;
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);

  const confirmDecision = async () => {
    if (!selected || !decision) return;
    const noteRequired =
      decision === "needs_info" ||
      (decision === "reject" && selected.status === "sent_to_insurer");
    if (noteRequired && !note.trim()) {
      toast.error("Add the explanation the member needs before continuing");
      return;
    }
    try {
      const amount = approvedAmount.trim() ? Number(approvedAmount) : undefined;
      const converted = convertedAmount.trim()
        ? Number(convertedAmount)
        : undefined;
      if (decision === "approve" && amount !== undefined && (!isFinite(amount) || amount <= 0)) {
        toast.error("Approved amount must be a positive number");
        return;
      }
      // Caught here rather than left to the server's 422 (`fx_amount_required`)
      // so the assessor is told beside the field, not by a toast over a dialog
      // that has already lost what they typed.
      if (
        decision === "approve" &&
        needsConversion &&
        (converted === undefined || !isFinite(converted) || converted <= 0)
      ) {
        toast.error(
          `Enter what this claim is worth in ${selected.policy_currency} — ` +
            "no exchange rate could be fetched for it.",
        );
        return;
      }
      await decide.mutateAsync({
        claimId: selected.id,
        action: decision,
        note: note.trim() || undefined,
        approvedAmount: decision === "approve" ? amount : undefined,
        // Only ever sent when the claim actually lacks one — the server refuses
        // to overwrite a conversion that already exists, and sending it on an
        // already-priced claim would be asking for that 409.
        convertedAmount:
          decision === "approve" && needsConversion ? converted : undefined,
        acknowledge: limitWarning !== null,
        // The revision this sheet is showing. A member may correct their claim
        // right up to the decision, so deciding without it can approve a figure
        // that changed while the sheet was open.
        expectedRevision: selected.revision,
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
        ["limit_exceeded", "claim_amount_exceeded"].includes(err.detail.code)
      ) {
        // Keep the dialog open; confirming again acknowledges the overrun.
        setLimitWarning(err.message);
        return;
      }
      if (
        err instanceof ConflictDetailError &&
        err.detail.code === "claim_amended"
      ) {
        // The member corrected the claim while this sheet was open. CLOSE the
        // dialog and refetch rather than letting them confirm again — the whole
        // point is that they have not yet seen what they would be deciding on.
        //
        // The LIST is what has to be invalidated, not just the detail: `selected`
        // prefers the list row (see its memo), and that query has a 30s
        // staleTime — so refetching only the detail left the sheet showing the
        // pre-amendment figures and re-confirming sent the same stale
        // `expectedRevision` straight into another 409. Which is the failure
        // this branch exists to end, not to repeat.
        setDecision(null);
        void qc.invalidateQueries({ queryKey: ["claims"] });
        void qc.invalidateQueries({ queryKey: ["claim-detail"] });
        toast.error(err.message);
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
              {/* Named and clearable. The member filter arrives from another
                  page, so unlike the controls opposite there is nothing on
                  screen showing it is on — and "8 cases" on a 467-member client
                  reads as the whole queue being nearly empty. */}
              {employeeId && (
                <button
                  type="button"
                  onClick={() =>
                    void navigate({
                      to: "/claims/review",
                      search: { tab: "queue" },
                      replace: true,
                    })
                  }
                  className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-2xs text-muted-foreground hover:text-foreground focus-ring"
                >
                  {filteredTo ? `Only ${filteredTo}` : "One member only"}
                  <X className="size-3" aria-hidden />
                  <span className="sr-only">Show all members</span>
                </button>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                aria-label="Search claims"
                className="h-8 w-56"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                placeholder="Search ref, member, invoice"
              />
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
              {!readOnly && (
                <Button size="sm" onClick={() => setLogFormOpen(true)}>
                  <Plus className="size-4" />
                  New LOG case
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SkeletonTable rows={6} columns={7} />
          ) : isError ? (
            <div className="flex flex-col items-center gap-3 rounded-md border border-border p-8 text-center">
              <p className="text-sm text-error">
                Couldn&apos;t load the claims queue. {formatError(error)}
              </p>
              <Button variant="outline" size="sm" onClick={() => void refetch()}>
                <RefreshCw className="size-4" /> Retry
              </Button>
            </div>
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
                        {/* The MEMBER changed this claim. Its OWN signal, not a
                            reuse of the unread badge: that one counts
                            MEMBER-AUTHORED messages and would never fire for an
                            automatic amendment notice — so a claim that moved
                            under an assessor would otherwise look untouched
                            until they opened it.

                            Read off `amended_by`, NOT `amended_at`. Three
                            writers stamp that timestamp — the member's edit,
                            the member's document change, and the ASSESSOR'S OWN
                            correction — so gated on it this flagged an
                            assessor's own save straight back at them, which is
                            the one thing the badge must never mean.

                            Scoped to claims still awaiting a decision. Nothing
                            ever clears the stamp, so an unscoped chip is
                            permanent: every claim ever corrected would wear it
                            forever, including ones long since decided, and a
                            badge that never goes away stops meaning "look at
                            this" — which is the only thing it is for. */}
                        {c.amended_by === "member" && can(c, "approve") && (
                          <Badge variant="warn" className="ml-2 align-middle">
                            Amended
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
                          {/* The reference is what a caller quotes, so it has
                              to be findable by eye in the queue — not only
                              inside the sheet they'd have to open first. */}
                          {c.reference_no && (
                            <span className="font-mono"> · {c.reference_no}</span>
                          )}
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
                        {/* An overdue claim is the one thing in this queue that
                            needs chasing today, and the count changes every
                            night — which is why it is derived rather than
                            stored, and why it belongs here rather than only in
                            a spreadsheet somebody pulls weekly. */}
                        {c.days_over_deadline != null &&
                          c.days_over_deadline > 0 && (
                            <div className="mt-1 text-2xs font-medium tabular-nums text-warn">
                              {c.days_over_deadline}d over
                            </div>
                          )}
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
          {/* Keep an accessible title mounted even while the selected claim
              refetches after deep-link navigation. Radix validates the dialog
              immediately, before async detail data is guaranteed present. */}
          <SheetHeader className={selected ? "sr-only" : "gap-3 pr-10"}>
            <SheetTitle>{selected?.claim_type ?? "Claim details"}</SheetTitle>
          </SheetHeader>
          {!selected && detail.isLoading && (
            <div className="px-6 py-8 text-sm text-muted-foreground">
              Loading claim details…
            </div>
          )}
          {!selected && detail.isError && (
            <div className="flex flex-col items-start gap-3 px-6 py-8">
              <p className="text-sm text-error">
                Couldn&apos;t load this claim. {formatError(detail.error)}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void detail.refetch()}
              >
                <RefreshCw className="size-4" /> Retry
              </Button>
            </div>
          )}
          {selected && (
            <>
              {/* Extra right padding keeps the title clear of the overlaid close control. */}
              <div className="flex flex-col gap-3 px-6 pb-1 pt-6 pr-14">
                <h2 className="text-lg font-semibold tracking-tight text-foreground">
                  {selected.claim_type}
                </h2>
                {/* The reference is the string the member quotes on the phone
                    and the key a broker reconciles against the insurer's
                    ledger. It was minted at submit and rendered nowhere, which
                    left support with only a uuid to look a claim up by. It
                    identifies the claim, so it belongs beside its name. */}
                {selected.reference_no && (
                  <p className="-mt-1 font-mono text-xs tabular-nums text-muted-foreground">
                    {selected.reference_no}
                  </p>
                )}
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
                  {can(selected, "rerun_review") &&
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
              </div>
              <SheetBody className="space-y-5">
                <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                  <DetailField label="Amount claimed">
                    <span className="font-medium tabular-nums">
                      {selected.currency} {selected.amount_claimed.toFixed(2)}
                    </span>
                    {/* On a foreign claim the figure above is NOT the one that
                        spends the limit — the SGD equivalent is. Shown together
                        so an assessor never has to hold two currencies in their
                        head, and so an unresolved conversion is visible before
                        they reach for Approve. */}
                    <ConversionLine claim={selected} />
                    {/* The honest first move on an unpriced claim: the rate
                        service was very likely just briefly unreachable when
                        the member filed. One press beats typing a figure off a
                        bank statement, and it clears the server's outage
                        cooldown so "try now" means now. */}
                    {needsConversion && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="mt-1.5"
                        disabled={refreshFx.isPending}
                        onClick={async () => {
                          try {
                            const out = await refreshFx.mutateAsync(selected.id);
                            toast[
                              out.fx_state === "converted" ? "success" : "error"
                            ](
                              out.fx_state === "converted"
                                ? `Converted to ${out.policy_currency} ${(
                                    out.amount_converted ?? 0
                                  ).toFixed(2)}`
                                : "Still no exchange rate — enter the value by hand.",
                            );
                          } catch (err) {
                            toast.error(formatError(err));
                          }
                        }}
                      >
                        {refreshFx.isPending ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="size-3.5" />
                        )}
                        Retry exchange rate
                      </Button>
                    )}
                  </DetailField>
                  {remainingLimit != null && (
                    <DetailField label="Remaining limit">
                      <span
                        className={
                          // Both sides in the POLICY currency. This used to
                          // compare `amount_claimed` — a foreign figure — to an
                          // SGD limit and print the limit with the claim's own
                          // currency code, so a USD 500 bill looked like it fit
                          // inside SGD 600 and the label agreed.
                          claimedInPolicyCurrency != null &&
                          claimedInPolicyCurrency > remainingLimit
                            ? "font-medium tabular-nums text-warn"
                            : "tabular-nums"
                        }
                      >
                        {selected.policy_currency} {remainingLimit.toFixed(2)}
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
                  {/* The visit this claim continues — the admission a pre-/post-
                      consult is claimed against, or the first visit of a
                      specialist course. This is the fact the insurer pays the
                      consultation ON, so an assessor needs it in front of them
                      alongside the doctor and the diagnosis it is matched by. */}
                  {selected.related_claim && (
                    <DetailField label="Follows" wide>
                      {[
                        selected.related_claim.provider_name,
                        selected.related_claim.admission_date &&
                        selected.related_claim.discharge_date
                          ? `${fmtDate(selected.related_claim.admission_date)} – ${fmtDate(
                              selected.related_claim.discharge_date,
                            )}`
                          : fmtDate(
                              selected.related_claim.admission_date ??
                                selected.related_claim.incurred_date,
                            ),
                        selected.related_claim.diagnosis,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
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
                        {/* `amount_approved` is ALWAYS the policy currency,
                            never the claim's own — on a foreign claim the two
                            differ by the exchange rate. */}
                        {selected.policy_currency}{" "}
                        {selected.amount_approved.toFixed(2)}
                      </span>
                    </DetailField>
                  )}
                  {selected.decision_notes && (
                    <DetailField label="Decision note" wide>
                      {selected.decision_notes}
                    </DetailField>
                  )}
                </dl>

                {/* Renders nothing before the insurer leg — see the component.
                    Gated on `hasSettlement`, not on the dispatch timestamp: a
                    claim recorded as paid without one still has a payment date
                    and amount to show. */}
                {hasSettlement(selected) && (
                  <DetailSection title="Insurer settlement">
                    <ClaimSettlementFacts claim={selected} />
                  </DetailSection>
                )}

                {/* Correcting what the MEMBER stated — kept apart from
                    Assessment below it, which records facts the broker owns
                    and the member never stated. Two different acts, two
                    different audit actions, and once a claim is settled two
                    different bars. */}
                <DetailSection title="Claim details">
                  {/* Keyed on the CLAIM, not on its revision. Per-revision
                      remounting threw away whatever the assessor had typed
                      every time the claim moved — including on the member's own
                      document uploads. The panel now holds its own baseline and
                      says when the claim has moved past it. */}
                  <ClaimAmendPanel key={selected.id} claim={selected} />
                </DetailSection>

                {/* The assessor's own fields. Directly above Documents because
                    the sector and admission window are read OFF those
                    documents, and every field here is a column on the claims
                    reports that nothing else in the product can fill. */}
                <DetailSection title="Assessment">
                  <ClaimAssessmentPanel claim={selected} />
                </DetailSection>

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
                  {can(selected, "reclassify") ? (
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
              {can(selected, "approve") && (
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
              {/* The same pinned slot carries the settlement step. The two are
                  mutually exclusive by construction — a claim is either awaiting
                  our decision or awaiting the insurer's — so one footer always
                  shows exactly the next thing to do. */}
              {can(selected, "send_to_insurer") && (
                <SheetFooter className="justify-start">
                  <Button onClick={() => setSettling("send")}>
                    Send to insurer
                  </Button>
                </SheetFooter>
              )}
              {can(selected, "record_payment") && (
                <SheetFooter className="justify-start">
                  <Button onClick={() => setSettling("pay")}>
                    Record payment
                  </Button>
                  {/* The insurer declining after WE accepted is a real outcome
                      (`VALID_TRANSITIONS` allows sent_to_insurer → rejected),
                      and it is the only transition that releases the member's
                      limit. Without this control the claim stays in
                      `sent_to_insurer` — a SETTLED status — forever, and the
                      limit stays permanently consumed. */}
                  <Button
                    variant="outline"
                    className="text-error hover:text-error"
                    onClick={() => setDecision("reject")}
                  >
                    Insurer declined
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
                {/* A foreign claim with no rate has no policy-currency value,
                    so there is nothing to approve yet. Asked for HERE rather
                    than sending the assessor to another screen: they are
                    looking at the receipt right now, and the server takes both
                    figures in one request. */}
                {needsConversion && (
                  <label className="block space-y-1.5">
                    <span className="text-xs font-medium text-warn">
                      What is {selected.currency}{" "}
                      {selected.amount_claimed.toFixed(2)} worth in{" "}
                      {selected.policy_currency}? (required)
                    </span>
                    <Input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={convertedAmount}
                      onChange={(e) => {
                        setConvertedAmount(e.target.value);
                        setLimitWarning(null);
                      }}
                      placeholder="0.00"
                      className="tabular-nums"
                    />
                    <span className="block text-xs text-muted-foreground">
                      No exchange rate could be fetched for{" "}
                      {selected.incurred_date}. This is what the claim is worth,
                      not what you are paying — set that below if they differ.
                    </span>
                  </label>
                )}
                <p>
                  The member will see the claim as approved. Leave the amount
                  blank to approve the full{" "}
                  {claimedInPolicyCurrency != null ? (
                    <>
                      {selected.policy_currency}{" "}
                      {claimedInPolicyCurrency.toFixed(2)}
                    </>
                  ) : (
                    "converted value"
                  )}
                  .
                </p>
                <label className="block space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">
                    {/* The currency is IN THE LABEL because on a foreign claim
                        it differs from the one shown beside "Amount claimed".
                        An assessor typing 500 against a USD bill is approving
                        SGD 500, and nothing else on this dialog said so. */}
                    Approved amount in {selected.policy_currency} (optional)
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
                    placeholder={
                      claimedInPolicyCurrency != null
                        ? claimedInPolicyCurrency.toFixed(2)
                        : "0.00"
                    }
                    className="tabular-nums"
                  />
                </label>
              </>
            )}
            {decision === "reject" && (
              <p>This is final — the member cannot resubmit a rejected claim.</p>
            )}
            {decision === "reject" && selected?.status === "sent_to_insurer" && (
              // They were already told the claim was approved, so a bare
              // rejection notice will read as a reversal. The note is the only
              // place that gets explained — say so before it is skipped.
              <p className="rounded-md border border-warn/40 bg-warn-soft px-3 py-2.5 text-warn">
                This member has already been told their claim was approved.
                Explain the insurer&apos;s decision in the note — it is the only
                thing they will see. Their benefit limit is released.
              </p>
            )}
            {decision === "needs_info" && (
              <p>
                The claim reopens for the member to add documents and resubmit. Explain
                what's missing in the note.
              </p>
            )}
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-muted-foreground">
                Note{
                  decision === "needs_info" ||
                  (decision === "reject" && selected?.status === "sent_to_insurer")
                    ? " (required — shown to the member)"
                    : " (optional)"
                }
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
        confirmDisabled={
          (decision === "needs_info" ||
            (decision === "reject" && selected?.status === "sent_to_insurer")) &&
          !note.trim()
        }
        onConfirm={confirmDecision}
      />

      <AlertDialog
        open={settling !== null}
        onOpenChange={(o) => {
          if (!o) setSettling(null);
        }}
        title={
          settling === "send" ? "Send to the insurer?" : "Record the payment"
        }
        tone="info"
        description={
          <div className="space-y-4">
            {settling === "send" && (
              <p>
                The claim moves to awaiting the insurer, and the turnaround
                deadline starts today. The member is not notified — they have
                already been told it was approved.
              </p>
            )}
            {settling === "pay" && selected && (
              <>
                {paymentWarning && (
                  <p className="rounded-md border border-warn/40 bg-warn-soft px-3 py-2.5 text-warn">
                    {paymentWarning} Confirm again to record this exception.
                  </p>
                )}
                <p>
                  The member is told their claim has been paid. Leave the amount
                  blank if the insurer paid the full{" "}
                  {selected.policy_currency}{" "}
                  {(
                    selected.amount_approved ??
                    claimedInPolicyCurrency ??
                    selected.amount_claimed
                  ).toFixed(2)}
                  .
                </p>
                <label className="block space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">
                    Payment date
                  </span>
                  <Input
                    type="date"
                    value={paidOn}
                    onChange={(e) => setPaidOn(e.target.value)}
                  />
                </label>
                <label className="block space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">
                    Amount paid in {selected.policy_currency} (optional)
                  </span>
                  <Input
                    type="number"
                    min="0"
                    step="0.01"
                    value={paidAmount}
                    onChange={(e) => {
                      setPaidAmount(e.target.value);
                      setPaymentWarning(null);
                    }}
                    placeholder={(
                      selected.amount_approved ??
                      claimedInPolicyCurrency ??
                      selected.amount_claimed
                    ).toFixed(2)}
                    className="tabular-nums"
                  />
                </label>
              </>
            )}
          </div>
        }
        confirmLabel={
          settling === "send"
            ? "Send"
            : paymentWarning
              ? "Record exception"
              : "Record payment"
        }
        loading={sendToInsurer.isPending || recordPayment.isPending}
        onConfirm={confirmSettlement}
      />

      {!readOnly && <LogCaseForm
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
      />}

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

// The Claims page: the review queue, the member conversations waiting on a
// reply, and everything that governs both — the per-claim-type AI review rule
// setup and the company claim settings (grace period + document vocabulary,
// moved here from Company settings so the whole claims surface lives in one
// place). Keep the legacy `ai-extraction` tab value so old deep links continue
// to open the review-rule setup.
const CLAIMS_TABS = ["queue", "messages", "ai-extraction", "settings"] as const;
type ClaimsTab = (typeof CLAIMS_TABS)[number];
const isClaimsTab = (v: string | undefined): v is ClaimsTab =>
  CLAIMS_TABS.includes(v as ClaimsTab);

export function ClaimsQueuePage() {
  const navigate = useNavigate();
  const { data: me } = useMe();
  const [reviewRulesImportOpen, setReviewRulesImportOpen] = useState(false);
  const search = useSearch({ strict: false }) as {
    tab?: string;
    claim?: string;
    employee?: string;
  };
  const requestedTab: ClaimsTab = isClaimsTab(search.tab) ? search.tab : "queue";
  const canConfigure = me?.role === "broker_admin" || me?.role === "system_admin";
  const tab: ClaimsTab =
    !canConfigure && ["ai-extraction", "settings"].includes(requestedTab)
      ? "queue"
      : requestedTab;
  const awaiting = useAwaitingReplyCount();

  return (
    <Tabs
      className="flex min-h-full flex-col"
      value={tab}
      onValueChange={(v) =>
        navigate({ to: "/claims/review", search: { tab: v } })
      }
    >
      <PageTabsBar className="flex shrink-0 items-center justify-between gap-3">
        <div className="min-w-0 overflow-x-auto">
          <TabsList className="min-w-max">
            <TabsTrigger value="queue">Queue</TabsTrigger>
          {/* The count is the whole point: with no email in prod, this badge is
              the ONLY signal a broker gets that a member has written. It has to
              be visible from the page, not inside the tab. */}
            <TabsTrigger value="messages">
              Messages
              {awaiting.count > 0 && (
                <Badge variant="warn" className="ml-2">
                  {awaiting.count}
                </Badge>
              )}
              {awaiting.isError && (
                <Badge
                  variant="error"
                  className="ml-2"
                  title="Couldn't check which conversations need a reply"
                  aria-label="Message count unavailable"
                >
                  !
                </Badge>
              )}
            </TabsTrigger>
            {canConfigure && (
              <TabsTrigger value="ai-extraction">Review rules</TabsTrigger>
            )}
            {canConfigure && (
              <TabsTrigger value="settings">Claim settings</TabsTrigger>
            )}
          </TabsList>
        </div>
        {tab === "ai-extraction" && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => setReviewRulesImportOpen(true)}
          >
            <Copy className="size-3.5" />
            Duplicate from another company
          </Button>
        )}
      </PageTabsBar>

      <TabsContent value="queue">
        <QueueTab initialClaimId={search.claim} employeeId={search.employee} />
      </TabsContent>

      <TabsContent value="messages">
        <ConversationQueue />
      </TabsContent>

      {canConfigure && (
        <TabsContent value="ai-extraction">
          <p className="mb-3 text-xs text-muted-foreground">
            Scope: the live benefit year and its configured claim types.
          </p>
          <ReviewRuleSettings />
        </TabsContent>
      )}

      {canConfigure && <TabsContent value="settings" className="space-y-5">
        <Card>
          <CardContent className="flex flex-wrap items-end gap-4 p-5">
            <ClaimGracePeriodField />
            <LeaverAccessField />
          </CardContent>
        </Card>
        <ClaimDocumentSettings />
      </TabsContent>}
      <ImportRulesDialog
        open={reviewRulesImportOpen}
        onOpenChange={setReviewRulesImportOpen}
      />
    </Tabs>
  );
}
