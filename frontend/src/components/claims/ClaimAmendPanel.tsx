import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useAmendClaim, type BrokerClaim } from "@/api/claims";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionLabel } from "@/components/ui/section-label";
import { formatError } from "@/lib/errors";
import { toast } from "sonner";

/**
 * Correcting what the MEMBER stated.
 *
 * A different act from `ClaimAssessmentPanel`, which sits beside it: that one
 * records facts the BROKER owns and the member never stated (sector, admission
 * window, payroll treatment, the internal note). This one rewrites the member's
 * own account of the claim — the figure, the date, the invoice number — and so
 * carries a different audit action and, once the claim is settled, a different
 * bar.
 *
 * Four rules it depends on:
 *
 * - **The PATCH is partial and this form sends only what CHANGED.** Same reason
 *   as the assessment panel: the server applies `model_fields_set`, so an
 *   untouched field must not appear in the body.
 * - **A settled claim demands a reason.** By then the figure has been given to
 *   the member and, on a dispatched claim, to the insurer. The server 422s
 *   (`reason_required`) without one; the field appears here only when it is
 *   actually required, so an ordinary queue correction is not slowed by a box
 *   nobody needs to fill.
 * - **`expected_revision` goes with every save.** A member may be correcting
 *   the same claim from the portal.
 * - **No validation is duplicated here.** The server re-runs the whole submit
 *   chain over the merged claim (`claims.validate_claim_facts`), so a date
 *   outside the policy year or an invoice number already on another live claim
 *   comes back as the same refusal the member would have got. This form
 *   collects and reports; it does not second-guess.
 */

interface Draft {
  incurred_date: string;
  provider_name: string;
  invoice_number: string;
  doctor_name: string;
  diagnosis: string;
  amount_claimed: string;
}

function draftFrom(claim: BrokerClaim): Draft {
  return {
    incurred_date: claim.incurred_date,
    provider_name: claim.provider_name ?? "",
    invoice_number: claim.invoice_number ?? "",
    doctor_name: claim.doctor_name ?? "",
    diagnosis: claim.diagnosis ?? "",
    amount_claimed: String(claim.amount_claimed),
  };
}

/** Statuses where correcting the claim is rewriting settled history rather than
 *  fixing a live record. Mirrors `claims._REASON_REQUIRED_STATUSES` — and if the
 *  two ever drift the server still refuses, so the cost is a 422 the assessor
 *  can act on rather than a correction that slips through unexplained. */
const REASON_REQUIRED = new Set([
  "approved",
  "sent_to_insurer",
  "paid",
  "rejected",
]);

export function ClaimAmendPanel({ claim }: { claim: BrokerClaim }) {
  // Keyed on the revision by the caller, so this captures the CURRENT values
  // and the baseline for the diff exactly once per version of the claim.
  const [original] = useState(() => draftFrom(claim));
  const [draft, setDraft] = useState(original);
  const [reason, setReason] = useState("");
  const amend = useAmendClaim();

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const patch: Record<string, unknown> = {};
  if (draft.incurred_date !== original.incurred_date)
    patch.incurred_date = draft.incurred_date;
  if (draft.provider_name.trim() !== original.provider_name)
    patch.provider_name = draft.provider_name.trim();
  if (draft.invoice_number.trim() !== original.invoice_number)
    patch.invoice_number = draft.invoice_number.trim();
  if (draft.doctor_name.trim() !== original.doctor_name)
    patch.doctor_name = draft.doctor_name.trim() || null;
  if (draft.diagnosis.trim() !== original.diagnosis)
    patch.diagnosis = draft.diagnosis.trim() || null;
  if (draft.amount_claimed !== original.amount_claimed)
    patch.amount_claimed = Number(draft.amount_claimed);

  const dirty = Object.keys(patch).length > 0;
  const needsReason = REASON_REQUIRED.has(claim.status);
  const amountValid = Number(draft.amount_claimed) > 0;
  const canSave = dirty && amountValid && (!needsReason || reason.trim() !== "");

  const save = async () => {
    try {
      await amend.mutateAsync({
        claimId: claim.id,
        patch,
        reason: needsReason ? reason.trim() : undefined,
        expectedRevision: claim.revision,
      });
      setReason("");
      toast.success("Claim corrected");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <section className="space-y-3">
      <SectionLabel>Correct the claim</SectionLabel>
      <p className="text-2xs text-muted-foreground">
        What the member stated. Corrections are recorded on the claim&rsquo;s
        audit trail.
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="amend-date">Date of treatment</Label>
          <Input
            id="amend-date"
            type="date"
            value={draft.incurred_date}
            onChange={(e) => set("incurred_date", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="amend-amount">Amount claimed</Label>
          <Input
            id="amend-amount"
            type="number"
            min="0.01"
            step="0.01"
            value={draft.amount_claimed}
            onChange={(e) => set("amount_claimed", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="amend-provider">Provider</Label>
          <Input
            id="amend-provider"
            value={draft.provider_name}
            maxLength={255}
            onChange={(e) => set("provider_name", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="amend-invoice">Invoice number</Label>
          <Input
            id="amend-invoice"
            value={draft.invoice_number}
            maxLength={128}
            onChange={(e) => set("invoice_number", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="amend-doctor">Doctor seen</Label>
          <Input
            id="amend-doctor"
            value={draft.doctor_name}
            maxLength={255}
            onChange={(e) => set("doctor_name", e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="amend-diagnosis">Diagnosis</Label>
          <Input
            id="amend-diagnosis"
            value={draft.diagnosis}
            maxLength={512}
            onChange={(e) => set("diagnosis", e.target.value)}
          />
        </div>
      </div>

      {/* Only where it is actually required — an ordinary queue correction is
          not worth slowing down with a box nobody needs to fill. */}
      {needsReason && (
        <div className="space-y-1">
          <Label htmlFor="amend-reason">
            Why is this being corrected? (required)
          </Label>
          <Input
            id="amend-reason"
            value={reason}
            maxLength={500}
            placeholder="e.g. Invoice re-read — the total was 55.00, not 550.00"
            onChange={(e) => setReason(e.target.value)}
          />
          <p className="text-2xs text-muted-foreground">
            This claim is already {claim.status.replace(/_/g, " ")}. The member
            has been told the outcome, so the record should say what changed.
          </p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button
          type="button"
          size="sm"
          disabled={!canSave || amend.isPending}
          onClick={() => void save()}
        >
          {amend.isPending && (
            <Loader2 className="mr-1.5 size-3.5 animate-spin" aria-hidden />
          )}
          Save correction
        </Button>
        {/* A disabled button with nothing explaining it reads as broken. */}
        {!dirty && (
          <span className="text-2xs text-muted-foreground">
            Nothing changed yet.
          </span>
        )}
        {dirty && needsReason && reason.trim() === "" && (
          <span className="text-2xs text-muted-foreground">
            Add a reason to save.
          </span>
        )}
      </div>
    </section>
  );
}
