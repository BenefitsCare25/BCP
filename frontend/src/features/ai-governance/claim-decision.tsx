import { useRef, useState, type FormEvent } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  LockKeyhole,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAIReview, type PublicDecision } from "./review-state";
import {
  BackToOversight,
  Field,
  PreviewNotice,
  ReviewGate,
  Status,
  fieldClass,
  panelClass,
} from "./shared";

export function AIClaimDecisionPage() {
  const revision = useAIReview((s) => s.revision);
  return (
    <ReviewGate>
      <ClaimDecision key={revision} />
    </ReviewGate>
  );
}
function ClaimDecision() {
  const {
    publicDecision,
    decisionSupport,
    decisionRecorded,
    recordDecision,
    patch,
  } = useAIReview();
  const [outcome, setOutcome] = useState<PublicDecision["outcome"]>(
    publicDecision.outcome,
  );
  const [amount, setAmount] = useState(publicDecision.amount);
  const [reason, setReason] = useState(publicDecision.reason);
  const [explanation, setExplanation] = useState(publicDecision.explanation);
  const [internal, setInternal] = useState(decisionSupport.internalNote);
  const [reference, setReference] = useState(decisionSupport.reference);
  const [errors, setErrors] = useState<string[]>([]);
  const errorRef = useRef<HTMLDivElement>(null);
  const [showDocument, setShowDocument] = useState(false);
  const cents = /^\d+(\.\d{1,2})?$/.test(amount)
    ? Math.round(Number(amount) * 100)
    : NaN;
  const adverse =
    outcome === "Reject" || (outcome === "Approve" && cents < 15000);
  const override = outcome === "Approve" && cents > 12000;
  const explanationRequired = adverse || outcome === "Request information";
  function submit(e: FormEvent) {
    e.preventDefault();
    const issues: string[] = [];
    if (
      outcome === "Approve" &&
      (!Number.isFinite(cents) || cents <= 0 || cents > 15000)
    )
      issues.push(
        "Enter an approved amount above S$0 and no more than S$150, with up to two decimal places.",
      );
    if (adverse && !reason)
      issues.push("Choose the reason for the reduced or rejected claim.");
    if (explanationRequired && !explanation.trim())
      issues.push("Write the explanation the member will receive.");
    if ((adverse || override) && !reference)
      issues.push("Choose a supporting document or policy reference.");
    if (override && !internal.trim())
      issues.push(
        "Explain why the non-covered-item concern is being overridden. This note stays internal.",
      );
    setErrors(issues);
    if (issues.length) {
      requestAnimationFrame(() => errorRef.current?.focus());
      return;
    }
    if (decisionRecorded) return;
    recordDecision(
      {
        outcome,
        amount: outcome === "Approve" ? (cents / 100).toFixed(2) : "0.00",
        reason: adverse ? reason : "",
        explanation: explanation.trim(),
      },
      { internalNote: internal.trim(), reference },
    );
    toast.success("Decision recorded in this preview. No member was notified.");
  }
  const amountLabel =
    outcome === "Approve" && Number.isFinite(cents)
      ? `S$${(cents / 100).toFixed(2)} approved`
      : outcome === "Reject"
        ? "Claim rejected"
        : outcome === "Request information"
          ? "More information needed"
          : "Enter an approved amount";
  return (
    <div className="mx-auto max-w-screen-2xl">
      <PreviewNotice />
      <BackToOversight />
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">
              Claim DEMO-1042
            </h1>
            <Status value={decisionRecorded ? "Reviewed" : "Action required"} />
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Member A · GP visit · 21 Sep 2026 · Sample claim
          </p>
        </div>
        <Link
          to="/firm/ai-oversight/member-journey"
          className="inline-flex min-h-11 items-center text-sm text-primary"
        >
          View member journey →
        </Link>
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <section className={`${panelClass} p-5 sm:p-6`}>
          <h2 className="text-lg font-semibold">Evidence summary</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Claimed{" "}
            <strong className="font-medium text-foreground">S$150.00</strong> ·
            Policy currency SGD
          </p>
          <div className="my-6 rounded-md border border-border p-4">
            <div className="flex items-center gap-3">
              <FileText
                className="size-5 text-muted-foreground"
                aria-hidden="true"
              />
              <div>
                <h3 className="text-sm font-medium">Receipt</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  GP Clinic · 21 Sep 2026
                </p>
              </div>
            </div>
            <table className="mt-5 w-full text-sm">
              <caption className="sr-only">
                Sample receipt expenses in Singapore dollars
              </caption>
              <thead>
                <tr className="border-b border-border text-xs text-muted-foreground">
                  <th className="py-3 text-left font-medium">Description</th>
                  <th className="py-3 text-right font-medium">Amount (SGD)</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="py-3">Consultation</td>
                  <td className="text-right tabular-nums">120.00</td>
                </tr>
                <tr className="bg-warn-soft/60">
                  <td className="px-2 py-3">Non-covered item</td>
                  <td className="px-2 text-right tabular-nums">30.00</td>
                </tr>
                <tr className="border-t border-border font-medium">
                  <td className="pt-3">Total</td>
                  <td className="pt-3 text-right tabular-nums">150.00</td>
                </tr>
              </tbody>
            </table>
            <Button
              type="button"
              variant="link"
              className="mt-3 px-0"
              onClick={() => setShowDocument(!showDocument)}
              aria-expanded={showDocument}
            >
              {showDocument ? "Hide source details" : "View source details"}
            </Button>
            {showDocument && (
              <div className="mt-3 rounded-md bg-muted p-4 text-xs leading-relaxed">
                <p className="font-semibold">Synthetic receipt · GP-2109</p>
                <p className="mt-2">
                  Consultation S$120.00; non-covered item S$30.00. Paid
                  S$150.00. This is a sample transcription, not a real medical
                  document.
                </p>
              </div>
            )}
          </div>
          <div className="border-t border-border pt-6">
            <h3 className="flex items-center gap-2 font-semibold">
              <Sparkles className="size-4 text-primary" aria-hidden="true" />
              AI review
            </h3>
            <p className="mt-4 flex items-start gap-2 rounded-md bg-warn-soft p-3 text-sm text-warn">
              <AlertTriangle
                className="mt-0.5 size-4 shrink-0"
                aria-hidden="true"
              />
              Check the non-covered item against the policy.
            </p>
            <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
              AI checks support your assessment. They do not approve or reject a
              claim.
            </p>
            <dl className="mt-5 space-y-3 text-xs">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Review reference</dt>
                <dd>DEMO-REVIEW-1042</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Review mode</dt>
                <dd>Sample AI-assisted assessment</dd>
              </div>
            </dl>
          </div>
        </section>
        <section className={`${panelClass} p-5 sm:p-6`}>
          <h2 className="mb-5 text-lg font-semibold">Record decision</h2>
          <form onSubmit={submit} noValidate className="space-y-5">
            {errors.length > 0 && (
              <div
                ref={errorRef}
                tabIndex={-1}
                role="alert"
                className="rounded-md bg-error-soft p-4 text-sm text-error"
              >
                <p className="font-semibold">Check the decision</p>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            )}
            <fieldset disabled={decisionRecorded} className="space-y-5">
              <legend className="sr-only">Claim decision fields</legend>
              <fieldset>
                <legend className="mb-2 text-sm font-medium">Decision</legend>
                <div className="flex flex-wrap gap-2">
                  {(["Approve", "Reject", "Request information"] as const).map(
                    (value) => (
                      <label
                        key={value}
                        className={`flex min-h-11 flex-1 cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm ${outcome === value ? "border-primary bg-accent text-accent-foreground" : "border-input"}`}
                      >
                        <input
                          type="radio"
                          name="decision"
                          value={value}
                          checked={outcome === value}
                          onChange={() => {
                            setOutcome(value);
                            setErrors([]);
                          }}
                          className="accent-primary"
                        />
                        {value}
                      </label>
                    ),
                  )}
                </div>
              </fieldset>
              {outcome === "Approve" && (
                <Field label="Amount approved (SGD)">
                  <Input
                    value={amount}
                    inputMode="decimal"
                    onChange={(e) => {
                      setAmount(e.target.value);
                      setErrors([]);
                    }}
                    aria-required="true"
                  />
                </Field>
              )}
              {outcome === "Approve" && adverse && (
                <p className="rounded-md bg-warn-soft px-3 py-2 text-xs leading-relaxed text-warn">
                  You are approving S${((15000 - cents) / 100).toFixed(2)} less
                  than claimed. Explain the difference to the member.
                </p>
              )}
              {adverse && (
                <Field label="Reason (required)">
                  <select
                    className={fieldClass}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    required
                  >
                    <option value="">Select a reason</option>
                    {[
                      "Policy limit",
                      "Excluded expense",
                      "Eligibility / date issue",
                      "Duplicate claim",
                      "Missing evidence",
                      "Calculation correction",
                      "Insurer decision",
                      "Other",
                    ].map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </Field>
              )}
              <Field
                label={
                  outcome === "Request information"
                    ? "What information does the member need to provide? (required)"
                    : `Explanation to member${explanationRequired ? " (required)" : ""}`
                }
                hint="Visible to the member. Explain the policy or evidence behind the outcome."
              >
                <textarea
                  rows={3}
                  value={explanation}
                  onChange={(e) => setExplanation(e.target.value)}
                  className={fieldClass}
                  aria-required={explanationRequired}
                />
              </Field>
              <Field
                label={`Supporting reference${adverse || override ? " (required)" : ""}`}
              >
                <select
                  className={fieldClass}
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                >
                  <option value="">Select a reference</option>
                  <option>Policy schedule · Excluded expenses</option>
                  <option>Receipt · GP-2109</option>
                  <option>Reviewed policy clarification</option>
                </select>
              </Field>
              <Field
                label={
                  override
                    ? "Reason for overriding the concern (required)"
                    : "Internal assessment note (optional)"
                }
                hint="Broker-only. Never included in the member explanation."
              >
                <textarea
                  rows={3}
                  className={fieldClass}
                  value={internal}
                  onChange={(e) => setInternal(e.target.value)}
                  aria-required={override}
                  placeholder={
                    override
                      ? "Explain the reviewed evidence that resolves the concern."
                      : "Write an internal note…"
                  }
                />
              </Field>
            </fieldset>
            <section
              className="border-t border-border pt-5"
              aria-label="Member-visible decision preview"
            >
              <h3 className="mb-3 text-sm font-semibold">
                What the member will receive
              </h3>
              <div className="rounded-md bg-muted/60 p-4">
                <p className="text-sm font-semibold">{amountLabel}</p>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">
                  {explanation.trim() || "Your explanation will appear here."}
                </p>
              </div>
              <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                <LockKeyhole className="size-3.5" aria-hidden="true" />
                Internal notes are excluded.
              </p>
            </section>
            {decisionRecorded ? (
              <div role="status" className="space-y-3">
                <p className="flex items-start gap-2 text-sm text-good">
                  <CheckCircle2
                    className="mt-0.5 size-4 shrink-0"
                    aria-hidden="true"
                  />
                  Sample decision recorded. No notification was sent.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => patch({ decisionRecorded: false })}
                >
                  Edit sample decision
                </Button>
              </div>
            ) : (
              <div className="flex justify-end">
                <Button type="submit">
                  {outcome === "Approve"
                    ? "Record approval"
                    : outcome === "Reject"
                      ? "Record rejection"
                      : "Request information"}
                </Button>
              </div>
            )}
          </form>
        </section>
      </div>
    </div>
  );
}
