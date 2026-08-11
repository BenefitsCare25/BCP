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
 *   the same claim from the portal — and this panel is NOT remounted when that
 *   happens. It used to be (the caller keyed it on `claim.revision`), which
 *   meant a member's amendment, or their attaching a document, wiped whatever
 *   an assessor had half-typed the moment the list refetched. Instead the
 *   baseline is held here, the assessor's work is kept, and the claim moving
 *   underneath is SAID rather than silently applied.
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

/** Trimmed baseline — see `ClaimEditSheet.draftFrom`. Values are stored
 *  verbatim, so an untrimmed baseline opens this form already dirty and saves a
 *  phantom change nobody made. */
function draftFrom(claim: BrokerClaim): Draft {
  return {
    incurred_date: claim.incurred_date,
    provider_name: (claim.provider_name ?? "").trim(),
    invoice_number: (claim.invoice_number ?? "").trim(),
    doctor_name: (claim.doctor_name ?? "").trim(),
    diagnosis: (claim.diagnosis ?? "").trim(),
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
  // The values this form is a diff AGAINST, and the revision they came from —
  // one piece of state, because they are one fact ("the claim as this assessor
  // last saw it"). Re-based only by this panel's OWN successful save; a change
  // from anywhere else is reported below instead of being applied silently.
  const [base, setBase] = useState(() => ({
    draft: draftFrom(claim),
    revision: claim.revision,
  }));
  const original = base.draft;
  const [draft, setDraft] = useState(original);
  const [reason, setReason] = useState("");
  const amend = useAmendClaim();

  // The claim moved since this form was based — the member corrected it, or
  // attached a document, or another assessor got there first. The save would
  // 409 (`expected_revision`), so the honest thing is to say so before they
  // spend the keystrokes, and to offer the reload as a deliberate act rather
  // than performing it under them.
  const movedUnderUs = claim.revision !== base.revision;
  const rebase = () => {
    const next = draftFrom(claim);
    setBase({ draft: next, revision: claim.revision });
    setDraft(next);
  };

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
      const saved = await amend.mutateAsync({
        claimId: claim.id,
        patch,
        reason: needsReason ? reason.trim() : undefined,
        // The revision this form is BASED on, not the latest one — otherwise
        // the guard would happily send back whatever the member had just
        // changed the claim to, which is the overwrite it exists to refuse.
        expectedRevision: base.revision,
      });
      // Re-base onto what the server stored, so the form settles clean instead
      // of reading as unsaved. The response is authoritative: it carries the
      // normalizations (a trimmed provider, a derived benefit key) and the new
      // revision, which the list invalidation has not necessarily delivered yet.
      setBase({ draft: draftFrom(saved), revision: saved.revision });
      setDraft(draftFrom(saved));
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

      {movedUnderUs && (
        <div className="rounded-md border border-border bg-muted p-3 text-xs">
          <p className="text-foreground">
            This claim changed after you opened it. Saving will be refused until
            you load the current details — anything you have typed here is still
            based on the old ones.
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-2"
            onClick={rebase}
          >
            Load the current details
          </Button>
        </div>
      )}

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
          // Disabled while the base is stale: the server would refuse it, and a
          // notice they can act on beats a toast after the round-trip.
          disabled={!canSave || amend.isPending || movedUnderUs}
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
