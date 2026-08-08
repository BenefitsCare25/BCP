/** The visit itself: when, where, which invoice, how much.
 *
 * One column on a phone (The Whole-Frame Rule) — the old two-up grid never
 * collapsed, so a date input and a hospital name shared ~147px each. */
import { AlertTriangle } from "lucide-react";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { DiagnosisPicker } from "@/components/portal/DiagnosisPicker";
import { FieldGroup } from "@/components/portal/leaf/Field";
import { FALLBACK_CURRENCIES, OTHER_HOSPITAL } from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

export function VisitFields({ form }: { form: NewClaimForm }) {
  const { hospitals, selectedProduct } = form;
  const currencies = form.currencies.length
    ? form.currencies
    : FALLBACK_CURRENCIES;

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Visit date" required error={form.fieldErrors.incurred_date}>
          {(p) => (
            <input
              {...p}
              type="date"
              className={leafControl}
              min={form.claimableFrom}
              max={form.maxIncurred}
              value={form.incurredDate}
              onChange={(e) => form.setIncurredDate(e.target.value)}
            />
          )}
        </Field>

        <Field
          label={form.isHospitalisation ? "Hospital" : "Provider / clinic"}
          required
          error={form.fieldErrors.provider}
        >
          {(p) =>
            form.isHospitalisation ? (
              <div className="space-y-2">
                <select
                  {...p}
                  className={leafControl}
                  value={form.hospital}
                  onChange={(e) => form.setHospital(e.target.value)}
                >
                  <option value="">Select the hospital</option>
                  <optgroup label="Government / Restructured">
                    {hospitals
                      .filter((h) => h.sector === "govt")
                      .map((h) => (
                        <option key={h.name} value={h.name}>
                          {h.name}
                        </option>
                      ))}
                  </optgroup>
                  <optgroup label="Private">
                    {hospitals
                      .filter((h) => h.sector === "private")
                      .map((h) => (
                        <option key={h.name} value={h.name}>
                          {h.name}
                        </option>
                      ))}
                  </optgroup>
                  <option value={OTHER_HOSPITAL}>
                    Other / overseas hospital
                  </option>
                </select>
                {form.hospital === OTHER_HOSPITAL && (
                  <input
                    className={leafControl}
                    aria-label="Hospital name"
                    placeholder="Hospital name"
                    value={form.provider}
                    onChange={(e) => form.setProvider(e.target.value)}
                  />
                )}
              </div>
            ) : (
              <input
                {...p}
                className={leafControl}
                value={form.provider}
                onChange={(e) => form.setProvider(e.target.value)}
              />
            )
          }
        </Field>

        <Field label="Invoice number" required error={form.fieldErrors.invoice}>
          {(p) => (
            <input
              {...p}
              className={leafControl}
              value={form.invoiceNumber}
              maxLength={128}
              onChange={(e) => form.setInvoiceNumber(e.target.value)}
            />
          )}
        </Field>

        {/* Pre-/post-hospitalisation only. The consult is claimed against the
            admission it belongs to, and the doctor's name is how the insurer
            ties the two together — nothing else on the bill identifies the
            episode. Whether to ask is the SERVED `requires_doctor_name`. */}
        {form.requiresDoctorName && (
          <Field
            label="Doctor seen"
            required
            error={form.fieldErrors.doctor_name}
          >
            {(p) => (
              <input
                {...p}
                className={leafControl}
                placeholder="e.g. Dr Tan Wei Ming"
                value={form.doctorName}
                maxLength={255}
                onChange={(e) => form.setDoctorName(e.target.value)}
              />
            )}
          </Field>
        )}

        <Field label="Currency" required>
          {(p) => (
            <select
              {...p}
              className={leafControl}
              value={form.effectiveCurrency}
              disabled={form.effectiveKind === "flex"}
              onChange={(e) => form.setCurrency(e.target.value)}
            >
              {form.effectiveKind === "flex" ? (
                <option value={form.walletCurrency}>
                  {form.walletCurrency}
                </option>
              ) : (
                currencies.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))
              )}
            </select>
          )}
        </Field>

        <Field label="Incurred amount" required error={form.fieldErrors.amount}>
          {(p) => (
            <input
              {...p}
              type="number"
              className={leafControl}
              min="0.01"
              step="0.01"
              placeholder="0.00"
              value={form.amount}
              onChange={(e) => form.setAmount(e.target.value)}
            />
          )}
        </Field>
      </div>

      {/* Wrong-currency guard: bills incurred in Singapore are almost always
          SGD — nudge before the AI review flags a mismatch. */}
      {form.effectiveKind === "insured" && form.effectiveCurrency !== "SGD" && (
        <p className="flex items-start gap-1.5 text-row text-strike-pending">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Double-check the receipt — most Singapore bills are in SGD. Claims in{" "}
          {form.effectiveCurrency} need broker confirmation of the conversion.
        </p>
      )}

      {/* Diagnosis — searchable catalog scoped to the claim type. */}
      {form.showDiagnosisPicker && selectedProduct && (
        <FieldGroup
          label={
            selectedProduct.diagnosis_required ? "Diagnosis (required)" : "Diagnosis"
          }
          error={form.fieldErrors.diagnosis}
        >
          <DiagnosisPicker
            // Remount on product change so the internal search text/open state
            // can't carry over to a different diagnosis group.
            key={selectedProduct.product_code}
            productCode={selectedProduct.product_code}
            value={form.diagnosis}
            onChange={form.setDiagnosis}
          />
        </FieldGroup>
      )}
    </>
  );
}
